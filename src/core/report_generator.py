"""Generador de expedientes técnicos reproducibles (Markdown + JSON) en POSGAR 2007."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from src.config import settings
from src.core.dossier_generator import generate_candidate_dossier, write_dossier_markdown
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

_INSTITUTIONAL_DISCLAIMER = (
    "Expediente técnico preliminar generado por RíoAlerta-MCP para apoyo a fiscalización territorial. "
    "Ninguna detección satelital ni dictamen auxiliar automatizado constituye imputación jurídica ni prueba "
    "definitiva de actividad no autorizada; toda actuación requiere verificación documental catastral, "
    "evaluación pericial humana e inspección en terreno por autoridad competente."
)


def _sanitize_file_token(token: str) -> str:
    """Normaliza un identificador de anomalía para usarlo como nombre seguro de archivo."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", token.strip())
    return cleaned or "SIN_ID"


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
    """Emite el acta técnica reproducible en Markdown (9 secciones) y JSON estructurado."""
    feat = coerce_anomaly_feature(anomaly)
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
    out_base.mkdir(parents=True, exist_ok=True)
    safe_id = _sanitize_file_token(feat.id)
    md_path = out_base / f"report_{safe_id}.md"
    json_path = out_base / f"report_{safe_id}.json"

    feature_dict: Dict[str, Any] = {
        "type": "Feature",
        "id": feat.id,
        "geometry": feat.geometry_geojson,
        "properties": feat.model_dump(exclude={"geometry_geojson"}),
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
        "calibration_parameters": settings.get_calibration_parameters(),
        "institutional_note": _INSTITUTIONAL_DISCLAIMER,
    }
    if detection_metadata:
        det_meta.update(detection_metadata)

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
    write_dossier_markdown(md_content, md_path)

    payload: Dict[str, Any] = {
        "schema_version": "phase3.v1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "study_area_name": study_area_name,
        "scenes": {
            "t0_scene_id": t0_scene_id,
            "t0_date": t0_date,
            "t1_scene_id": t1_scene_id,
            "t1_date": t1_date,
        },
        "anomaly": feat.model_dump(),
        "permit_validation": resolved_permits.model_dump(),
        "vlm_verdict": resolved_vlm.model_dump(),
        "case_record": case_record.model_dump() if case_record is not None else None,
        "methodology_parameters": det_meta.get("calibration_parameters", settings.get_calibration_parameters()),
        "human_review_required": True,
        "disclaimer": _INSTITUTIONAL_DISCLAIMER,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return EnforcementReportResult(
        status="success",
        anomaly_id=feat.id,
        report_md_path=str(md_path),
        report_json_path=str(json_path),
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
