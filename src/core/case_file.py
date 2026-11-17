"""Ficha de caso versionada del Protocolo de Validación Independiente (Fase 3.2, P2).

Materializa el esquema canónico del Apéndice B del protocolo
(`docs/validation/PROTOCOL_INDEPENDENT_VALIDATION.md`) como artefacto JSON persistente,
escrito de forma atómica y trazable por caso de investigación.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union
from uuid import uuid4

from src.config import settings
from src.schemas import coerce_anomaly_feature

if TYPE_CHECKING:
    from src.core.case_workflow import CaseEventRecord, CaseRecord

CASE_FILE_SCHEMA_VERSION = "phase3.2.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Escritura atómica (tmp + fsync + os.replace) idéntica al patrón del report generator."""
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid4().hex[:6]}")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def build_case_file(
    anomaly: Union[Any, Dict[str, Any]],
    case_record: Optional["CaseRecord"] = None,
    events: Optional[Sequence["CaseEventRecord"]] = None,
    scenes: Optional[Sequence[Dict[str, Any]]] = None,
    hypotheses: Optional[Sequence[Dict[str, str]]] = None,
    review: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    detection_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construye la ficha de caso con el esquema canónico del protocolo (Apéndice B).

    Los campos de revisión humana y verificación externa quedan como `pendiente` hasta que la
    institución los complete; la ficha nunca los fabrica.

    Args:
        anomaly: `ExtractionAnomalyFeature`, dict plano o Feature GeoJSON del candidato.
        case_record: Registro del expediente vinculado (workflow SQLite), si existe.
        events: Bitácora de eventos del expediente, si existe.
        scenes: Lista de escenas con `scene_id`, `date`, `proveedor`, `rol` y hashes opcionales.
        hypotheses: Hipótesis alternativas con `id`, `descripcion`, `estado`, `fundamento`.
        review: Datos de revisión humana independiente (ciego, evaluator_id, etiqueta pre-desvelado).
        verification: Resultado de verificación (`nivel_evidencia`, `etiqueta_referencia`, `limitaciones`).
        detection_metadata: Metadata de detección de la corrida congelada de origen.

    Returns:
        Diccionario con el esquema `CASE_FILE_SCHEMA_VERSION`.
    """
    feat = coerce_anomaly_feature(anomaly)
    unc = feat.uncertainty

    scenes_out: List[Dict[str, Any]] = []
    for scene in scenes or []:
        scenes_out.append(
            {
                "scene_id": str(scene.get("scene_id", "")),
                "fecha": str(scene.get("date", scene.get("fecha", ""))),
                "proveedor": str(scene.get("proveedor", scene.get("provider", ""))),
                "rol": str(scene.get("rol", scene.get("role", "intermedia"))),
                "sha256_rasters": dict(scene.get("sha256_rasters", {})),
            }
        )

    hypotheses_out: List[Dict[str, Any]] = []
    for hyp in hypotheses or []:
        hypotheses_out.append(
            {
                "id": str(hyp.get("id", "")),
                "descripcion": str(hyp.get("descripcion", "")),
                "estado": str(hyp.get("estado", "PLAUSIBLE_NO_CONFIRMADA")),
                "fundamento": str(hyp.get("fundamento", "")),
            }
        )

    review_payload: Dict[str, Any] = {
        "ciego_aplicado": None,
        "evaluator_id": None,
        "fecha_revision": None,
        "etiqueta_pre_desvelado": None,
        "concordancias_discrepancias": None,
    }
    review_payload.update(review or {})

    verification_payload: Dict[str, Any] = {
        "nivel_evidencia": None,
        "etiqueta_referencia": None,
        "limitaciones": [],
    }
    verification_payload.update(verification or {})

    scl_domain = {
        "interior_t1_10m_counts": (feat.spectral_stats.t1_scl_counts if feat.spectral_stats else {}),
        "buffer": {
            "scope": "adyacencia_buffer_40m",
            "adjacent": bool(unc.cloud_shadow_adjacent) if unc else False,
            "source_dates": list(unc.cloud_shadow_source_dates) if unc else [],
        },
    }

    calib = dict((detection_metadata or {}).get("calibration_parameters") or {})
    payload: Dict[str, Any] = {
        "ficha_version": CASE_FILE_SCHEMA_VERSION,
        "identificador": {
            "candidate_uid": feat.candidate_uid,
            "coarse_location_uid": feat.coarse_location_uid,
            "anomaly_id_corrida": feat.id,
            "caso_id": case_record.case_id if case_record is not None else None,
        },
        "geometria": {
            "poligono_wgs84": feat.geometry_geojson,
            "crs_metrico": feat.crs_epsg,
            "area_m2": feat.area_m2,
            "centroide_wgs84": list(feat.centroid_wgs84),
            "centroide_posgar": list(feat.centroid_posgar),
        },
        "deteccion": {
            "anomaly_type_hipotesis": feat.anomaly_type,
            "signal_score": feat.confidence,
            "es_probabilidad_estadistica": False,
            "zona_geomorfologica_heuristica": feat.geomorphological_zone,
            "deltas": {
                "mndwi": feat.delta_mndwi_mean,
                "bsi": feat.delta_bsi_mean,
                "ndvi": feat.delta_ndvi_mean,
            },
            "dominio_scl": scl_domain,
        },
        "escenas_y_fuentes": scenes_out,
        "evidencia_visual": {"chips": []},
        "hipotesis_alternativas": hypotheses_out
        or [
            {
                "id": "H1",
                "descripcion": "Extracción o acopio de áridos",
                "estado": "PLAUSIBLE_NO_CONFIRMADA",
                "fundamento": "Pendiente de ficha completa (protocolo §4.1).",
            },
            {
                "id": "H3",
                "descripcion": "Dinámica fluvial / erosión / inundación",
                "estado": "PLAUSIBLE_NO_CONFIRMADA",
                "fundamento": "Pendiente de ficha completa (protocolo §4.1).",
            },
        ],
        "revision_humana_independiente": review_payload,
        "resultado_verificacion": verification_payload,
        "workflow": {
            "estado": case_record.status if case_record is not None else "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
            "prioridad": case_record.priority if case_record is not None else None,
            "eventos_count": case_record.events_count if case_record is not None else 0,
            "eventos": [e.event_id for e in (events or [])],
            "human_review_required": True,
        },
        "limitaciones": list(unc.limitations) if unc else [],
        "provenance": {
            "protocolo": "docs/validation/PROTOCOL_INDEPENDENT_VALIDATION.md",
            "calibracion_detector": calib,
            "detector_committed": bool(calib),
            "generado_at_utc": _utc_now_iso(),
        },
    }
    return payload


def write_case_file(
    payload: Dict[str, Any],
    output_dir: Union[str, Path],
    anomaly_id: str,
) -> Path:
    """Escribe la ficha de forma atómica en `<output_dir>/<anomaly_id>/case_file.json`."""
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in anomaly_id.strip()) or "SIN_ID"
    out_path = Path(output_dir) / safe_id / "case_file.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(out_path, payload)
    return out_path
