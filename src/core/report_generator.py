"""Generador de expedientes técnicos reproducibles con publicación atómica por generación."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union
from uuid import uuid4

from src.config import PROJECT_ROOT, settings
from src.core.dossier_generator import generate_candidate_dossier
from src.core.permits_validator import validate_anomaly_permits
from src.core.vlm_judge import evaluate_candidate_vlm_verdict
from src.schemas import (
    CadastreMetadata,
    CaseRecord,
    EnforcementReportResult,
    ExtractionAnomalyFeature,
    PermitValidationResult,
    VLMFluvialVerdict,
    coerce_anomaly_feature,
)

REPORT_SCHEMA_VERSION = "phase3.1.v1"

_INSTITUTIONAL_DISCLAIMER = (
    "Expediente técnico preliminar generado por RíoAlerta-MCP para apoyo a fiscalización territorial. "
    "Ninguna detección satelital ni dictamen auxiliar automatizado constituye imputación jurídica ni prueba "
    "definitiva de actividad no autorizada; toda actuación requiere verificación documental catastral, "
    "evaluación pericial humana e inspección en terreno por autoridad competente."
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_file_token(token: str) -> str:
    """Normaliza un identificador de anomalía para usarlo como nombre seguro de directorio."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", token.strip())
    return cleaned or "SIN_ID"


@lru_cache(maxsize=1)
def get_software_commit_sha() -> str:
    """Resuelve el SHA del commit actual de RíoAlerta para trazabilidad de reproducibilidad."""
    env_sha = os.environ.get("RIO_ALERTA_GIT_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3.0,
        ).strip()
        return out or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_and_fsync(path: Path, content: str) -> int:
    """Escribe contenido UTF-8 en disco, fuerza fsync y retorna bytes escritos."""
    data = content.encode("utf-8")
    with path.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return len(data)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid4().hex[:6]}")
    try:
        _write_and_fsync(tmp_path, json.dumps(payload, indent=2, ensure_ascii=False))
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _collect_input_hashes(
    permits_file: Optional[Path],
    map_artifacts: Optional[Sequence[str]],
    vlm_verdict: VLMFluvialVerdict,
    detection_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Calcula hashes SHA-256 de los insumos locales disponibles para reproducibilidad."""
    hashes: Dict[str, Dict[str, Any]] = {}

    if permits_file is not None and permits_file.is_file():
        hashes[f"permits:{permits_file}"] = {
            "role": "permits_cadastre",
            "path": str(permits_file),
            "sha256": _sha256_file(permits_file),
            "bytes": permits_file.stat().st_size,
        }

    for ref, img_hash in (vlm_verdict.image_hashes or {}).items():
        hashes[f"evaluation_image:{ref}"] = {
            "role": "evaluation_image",
            "path": ref,
            "sha256": img_hash,
        }

    if map_artifacts:
        for art in map_artifacts:
            art_path = Path(art)
            if art_path.is_file():
                hashes[f"map_artifact:{art}"] = {
                    "role": "map_artifact",
                    "path": str(art_path),
                    "sha256": _sha256_file(art_path),
                    "bytes": art_path.stat().st_size,
                }

    if detection_metadata:
        for key in ("t0_raster_path", "t1_raster_path", "t0_swir_raster_path", "t1_swir_raster_path"):
            val = detection_metadata.get(key)
            if val:
                rpath = Path(str(val))
                if rpath.is_file():
                    hashes[f"{key}:{rpath}"] = {
                        "role": key,
                        "path": str(rpath),
                        "sha256": _sha256_file(rpath),
                        "bytes": rpath.stat().st_size,
                    }

    return hashes


def generate_enforcement_report(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
    t0_scene_id: str,
    t0_date: str,
    t1_scene_id: str,
    t1_date: str,
    permit_validation: Optional[PermitValidationResult] = None,
    vlm_verdict: Optional[VLMFluvialVerdict] = None,
    case_record: Optional[CaseRecord] = None,
    multitemporal_series: Optional[Sequence[Dict[str, Any]]] = None,
    study_area_name: str = "Tramo Fluvial Monitoreado",
    output_dir: Optional[Union[str, Path]] = None,
    strict_hydrometric_validation: bool = False,
    detection_metadata: Optional[Dict[str, Any]] = None,
    map_artifacts: Optional[Sequence[str]] = None,
    figure_captions: Optional[Dict[str, str]] = None,
    permits_path: Optional[Union[str, Path]] = None,
    cadastre_metadata: Optional[CadastreMetadata] = None,
) -> EnforcementReportResult:
    """Emite una generación atómica completa (`report.md`, `report.json`, `manifest.json`).

    Estructura de publicación por candidato y generación:
      `<output_dir>/<anomaly_id>/generations/<report_generation_id>/{report.md, report.json, manifest.json}`
      `<output_dir>/<anomaly_id>/current_generation.json`
    """
    feat = coerce_anomaly_feature(anomaly)
    queried_at_utc = _utc_now_iso()
    permits_file = Path(permits_path) if permits_path is not None else settings.permits_gpkg_path

    resolved_permits = (
        permit_validation
        if permit_validation is not None
        else validate_anomaly_permits(
            anomaly=feat,
            t1_date=t1_date,
            permits_path=permits_path,
            cadastre_metadata=cadastre_metadata,
        )
    )
    resolved_vlm = (
        vlm_verdict
        if vlm_verdict is not None
        else evaluate_candidate_vlm_verdict(anomaly=feat, permit_result=resolved_permits)
    )

    out_base = Path(output_dir) if output_dir is not None else settings.reports_dir
    safe_id = _sanitize_file_token(feat.id)
    candidate_dir = out_base / safe_id
    generations_dir = candidate_dir / "generations"
    generations_dir.mkdir(parents=True, exist_ok=True)

    generation_id = f"GEN-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    staging_dir = generations_dir / f".staging_{generation_id}"
    final_gen_dir = generations_dir / generation_id
    software_sha = get_software_commit_sha()

    feature_dict: Dict[str, Any] = {
        "type": "Feature",
        "id": feat.id,
        "geometry": feat.geometry_geojson,
        "properties": feat.model_dump(exclude={"geometry_geojson"}),
    }

    calib_params = settings.get_calibration_parameters()
    effective_validation_params = {
        "excess_tolerance_pct": settings.excess_tolerance_pct,
        "min_anomaly_area_m2": settings.min_anomaly_area_m2,
        "min_clearing_area_m2": settings.min_clearing_area_m2,
    }

    det_meta: Dict[str, Any] = {
        "crs_metric_epsg": feat.crs_epsg,
        "grid_resolution_m": 10.0,
        "effective_swir_resolution_m": feat.effective_resolution_m,
        "min_anomaly_area_m2": settings.min_anomaly_area_m2,
        "min_clearing_area_m2": settings.min_clearing_area_m2,
        "baseline_scenes_count": (
            feat.uncertainty.persistence_scenes_count if feat.uncertainty is not None else 1
        ),
        "calibration_parameters": calib_params,
        "report_generation_id": generation_id,
        "software_commit_sha": software_sha,
        "institutional_note": _INSTITUTIONAL_DISCLAIMER,
    }
    if detection_metadata:
        det_meta.update(detection_metadata)

    input_hashes = _collect_input_hashes(
        permits_file=permits_file,
        map_artifacts=map_artifacts,
        vlm_verdict=resolved_vlm,
        detection_metadata=det_meta,
    )

    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        md_content = generate_candidate_dossier(
            feature=feature_dict,
            detection_metadata=det_meta,
            aoi_name=study_area_name,
            period_label=f"{t0_date} -> {t1_date}",
            multitemporal_series=multitemporal_series,
            map_artifacts=map_artifacts,
            figure_captions=figure_captions,
            strict_hydrometric_consistency=strict_hydrometric_validation,
            permit_validation=resolved_permits,
            vlm_verdict=resolved_vlm,
            case_record=case_record,
        )

        staging_md = staging_dir / "report.md"
        md_bytes = _write_and_fsync(staging_md, md_content)
        md_sha256 = hashlib.sha256(md_content.encode("utf-8")).hexdigest()

        json_payload: Dict[str, Any] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "report_generation_id": generation_id,
            "generated_at_utc": queried_at_utc,
            "software_commit_sha": software_sha,
            "markdown_sha256": md_sha256,
            "study_area_name": study_area_name,
            "scenes": {
                "t0_scene_id": t0_scene_id,
                "t0_date": t0_date,
                "t1_scene_id": t1_scene_id,
                "t1_date": t1_date,
            },
            "inputs_provenance": {
                "multitemporal_series": list(multitemporal_series) if multitemporal_series else [],
                "detection_metadata": det_meta,
                "map_artifacts": list(map_artifacts) if map_artifacts else [],
                "figure_captions": dict(figure_captions) if figure_captions else {},
                "cadastre_metadata": (
                    resolved_permits.cadastre_metadata.model_dump()
                    if resolved_permits.cadastre_metadata is not None
                    else None
                ),
                "source_consulted_at_utc": queried_at_utc,
                "input_hashes": input_hashes,
            },
            "evaluation_provenance": {
                "evaluation_mode": resolved_vlm.evaluation_mode,
                "backend": resolved_vlm.backend,
                "model_id": resolved_vlm.model_id,
                "model_version": resolved_vlm.model_version,
                "prompt_version": resolved_vlm.prompt_version,
                "evaluator_id": resolved_vlm.evaluator_id,
                "evaluated_at_utc": resolved_vlm.evaluated_at_utc,
                "image_refs": resolved_vlm.image_refs,
                "image_hashes": resolved_vlm.image_hashes,
                "is_statistical_probability": resolved_vlm.is_statistical_probability,
                "expert_declaration_recorded": resolved_vlm.expert_declaration_recorded,
            },
            "anomaly": feat.model_dump(),
            "permit_validation": resolved_permits.model_dump(),
            "vlm_verdict": resolved_vlm.model_dump(),
            "case_record": case_record.model_dump() if case_record is not None else None,
            "methodology_parameters": det_meta.get("calibration_parameters", calib_params),
            "validation_parameters": effective_validation_params,
            "human_review_required": True,
            "disclaimer": _INSTITUTIONAL_DISCLAIMER,
        }
        json_text = json.dumps(json_payload, indent=2, ensure_ascii=False)
        staging_json = staging_dir / "report.json"
        json_bytes = _write_and_fsync(staging_json, json_text)
        json_sha256 = hashlib.sha256(json_text.encode("utf-8")).hexdigest()

        manifest_payload: Dict[str, Any] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "report_generation_id": generation_id,
            "anomaly_id": feat.id,
            "generated_at_utc": queried_at_utc,
            "software_commit_sha": software_sha,
            "artifacts": {
                "report.md": {"sha256": md_sha256, "bytes": md_bytes},
                "report.json": {"sha256": json_sha256, "bytes": json_bytes},
            },
            "input_hashes": input_hashes,
        }
        staging_manifest = staging_dir / "manifest.json"
        _write_and_fsync(staging_manifest, json.dumps(manifest_payload, indent=2, ensure_ascii=False))

        if md_bytes == 0 or json_bytes == 0 or not staging_manifest.is_file():
            raise RuntimeError("Validación de staging fallida: artefactos vacíos o incompletos.")

        # Publicación atómica del conjunto de artefactos como una sola unidad de directorio
        os.replace(staging_dir, final_gen_dir)

        pointer_payload = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "anomaly_id": feat.id,
            "active_generation_id": generation_id,
            "generation_dir": str(final_gen_dir),
            "report_md_path": str(final_gen_dir / "report.md"),
            "report_json_path": str(final_gen_dir / "report.json"),
            "manifest_json_path": str(final_gen_dir / "manifest.json"),
            "published_at_utc": queried_at_utc,
        }
        _atomic_write_json(candidate_dir / "current_generation.json", pointer_payload)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return EnforcementReportResult(
        status="success",
        anomaly_id=feat.id,
        report_md_path=str(final_gen_dir / "report.md"),
        report_json_path=str(final_gen_dir / "report.json"),
        report_generation_id=generation_id,
        generation_dir_path=str(final_gen_dir),
        manifest_json_path=str(final_gen_dir / "manifest.json"),
        markdown_sha256=md_sha256,
        report_json_sha256=json_sha256,
        software_commit_sha=software_sha,
        classification=resolved_permits.classification,
        priority=resolved_permits.priority,
        area_m2=feat.area_m2,
        area_ha=feat.area_ha,
        crs_epsg=feat.crs_epsg,
        centroid_posgar=feat.centroid_posgar,
        centroid_wgs84=feat.centroid_wgs84,
        t0_scene_id=t0_scene_id,
        t0_date=t0_date,
        t1_scene_id=t1_scene_id,
        t1_date=t1_date,
    )


def verify_report_generation_integrity(generation_dir_or_artifact: Union[str, Path]) -> Dict[str, Any]:
    """Verifica que `report.md`, `report.json` y `manifest.json` pertenezcan a la misma generación."""
    target = Path(generation_dir_or_artifact)
    gen_dir = target if target.is_dir() else target.parent
    md_path = gen_dir / "report.md"
    json_path = gen_dir / "report.json"
    manifest_path = gen_dir / "manifest.json"

    for req in (md_path, json_path, manifest_path):
        if not req.is_file():
            raise FileNotFoundError(f"Artefacto faltante en generación '{gen_dir}': '{req.name}'.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report_data = json.loads(json_path.read_text(encoding="utf-8"))
    md_sha = _sha256_file(md_path)
    json_sha = _sha256_file(json_path)

    gen_id_manifest = manifest.get("report_generation_id")
    gen_id_json = report_data.get("report_generation_id")
    if not gen_id_manifest or gen_id_manifest != gen_id_json:
        raise ValueError(
            f"Desincronización de report_generation_id entre manifest ('{gen_id_manifest}') y report.json ('{gen_id_json}')."
        )

    expected_md_sha = manifest.get("artifacts", {}).get("report.md", {}).get("sha256")
    expected_json_sha = manifest.get("artifacts", {}).get("report.json", {}).get("sha256")
    if md_sha != expected_md_sha or md_sha != report_data.get("markdown_sha256"):
        raise ValueError(
            f"Hash de report.md adulterado o desincronizado (actual={md_sha}, manifest={expected_md_sha})."
        )
    if json_sha != expected_json_sha:
        raise ValueError(
            f"Hash de report.json adulterado o desincronizado (actual={json_sha}, manifest={expected_json_sha})."
        )

    return {
        "valid": True,
        "report_generation_id": gen_id_manifest,
        "markdown_sha256": md_sha,
        "report_json_sha256": json_sha,
        "software_commit_sha": manifest.get("software_commit_sha"),
    }
