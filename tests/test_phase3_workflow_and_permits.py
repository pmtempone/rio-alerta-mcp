"""Pruebas integrales de Fase 3: validación catastral, dictamen VLM, casos JSONL/SQLite y tools MCP."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from src.core.case_workflow import CaseWorkflowManager
from src.core.crs_manager import reproject_geometry
from src.core.permits_validator import validate_anomaly_permits
from src.core.report_generator import generate_enforcement_report
from src.core.vlm_judge import build_vlm_fluvial_prompt, evaluate_candidate_vlm_verdict
from src.schemas import (
    CadastreMetadata,
    EnforcementReportResult,
    ExtractionAnomalyFeature,
    PermitValidationResult,
    UncertaintyMetadata,
    VLMFluvialVerdict,
)
from src.server import (
    tool_evaluate_vlm_verdict,
    tool_generate_enforcement_report,
    tool_manage_case_workflow,
    tool_validate_permits,
)


def _make_anomaly(
    anomaly_id: str = "RIO-F3-001",
    minx: float = 5445000.0,
    miny: float = 6498000.0,
    width_m: float = 50.0,
    height_m: float = 60.0,
    confidence: float = 0.82,
    confounder_score: float = 0.20,
    valid_pixel_fraction: float = 0.96,
    cloud_shadow_adjacent: bool = False,
    zone: str = "RIBERA_FIRME_Y_TERRAZA",
    anomaly_type: str = "desmonte_ribera_acopio",
    candidate_uid: str | None = "CAND-TEST-F3-001",
) -> ExtractionAnomalyFeature:
    """Construye una anomalía sintética en POSGAR 2007 Faja 5 con geometría exacta en WGS84."""
    poly_posgar = box(minx, miny, minx + width_m, miny + height_m)
    geom_wgs84 = reproject_geometry(
        {
            "type": "Polygon",
            "coordinates": [list(poly_posgar.exterior.coords)],
        },
        from_crs="EPSG:5347",
        to_crs="EPSG:4326",
    )
    centroid_geom = reproject_geometry(
        {"type": "Point", "coordinates": [minx + width_m / 2.0, miny + height_m / 2.0]},
        from_crs="EPSG:5347",
        to_crs="EPSG:4326",
    )
    lon_c, lat_c = centroid_geom["coordinates"]
    area_m2 = round(width_m * height_m, 2)
    return ExtractionAnomalyFeature(
        id=anomaly_id,
        candidate_uid=candidate_uid,
        coarse_location_uid="5445000_6498000_desmonte",
        anomaly_type=anomaly_type,  # type: ignore[arg-type]
        confidence=confidence,
        signal_score=confidence,
        area_m2=area_m2,
        area_ha=round(area_m2 / 10000.0, 4),
        rectangularity=0.92,
        elongation=max(width_m, height_m) / min(width_m, height_m),
        delta_mndwi_mean=-0.08,
        delta_bsi_mean=0.31,
        delta_ndvi_mean=-0.42,
        bbox_posgar=(minx, miny, minx + width_m, miny + height_m),
        centroid_posgar=(minx + width_m / 2.0, miny + height_m / 2.0),
        centroid_wgs84=(round(float(lon_c), 6), round(float(lat_c), 6)),
        crs_epsg=5347,
        geometry_geojson=geom_wgs84,
        effective_resolution_m=20.0,
        natural_filter_status="CANDIDATO_RESIDUAL",
        geomorphological_zone=zone,  # type: ignore[arg-type]
        distance_to_water_t0_m=180.0,
        uncertainty=UncertaintyMetadata(
            effective_resolution_m=20.0,
            valid_pixel_fraction=valid_pixel_fraction,
            cloud_shadow_adjacent=cloud_shadow_adjacent,
            water_stage_delta_pct=6.5,
            natural_dynamics_confounder_score=confounder_score,
            persistence_scenes_count=3,
            limitations=["Prueba controlada sintética en POSGAR EPSG:5347."],
        ),
    )


def test_permits_validator_no_cadastre_file(tmp_path: Path) -> None:
    """Cuando falta el GeoPackage/GeoJSON de concesiones se clasifica SIN_CATASTRO_OFICIAL_CARGADO."""
    anomaly = _make_anomaly(width_m=80.0, height_m=70.0)  # 5600 m2
    missing_gpkg = tmp_path / "permits_inexistente.gpkg"

    res = validate_anomaly_permits(
        anomaly=anomaly,
        t1_date="2026-11-08",
        permits_path=missing_gpkg,
    )

    assert isinstance(res, PermitValidationResult)
    assert res.classification == "SIN_CATASTRO_OFICIAL_CARGADO"
    assert res.priority == "ALTA_FISCALIZACION_INMEDIATA"
    assert res.human_review_required is True
    assert res.excess_pct == 100.0
    assert res.cadastre_metadata is not None
    assert res.cadastre_metadata.cadastre_status == "NO_DISPONIBLE"
    assert "NO constituye prueba" in res.observations


def test_permits_validator_authorized_excess_and_expired(tmp_path: Path) -> None:
    """Verifica cruce espacial/temporal completo: autorizado, exceso, vencido, sin coincidencia e hidrografía."""
    permits_geojson = tmp_path / "permits_test.geojson"
    # Faja 5 (EPSG:5347)
    permits_gdf = gpd.GeoDataFrame(
        [
            {
                "permit_id": "CONC-ACTIVA-01",
                "titular": "Arenera Litoral S.A.",
                "expediente": "EXP-2025-110",
                "fecha_desde": "2024-01-01",
                "fecha_hasta": "2027-12-31",
                "permitted_activity": "cantera aluvial autorizada",
                "geometry": box(5445000.0, 6498000.0, 5445100.0, 6498100.0),  # 100x100 m
            },
            {
                "permit_id": "CONC-VENCIDA-02",
                "titular": "Ex Concesionaria Sur",
                "expediente": "EXP-2020-044",
                "fecha_desde": "2020-01-01",
                "fecha_hasta": "2025-06-30",
                "permitted_activity": "acopio ribereño",
                "geometry": box(5446000.0, 6498000.0, 5446100.0, 6498100.0),
            },
        ],
        crs="EPSG:5347",
    )
    permits_gdf.to_file(permits_geojson, driver="GeoJSON")

    water_gdf = gpd.GeoDataFrame(
        [{"nombre": "Río Paraná - Brazo Principal", "geometry": LineString([(5444950.0, 6498000.0), (5444950.0, 6498200.0)])}],
        crs="EPSG:5347",
    )

    # 1) Cubierta 100% por permiso activo -> EXTRACCION_AUTORIZADA
    anom_auth = _make_anomaly(anomaly_id="AUTH-1", minx=5445010.0, miny=6498010.0, width_m=50.0, height_m=50.0)
    res_auth = validate_anomaly_permits(
        anomaly=anom_auth,
        t1_date="2026-11-08",
        permits_path=permits_geojson,
        cadastre_metadata=CadastreMetadata(cadastre_status="OFICIAL_COMPLETO", jurisdiction="Entre Ríos"),
        watercourses_gdf=water_gdf,
    )
    assert res_auth.classification == "EXTRACCION_AUTORIZADA"
    assert res_auth.priority == "BAJA_ARCHIVO"
    assert res_auth.excess_area_m2 == pytest.approx(0.0, abs=1.0)
    assert res_auth.watercourse_name == "Río Paraná - Brazo Principal"
    assert res_auth.distance_to_water_m == pytest.approx(60.0, abs=1.0)

    # 2) Mitad dentro (50x50=2500 m2) y mitad fuera (50x50=2500 m2) -> EXCESO_DE_POLIGONO (50% > 10%)
    anom_excess = _make_anomaly(anomaly_id="EXC-1", minx=5445050.0, miny=6498010.0, width_m=100.0, height_m=50.0)
    res_excess = validate_anomaly_permits(
        anomaly=anom_excess,
        t1_date="2026-11-08",
        permits_path=permits_geojson,
        cadastre_metadata=CadastreMetadata(cadastre_status="OFICIAL_COMPLETO", jurisdiction="Entre Ríos"),
        excess_tolerance_pct=10.0,
    )
    assert res_excess.classification == "EXCESO_DE_POLIGONO"
    assert res_excess.priority == "ALTA_FISCALIZACION_INMEDIATA"
    assert res_excess.excess_pct == pytest.approx(50.0, abs=0.5)
    assert res_excess.excess_area_m2 == pytest.approx(2500.0, abs=25.0)

    # 3) Sobre permiso vencido antes de T1 -> PERMISO_VENCIDO
    anom_exp = _make_anomaly(anomaly_id="VENC-1", minx=5446010.0, miny=6498010.0, width_m=50.0, height_m=50.0)
    res_exp = validate_anomaly_permits(
        anomaly=anom_exp,
        t1_date="2026-11-08",
        permits_path=permits_geojson,
        cadastre_metadata=CadastreMetadata(cadastre_status="OFICIAL_COMPLETO", jurisdiction="Entre Ríos"),
    )
    assert res_exp.classification == "PERMISO_VENCIDO"
    assert res_exp.priority == "ALTA_FISCALIZACION_INMEDIATA"
    assert res_exp.matched_permits[0].vigente_en_t1 is False

    # 4) Lejos de concesiones, con catastro PARCIAL_LOCAL vs OFICIAL_COMPLETO, y cercano <= 500 m
    anom_near = _make_anomaly(anomaly_id="NEAR-1", minx=5445250.0, miny=6498010.0, width_m=40.0, height_m=40.0)
    res_local = validate_anomaly_permits(
        anomaly=anom_near,
        t1_date="2026-11-08",
        permits_path=permits_geojson,
        cadastre_metadata=CadastreMetadata(cadastre_status="PARCIAL_LOCAL", jurisdiction="Municipal"),
    )
    assert res_local.classification == "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL"
    assert len(res_local.matched_permits) == 1
    assert res_local.matched_permits[0].distance_m == pytest.approx(150.0, abs=2.0)

    res_official = validate_anomaly_permits(
        anomaly=anom_near,
        t1_date="2026-11-08",
        permits_path=permits_geojson,
        cadastre_metadata=CadastreMetadata(cadastre_status="OFICIAL_COMPLETO", jurisdiction="Provincial"),
    )
    assert res_official.classification == "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS"


def test_vlm_judge_heuristic_and_explicit_override() -> None:
    """Valida todas las ramas heurísticas de `vlm_judge.py`, override explícito y generación de prompt."""
    anom_anth = _make_anomaly(confidence=0.85, confounder_score=0.15)
    verdict_anth = evaluate_candidate_vlm_verdict(anom_anth)
    assert isinstance(verdict_anth, VLMFluvialVerdict)
    assert verdict_anth.verdict == "EXTRACCION_ANTROPICA"
    assert verdict_anth.field_inspection_recommended is True
    assert verdict_anth.human_review_required is True

    anom_nat = _make_anomaly(confidence=0.58, confounder_score=0.72, zone="BARRAS_Y_BANCOS_MOVILES")
    verdict_nat = evaluate_candidate_vlm_verdict(anom_nat)
    assert verdict_nat.verdict == "DINAMICA_FLUVIAL_NATURAL"
    assert verdict_nat.field_inspection_recommended is False

    anom_cloud = _make_anomaly(confidence=0.50, valid_pixel_fraction=0.55, cloud_shadow_adjacent=True)
    verdict_cloud = evaluate_candidate_vlm_verdict(anom_cloud)
    assert verdict_cloud.verdict == "ARTEFACTO_NUBES_SOMBRAS"

    anom_doubt = _make_anomaly(confidence=0.62, confounder_score=0.45)
    verdict_doubt = evaluate_candidate_vlm_verdict(anom_doubt)
    assert verdict_doubt.verdict == "PENDIENTE_REVISION"
    assert verdict_doubt.field_inspection_recommended is True

    verdict_override = evaluate_candidate_vlm_verdict(
        anom_doubt,
        verdict="EXTRACCION_ANTROPICA",
        certainty_pct=88,
        extraction_evidence="Acopio claro y frente rectilíneo verificado en ortofoto.",
        fluvial_dynamics_assessment="Sin señal de crecida en el sector.",
        backend="human_vhr_review",
    )
    assert verdict_override.verdict == "EXTRACCION_ANTROPICA"
    assert verdict_override.certainty_pct == 88
    assert verdict_override.backend == "human_vhr_review"

    prompt = build_vlm_fluvial_prompt(anom_anth, t0_date="2025-11-10", t1_date="2026-11-08")
    assert "RIO-F3-001" in prompt
    assert "EXTRACCION_ANTROPICA" in prompt


def test_case_workflow_append_only_jsonl_and_sqlite(tmp_path: Path) -> None:
    """Verifica apertura idempotente, bitácora JSONL append-only y consultas SQLite."""
    storage_dir = tmp_path / "cases"
    mgr = CaseWorkflowManager(storage_dir=storage_dir)
    anom = _make_anomaly(anomaly_id="RIO-CASE-01", candidate_uid="CAND-UNIQUE-01")

    rec1 = mgr.open_or_get_case(
        anomaly=anom,
        actor="sistema_f3",
        summary="Apertura automática por detección persistente.",
        initial_status="SIN_CATASTRO_OFICIAL_CARGADO",
        priority="ALTA_FISCALIZACION_INMEDIATA",
    )
    # Segunda apertura con mismo candidate_uid debe ser idempotente
    rec1_dup = mgr.open_or_get_case(anomaly=anom)
    assert rec1_dup.case_id == rec1.case_id
    assert rec1_dup.events_count == 1

    rec2 = mgr.append_event(
        case_id=rec1.case_id,
        action_type="SOLICITUD_INSPECCION",
        summary="Derivado a delegación Prefectura Paraná para constatación.",
        actor="inspector_juan",
        new_status="INSPECCION_SOLICITADA",
    )
    assert rec2.status == "INSPECCION_SOLICITADA"
    assert rec2.events_count == 2

    rec3 = mgr.append_event(
        case_id=rec1.case_id,
        action_type="RESULTADO_CAMPO",
        summary="Constatación en ribera sin cartelería ni permiso exhibido.",
        actor="patrulla_pna",
        new_status="INSPECCIONADO_SIN_PERMISO_REGISTRADO",
        metadata={"acta_numero": "PNA-2026-884"},
    )
    assert rec3.events_count == 3
    assert rec3.status == "INSPECCIONADO_SIN_PERMISO_REGISTRADO"

    loaded_case, events = mgr.get_case(rec1.case_id)
    assert loaded_case.status == "INSPECCIONADO_SIN_PERMISO_REGISTRADO"
    assert [e.action_type for e in events] == [
        "APERTURA_CASO",
        "SOLICITUD_INSPECCION",
        "RESULTADO_CAMPO",
    ]
    assert events[0].previous_status is None
    assert events[2].previous_status == "INSPECCION_SOLICITADA"

    jsonl_lines = [line for line in mgr.jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(jsonl_lines) == 3
    parsed_last = json.loads(jsonl_lines[-1])
    assert parsed_last["metadata"]["acta_numero"] == "PNA-2026-884"

    listed_filtered = mgr.list_cases(status="INSPECCIONADO_SIN_PERMISO_REGISTRADO")
    assert len(listed_filtered) == 1
    assert listed_filtered[0].case_id == rec1.case_id


def test_generate_enforcement_report_and_mcp_tools(tmp_path: Path) -> None:
    """Prueba `generate_enforcement_report` y las 4 tools MCP de Fase 3 en `src/server.py`."""
    anomaly = _make_anomaly(anomaly_id="RIO-MCP-099", candidate_uid="CAND-MCP-099")
    # Simular un GeoJSON Feature directo como lo emite `tool_detect_extraction_anomalies`
    geojson_feature = {
        "type": "Feature",
        "id": anomaly.id,
        "geometry": anomaly.geometry_geojson,
        "properties": anomaly.model_dump(exclude={"geometry_geojson"}),
    }

    perm_dict = tool_validate_permits(
        anomaly_dict=geojson_feature,
        t1_date="2026-11-08",
        permits_gpkg_path=str(tmp_path / "missing_permits.gpkg"),
        cadastre_status="NO_DISPONIBLE",
        jurisdiction="Entre Ríos",
    )
    perm_model = PermitValidationResult(**perm_dict)
    assert perm_model.classification == "SIN_CATASTRO_OFICIAL_CARGADO"

    vlm_dict = tool_evaluate_vlm_verdict(
        anomaly_dict=geojson_feature,
        t0_date="2025-11-10",
        t1_date="2026-11-08",
    )
    assert "suggested_prompt" in vlm_dict
    vlm_prompt = vlm_dict.pop("suggested_prompt")
    assert "RIO-MCP-099" in vlm_prompt
    vlm_model = VLMFluvialVerdict(**vlm_dict)
    assert vlm_model.human_review_required is True

    case_dir = tmp_path / "cases_mcp"
    reports_dir = tmp_path / "reports_mcp"
    rep_dict = tool_generate_enforcement_report(
        anomaly_dict=geojson_feature,
        t0_scene_id="S2A_T0_20251110",
        t0_date="2025-11-10",
        t1_scene_id="S2B_T1_20261108",
        t1_date="2026-11-08",
        study_area_name="Paraná Medio - Sector Prueba",
        permits_gpkg_path=str(tmp_path / "missing_permits.gpkg"),
        output_dir=str(reports_dir),
        open_case_workflow=True,
        case_storage_dir=str(case_dir),
    )
    rep_model = EnforcementReportResult(**rep_dict)
    md_path = Path(rep_model.report_md_path)
    json_path = Path(rep_model.report_json_path)
    assert md_path.exists()
    assert json_path.exists()

    md_text = md_path.read_text(encoding="utf-8")
    assert "## 1. Ficha de identificación" in md_text
    assert "## 9. Fuentes, parámetros y trazabilidad técnica" in md_text
    assert "800.0 m² (desmonte ribereño / acopio)" in md_text
    assert "SIN_CATASTRO_OFICIAL_CARGADO" in md_text
    # Prudencia institucional: no afirmar delito ni ilegalidad categórica
    lowered = md_text.lower()
    assert "actividad ilegal comprobada" not in lowered
    assert "delito" not in lowered

    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_payload["human_review_required"] is True
    assert json_payload["case_record"]["case_id"] == "CASE-CAND-MCP-099"

    # Verificar gestión de caso vía tool MCP `tool_manage_case_workflow`
    listed = tool_manage_case_workflow(action="list", storage_dir=str(case_dir))
    assert listed["count"] == 1
    case_id = listed["cases"][0]["case_id"]

    updated = tool_manage_case_workflow(
        action="append_event",
        case_id=case_id,
        action_type="NOTA_ANALISTA",
        summary="Se remitió oficio a Minería de Entre Ríos.",
        actor="analista_gis",
        new_status="EN_REVISION_GABINETE",
        storage_dir=str(case_dir),
    )
    assert updated["status"] == "EN_REVISION_GABINETE"

    fetched = tool_manage_case_workflow(action="get", case_id=case_id, storage_dir=str(case_dir))
    assert fetched["case"]["events_count"] == 2
    assert len(fetched["events"]) == 2

    # Verificar llamada directa Python a `generate_enforcement_report`
    direct = generate_enforcement_report(
        anomaly=anomaly,
        t0_scene_id="S2A_T0",
        t0_date="2025-11-10",
        t1_scene_id="S2B_T1",
        t1_date="2026-11-08",
        output_dir=tmp_path / "direct_reports",
    )
    assert Path(direct.report_md_path).exists()
