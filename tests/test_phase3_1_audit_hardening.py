"""Pruebas integrales de Fase 3.1: auditoría semántica, integridad transaccional, modalidad VLM, atomicidad y seguridad MCP."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from src.config import PROJECT_ROOT
from src.core.case_workflow import CaseWorkflowManager
from src.core.crs_manager import reproject_geometry
from src.core.permits_validator import (
    _repair_geometry,
    _reproject_gdf_to_posgar,
    validate_anomaly_permits,
)
from src.core.report_generator import (
    generate_enforcement_report,
    verify_report_generation_integrity,
)
from src.core.vlm_judge import evaluate_candidate_vlm_verdict
from src.schemas import (
    CadastreMetadata,
    ExtractionAnomalyFeature,
    UncertaintyMetadata,
)
from src.server import (
    tool_compute_spectral_changes,
    tool_detect_extraction_anomalies,
    tool_evaluate_vlm_verdict,
    tool_generate_enforcement_report,
    tool_manage_case_workflow,
    tool_validate_permits,
)


def _make_anomaly(
    anomaly_id: str = "RIO-P31-001",
    minx: float = 5445000.0,
    miny: float = 6498000.0,
    width_m: float = 50.0,
    height_m: float = 60.0,
    confidence: float = 0.84,
    candidate_uid: str | None = "CAND-P31-001",
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
        anomaly_type="desmonte_ribera_acopio",
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
        geomorphological_zone="RIBERA_FIRME_Y_TERRAZA",
        distance_to_water_t0_m=180.0,
        uncertainty=UncertaintyMetadata(
            effective_resolution_m=20.0,
            valid_pixel_fraction=0.96,
            cloud_shadow_adjacent=False,
            water_stage_delta_pct=6.5,
            natural_dynamics_confounder_score=0.18,
            persistence_scenes_count=3,
            limitations=["Prueba controlada Fase 3.1 en POSGAR EPSG:5347."],
        ),
    )


def _write_permits_gpkg(path: Path, rows: List[Dict[str, Any]], epsg: int = 5347) -> Path:
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=f"EPSG:{epsg}")
    gdf.to_file(path, driver="GPKG")
    return path


def _official_meta(source_path: Path) -> CadastreMetadata:
    return CadastreMetadata(
        cadastre_status="OFICIAL_COMPLETO",
        jurisdiction="Provincia de Entre Ríos",
        authority_name="Secretaría de Minería Provincial",
        source_reference=f"Res. 102/2026 ({source_path})",
    )


# ---------------------------------------------------------------------------
# ETAPA 1: Seguridad semántica catastral, unión geométrica y CRS
# ---------------------------------------------------------------------------


def test_permit_missing_dates_not_authorized_and_no_synthetic_dates(tmp_path: Path) -> None:
    anom = _make_anomaly()
    perm_box = box(5444990.0, 6497990.0, 5445070.0, 6498070.0)
    gpkg = _write_permits_gpkg(
        tmp_path / "sin_fechas.gpkg",
        [
            {
                "permit_id": "PERM-SF-01",
                "titular": "Astillero Sin Fechas SA",
                "fecha_desde": None,
                "fecha_hasta": None,
                "actividad": "Extracción de arena",
                "geometry": perm_box,
            }
        ],
    )
    res = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg, cadastre_metadata=_official_meta(gpkg))
    assert res.classification == "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE"
    assert res.classification != "EXTRACCION_AUTORIZADA"
    assert res.priority == "MEDIA_VERIFICACION"
    assert len(res.matched_permits) == 1
    m = res.matched_permits[0]
    assert m.validity_status == "VIGENCIA_DESCONOCIDA"
    assert m.vigente_en_t1 is False
    assert m.fecha_desde is None
    assert m.fecha_hasta is None


def test_permit_missing_activity_not_authorized(tmp_path: Path) -> None:
    anom = _make_anomaly()
    perm_box = box(5444990.0, 6497990.0, 5445070.0, 6498070.0)
    gpkg = _write_permits_gpkg(
        tmp_path / "sin_actividad.gpkg",
        [
            {
                "permit_id": "PERM-SA-01",
                "titular": "Concesión Sin Rubro",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": None,
                "geometry": perm_box,
            }
        ],
    )
    res = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg, cadastre_metadata=_official_meta(gpkg))
    assert res.classification == "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE"
    assert res.matched_permits[0].validity_status == "VIGENTE_CONFIRMADO"
    assert res.matched_permits[0].vigente_en_t1 is True
    assert res.matched_permits[0].activity_status == "SIN_ACTIVIDAD_DECLARADA"


def test_permit_invalid_or_inverted_dates_not_verifiable(tmp_path: Path) -> None:
    anom = _make_anomaly()
    perm_box = box(5444990.0, 6497990.0, 5445070.0, 6498070.0)

    gpkg_bad = _write_permits_gpkg(
        tmp_path / "fecha_invalida.gpkg",
        [
            {
                "permit_id": "PERM-INV-DATE",
                "titular": "Fecha Corrupta",
                "fecha_desde": "2025-13-45",
                "fecha_hasta": "2026-12-31",
                "actividad": "Extracción de arena",
                "geometry": perm_box,
            }
        ],
    )
    res_bad = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg_bad)
    assert res_bad.classification == "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE"
    assert res_bad.matched_permits[0].validity_status == "VIGENCIA_DESCONOCIDA"
    assert res_bad.matched_permits[0].vigente_en_t1 is False

    gpkg_flip = _write_permits_gpkg(
        tmp_path / "fecha_invertida.gpkg",
        [
            {
                "permit_id": "PERM-FLIP-DATE",
                "titular": "Rango Invertido",
                "fecha_desde": "2026-12-31",
                "fecha_hasta": "2025-01-01",
                "actividad": "Cantera aluvial",
                "geometry": perm_box,
            }
        ],
    )
    res_flip = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg_flip)
    assert res_flip.classification == "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE"
    assert res_flip.matched_permits[0].validity_status == "VIGENCIA_DESCONOCIDA"
    assert res_flip.matched_permits[0].vigente_en_t1 is False


def test_permit_expired_classified_as_permiso_vencido(tmp_path: Path) -> None:
    anom = _make_anomaly()
    perm_box = box(5444990.0, 6497990.0, 5445070.0, 6498070.0)
    gpkg = _write_permits_gpkg(
        tmp_path / "vencido.gpkg",
        [
            {
                "permit_id": "PERM-EXP-01",
                "titular": "Arenera Vencida",
                "fecha_desde": "2023-01-01",
                "fecha_hasta": "2025-01-01",
                "actividad": "Dragado de arena",
                "geometry": perm_box,
            }
        ],
    )
    res = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg)
    assert res.classification == "PERMISO_VENCIDO"
    assert res.matched_permits[0].validity_status == "VENCIDO"
    assert res.matched_permits[0].vigente_en_t1 is False
    assert res.priority == "ALTA_FISCALIZACION_INMEDIATA"


def test_permit_active_incompatible_activity_rejected(tmp_path: Path) -> None:
    anom = _make_anomaly()
    perm_box = box(5444990.0, 6497990.0, 5445070.0, 6498070.0)

    for idx, act in enumerate(["conservación de humedal", "muelle náutico", "veda sin extracción de arena"]):
        gpkg = _write_permits_gpkg(
            tmp_path / f"incompatible_{idx}.gpkg",
            [
                {
                    "permit_id": f"PERM-INC-{idx}",
                    "titular": "Permiso No Extractivo",
                    "fecha_desde": "2025-01-01",
                    "fecha_hasta": "2027-01-01",
                    "actividad": act,
                    "geometry": perm_box,
                }
            ],
        )
        res = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg, cadastre_metadata=_official_meta(gpkg))
        assert res.classification == "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE"
        assert res.matched_permits[0].validity_status == "VIGENTE_CONFIRMADO"
        assert res.matched_permits[0].activity_status == "INCOMPATIBLE"


def test_multiple_overlapping_permits_union_no_double_counting(tmp_path: Path) -> None:
    # Anomalía [5445000..5445050] x [6498000..6498060] -> área = 3000 m2
    anom = _make_anomaly(minx=5445000.0, miny=6498000.0, width_m=50.0, height_m=60.0)

    # Dos permisos solapados (izquierdo 60% y derecho 60% con 20% de superposición mutua) que cubren 100% en unión
    p_left = box(5445000.0, 6498000.0, 5445030.0, 6498060.0)
    p_right = box(5445020.0, 6498000.0, 5445050.0, 6498060.0)
    gpkg_full = _write_permits_gpkg(
        tmp_path / "union_100.gpkg",
        [
            {
                "permit_id": "PERM-U1",
                "titular": "Arenera Oeste",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Dragado de arena",
                "geometry": p_left,
            },
            {
                "permit_id": "PERM-U2",
                "titular": "Arenera Este",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Acopio de áridos",
                "geometry": p_right,
            },
        ],
    )
    res_full = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg_full)
    assert res_full.classification == "EXTRACCION_AUTORIZADA"
    assert res_full.excess_area_m2 == 0.0
    assert res_full.excess_pct == 0.0
    assert len(res_full.matched_permits) == 2

    # Dos permisos solapados que juntos cubren 70% (x: 0..25 y 15..35 -> unión 0..35 de 50 => 70%, exceso 30%)
    p_a = box(5445000.0, 6498000.0, 5445025.0, 6498060.0)
    p_b = box(5445015.0, 6498000.0, 5445035.0, 6498060.0)
    gpkg_part = _write_permits_gpkg(
        tmp_path / "union_70.gpkg",
        [
            {
                "permit_id": "PERM-P1",
                "titular": "Arenera A",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Extracción de arena",
                "geometry": p_a,
            },
            {
                "permit_id": "PERM-P2",
                "titular": "Arenera B",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Extracción de áridos",
                "geometry": p_b,
            },
        ],
    )
    res_part = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg_part, excess_tolerance_pct=15.0)
    assert res_part.classification == "EXCESO_DE_POLIGONO"
    assert res_part.excess_pct == pytest.approx(30.0, abs=0.2)
    assert res_part.excess_area_m2 == pytest.approx(900.0, abs=5.0)


def test_oficial_completo_without_provenance_downgraded_to_parcial_local(tmp_path: Path) -> None:
    anom = _make_anomaly()
    gpkg = _write_permits_gpkg(
        tmp_path / "local_only.gpkg",
        [
            {
                "permit_id": "PERM-LOC-1",
                "titular": "Registro Local",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Extracción de arena",
                "geometry": box(5446000.0, 6499000.0, 5446100.0, 6499100.0),
            }
        ],
    )
    # Directo sin authority_name -> degrada OFICIAL_COMPLETO a PARCIAL_LOCAL
    res = validate_anomaly_permits(
        anom,
        t1_date="2026-03-10",
        permits_path=gpkg,
        cadastre_metadata=CadastreMetadata(
            cadastre_status="OFICIAL_COMPLETO",
            jurisdiction="No especificada",
            authority_name=None,
            source_reference=str(gpkg),
        ),
    )
    assert res.cadastre_metadata is not None
    assert res.cadastre_metadata.cadastre_status == "PARCIAL_LOCAL"
    assert res.classification == "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL"
    assert "PARCIAL_LOCAL" in res.observations

    # Vía tool MCP sin authority_name también degrada
    out_downgraded = tool_validate_permits(
        anomaly_dict=anom.model_dump(),
        t1_date="2026-03-10",
        permits_gpkg_path=str(gpkg),
        cadastre_status="OFICIAL_COMPLETO",
        jurisdiction="Entre Ríos",
        authority_name=None,
    )
    assert out_downgraded["cadastre_metadata"]["cadastre_status"] == "PARCIAL_LOCAL"

    # Con authority_name explícito conserva OFICIAL_COMPLETO
    out_official = tool_validate_permits(
        anomaly_dict=anom.model_dump(),
        t1_date="2026-03-10",
        permits_gpkg_path=str(gpkg),
        cadastre_status="OFICIAL_COMPLETO",
        jurisdiction="Entre Ríos",
        authority_name="Secretaría de Minería Provincial",
        source_reference="Expediente ME-2026-4412",
    )
    assert out_official["cadastre_metadata"]["cadastre_status"] == "OFICIAL_COMPLETO"
    assert out_official["classification"] == "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS"


def test_anomaly_geometry_and_crs_repair_validations(tmp_path: Path) -> None:
    bad_empty = _make_anomaly().model_copy(
        update={"geometry_geojson": {"type": "Polygon", "coordinates": []}}
    )
    with pytest.raises(ValueError, match="coordenadas GeoJSON válidas"):
        validate_anomaly_permits(bad_empty, t1_date="2026-03-10")

    bad_point = _make_anomaly().model_copy(
        update={"geometry_geojson": {"type": "Point", "coordinates": [-60.6, -31.7]}}
    )
    with pytest.raises(ValueError, match="Polygon o MultiPolygon"):
        validate_anomaly_permits(bad_point, t1_date="2026-03-10")

    # Polígono moño autointersante en padrón reparado mediante _repair_geometry
    bowtie = Polygon(
        [
            (5445000.0, 6498000.0),
            (5445050.0, 6498060.0),
            (5445050.0, 6498000.0),
            (5445000.0, 6498060.0),
            (5445000.0, 6498000.0),
        ]
    )
    assert not bowtie.is_valid
    repaired = _repair_geometry(bowtie)
    assert repaired.is_valid
    assert repaired.area > 0.0

    anom = _make_anomaly()
    gpkg_bowtie = _write_permits_gpkg(
        tmp_path / "bowtie.gpkg",
        [
            {
                "permit_id": "PERM-BOWTIE",
                "titular": "Geometría Corregible",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Extracción de arena",
                "geometry": bowtie,
            }
        ],
    )
    res_bowtie = validate_anomaly_permits(anom, t1_date="2026-03-10", permits_path=gpkg_bowtie)
    assert len(res_bowtie.matched_permits) == 1

    # Inferencia CRS acotada en _reproject_gdf_to_posgar
    gdf_wgs = gpd.GeoDataFrame(geometry=[box(-60.60, -31.70, -60.59, -31.69)])
    proj_wgs, warn_wgs = _reproject_gdf_to_posgar(gdf_wgs, 5347, "catastro")
    assert proj_wgs.crs is not None and proj_wgs.crs.to_epsg() == 5347
    assert any("EPSG:4326" in w for w in warn_wgs)

    gdf_posgar = gpd.GeoDataFrame(geometry=[box(5445000.0, 6498000.0, 5445100.0, 6498100.0)])
    proj_posgar, warn_posgar = _reproject_gdf_to_posgar(gdf_posgar, 5347, "catastro")
    assert proj_posgar.crs is not None and proj_posgar.crs.to_epsg() == 5347
    assert any("EPSG:5347" in w for w in warn_posgar)

    gdf_oob = gpd.GeoDataFrame(geometry=[box(99999999.0, 99999999.0, 100000100.0, 100000100.0)])
    with pytest.raises(ValueError, match="carece de CRS"):
        _reproject_gdf_to_posgar(gdf_oob, 5347, "catastro")


# ---------------------------------------------------------------------------
# ETAPA 2: Workflow transaccional SQLite, concurrencia, idempotencia y tampering
# ---------------------------------------------------------------------------


def test_workflow_recovery_after_simulated_jsonl_export_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anom = _make_anomaly(anomaly_id="RIO-REC-01", candidate_uid="CAND-REC-01")
    mgr = CaseWorkflowManager(storage_dir=tmp_path / "wf_rec")
    rec = mgr.open_or_get_case(anom, actor="auto", summary="Apertura.")

    def _boom(_self: CaseWorkflowManager, _conn: sqlite3.Connection) -> None:
        raise RuntimeError("Fallo simulado al exportar JSONL tras commit SQLite")

    monkeypatch.setattr(CaseWorkflowManager, "_export_jsonl_unlocked", _boom)
    with pytest.raises(RuntimeError, match="Fallo simulado"):
        mgr.append_event(
            case_id=rec.case_id,
            action_type="NOTA_ANALISTA",
            summary="Evento persistido en SQLite pese a caída de exportación.",
            actor="analista",
        )

    monkeypatch.undo()
    # SQLite conservó el evento; JSONL aún tiene solo 1 línea
    assert len(mgr.jsonl_path.read_text(encoding="utf-8").splitlines()) == 1
    case_sql, events_sql = mgr.get_case(rec.case_id)
    assert case_sql.events_count == 2
    assert len(events_sql) == 2

    synced_count = mgr.sync_jsonl_from_sqlite()
    assert synced_count == 2
    assert len(mgr.jsonl_path.read_text(encoding="utf-8").splitlines()) == 2
    integrity = mgr.verify_log_integrity()
    assert integrity["valid"] is True
    assert integrity["sqlite_events_count"] == 2
    assert integrity["jsonl_events_count"] == 2


def test_workflow_concurrent_open_and_append_events_with_barrier(tmp_path: Path) -> None:
    storage = tmp_path / "wf_conc"
    anom = _make_anomaly(anomaly_id="RIO-CONC-01", candidate_uid="CAND-CONC-01")

    n_open_threads = 6
    open_barrier = threading.Barrier(n_open_threads)
    open_results: List[str] = []
    open_errors: List[BaseException] = []
    lock = threading.Lock()

    def _worker_open() -> None:
        try:
            open_barrier.wait(timeout=10.0)
            rec = CaseWorkflowManager(storage_dir=storage).open_or_get_case(
                anom, actor="worker", summary="Apertura concurrente."
            )
            with lock:
                open_results.append(rec.case_id)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                open_errors.append(exc)

    threads_open = [threading.Thread(target=_worker_open) for _ in range(n_open_threads)]
    for t in threads_open:
        t.start()
    for t in threads_open:
        t.join(timeout=20.0)

    assert not open_errors
    assert len(open_results) == n_open_threads
    assert len(set(open_results)) == 1
    case_id = open_results[0]

    mgr = CaseWorkflowManager(storage_dir=storage)
    rec0, events0 = mgr.get_case(case_id)
    assert rec0.events_count == 1
    assert len(events0) == 1

    # Ahora 8 hilos agregan eventos concurrentemente sobre el mismo caso
    n_append_threads = 8
    append_barrier = threading.Barrier(n_append_threads)
    append_errors: List[BaseException] = []

    def _worker_append(idx: int) -> None:
        try:
            append_barrier.wait(timeout=10.0)
            CaseWorkflowManager(storage_dir=storage).append_event(
                case_id=case_id,
                action_type="NOTA_ANALISTA",
                summary=f"Nota concurrente #{idx}",
                actor=f"op_{idx}",
            )
        except BaseException as exc:  # noqa: BLE001
            with lock:
                append_errors.append(exc)

    threads_append = [threading.Thread(target=_worker_append, args=(i,)) for i in range(n_append_threads)]
    for t in threads_append:
        t.start()
    for t in threads_append:
        t.join(timeout=20.0)

    assert not append_errors
    rec_final, events_final = mgr.get_case(case_id)
    assert rec_final.events_count == 1 + n_append_threads
    assert len(events_final) == 1 + n_append_threads
    integrity = mgr.verify_log_integrity()
    assert integrity["valid"] is True
    assert integrity["sqlite_events_count"] == 1 + n_append_threads
    assert integrity["jsonl_events_count"] == 1 + n_append_threads


def test_workflow_idempotency_key_deduplicates_events(tmp_path: Path) -> None:
    storage = tmp_path / "wf_idem"
    anom = _make_anomaly(anomaly_id="RIO-IDEM-01", candidate_uid="CAND-IDEM-01")
    mgr = CaseWorkflowManager(storage_dir=storage)
    rec = mgr.open_or_get_case(anom)

    r1 = mgr.append_event(
        case_id=rec.case_id,
        action_type="SOLICITUD_INSPECCION",
        summary="Oficio PNA-2026-01",
        actor="fiscalizador",
        new_status="INSPECCION_SOLICITADA",
        idempotency_key="oficio-2026-01",
    )
    r2 = mgr.append_event(
        case_id=rec.case_id,
        action_type="SOLICITUD_INSPECCION",
        summary="Reintento idempotente Oficio PNA-2026-01",
        actor="fiscalizador",
        new_status="INSPECCION_SOLICITADA",
        idempotency_key="oficio-2026-01",
    )
    assert r1.events_count == 2
    assert r2.events_count == 2

    # Vía tool MCP también deduplica
    r3 = tool_manage_case_workflow(
        action="append_event",
        case_id=rec.case_id,
        action_type="NOTA_ANALISTA",
        summary="Nota MCP única",
        idempotency_key="mcp-note-1",
        storage_dir=str(storage),
    )
    r4 = tool_manage_case_workflow(
        action="append_event",
        case_id=rec.case_id,
        action_type="NOTA_ANALISTA",
        summary="Reintento Nota MCP",
        idempotency_key="mcp-note-1",
        storage_dir=str(storage),
    )
    assert r3["events_count"] == 3
    assert r4["events_count"] == 3


def test_workflow_rebuild_sqlite_from_jsonl_disaster_recovery(tmp_path: Path) -> None:
    storage = tmp_path / "wf_disaster"
    anom = _make_anomaly(anomaly_id="RIO-DIS-01", candidate_uid="CAND-DIS-01")
    mgr = CaseWorkflowManager(storage_dir=storage)
    rec = mgr.open_or_get_case(anom, priority="ALTA_FISCALIZACION_INMEDIATA")
    mgr.append_event(rec.case_id, "NOTA_ANALISTA", "Verificación 1", actor="op1")
    mgr.append_event(rec.case_id, "CAMBIO_ESTADO", "A revisión", actor="op2", new_status="EN_REVISION_GABINETE")
    original_integrity = mgr.verify_log_integrity()
    assert original_integrity["sqlite_events_count"] == 3

    # Caso 1: eliminar SQLite y reconstruir explícitamente sobre mgr
    mgr.sqlite_path.unlink()
    rebuilt_cases = mgr.rebuild_sqlite_from_jsonl(expected_head_hash=original_integrity["head_event_hash"])
    assert rebuilt_cases == 1
    post_rec, post_events = mgr.get_case(rec.case_id)
    assert post_rec.status == "EN_REVISION_GABINETE"
    assert post_rec.events_count == 3
    assert len(post_events) == 3

    # Caso 2: perder SQLite y re-instanciar CaseWorkflowManager desde cero (NO debe sobrescribir JSONL)
    mgr.sqlite_path.unlink()
    for side in ("-wal", "-shm"):
        Path(str(mgr.sqlite_path) + side).unlink(missing_ok=True)
    recovered_mgr = CaseWorkflowManager(storage_dir=storage)
    recovered_rec, recovered_events = recovered_mgr.get_case(rec.case_id)
    assert recovered_rec.events_count == 3
    assert len(recovered_events) == 3
    assert recovered_mgr.verify_log_integrity(expected_head_hash=original_integrity["head_event_hash"])["valid"] is True

    # Caso 3: si SQLite tiene 0 filas pero JSONL tiene eventos, sync_jsonl_from_sqlite bloquea sobrescritura
    conn = sqlite3.connect(recovered_mgr.sqlite_path)
    conn.execute("DROP TRIGGER IF EXISTS trg_case_events_no_delete")
    conn.execute("DELETE FROM case_events")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="rebuild_sqlite_from_jsonl"):
        recovered_mgr.sync_jsonl_from_sqlite()


def test_workflow_tamper_detection_and_append_only_sqlite_triggers(tmp_path: Path) -> None:
    storage = tmp_path / "wf_tamper"
    anom = _make_anomaly(anomaly_id="RIO-TAMP-01", candidate_uid="CAND-TAMP-01")
    mgr = CaseWorkflowManager(storage_dir=storage)
    rec = mgr.open_or_get_case(anom)
    mgr.append_event(rec.case_id, "NOTA_ANALISTA", "Resumen legítimo", actor="op")

    # Triggers append-only en SQLite deben bloquear UPDATE y DELETE sobre case_events
    conn = sqlite3.connect(mgr.sqlite_path)
    try:
        with pytest.raises(sqlite3.Error, match="append-only"):
            conn.execute("UPDATE case_events SET summary = 'ADULTERADO'")
        with pytest.raises(sqlite3.Error, match="append-only"):
            conn.execute("DELETE FROM case_events")
    finally:
        conn.close()

    # Alterar 1 byte del contenido en case_events.jsonl debe fallar verify_log_integrity
    lines = mgr.jsonl_path.read_text(encoding="utf-8").splitlines()
    tampered = lines[1].replace("Resumen legítimo", "Resumen alterado")
    mgr.jsonl_path.write_text("\n".join([lines[0], tampered]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Alteración detectada"):
        mgr.verify_log_integrity()


# ---------------------------------------------------------------------------
# ETAPA 3: Separación estricta de modalidades de evaluación y hashes
# ---------------------------------------------------------------------------


def test_vlm_judge_separates_heuristic_external_vlm_and_human_expert(tmp_path: Path) -> None:
    anom = _make_anomaly()

    # 1. Modo por defecto -> HEURISTIC_SCREENING explícito, no VLM ni probabilidad estadística
    heur = evaluate_candidate_vlm_verdict(anom)
    assert heur.evaluation_mode == "HEURISTIC_SCREENING"
    assert heur.is_statistical_probability is False
    assert heur.is_forensic_certification is False
    assert heur.human_review_required is True
    assert "cribado heurístico" in heur.extraction_evidence.lower()

    # 2. EXTERNAL_VLM exige verdict, model_id e image_refs
    with pytest.raises(ValueError, match="verdict"):
        evaluate_candidate_vlm_verdict(anom, evaluation_mode="EXTERNAL_VLM", model_id="gemini-2.5-pro", image_refs=["stac://img"])
    with pytest.raises(ValueError, match="model_id"):
        evaluate_candidate_vlm_verdict(anom, evaluation_mode="EXTERNAL_VLM", verdict="EXTRACCION_ANTROPICA", image_refs=["stac://img"])
    with pytest.raises(ValueError, match="image_refs"):
        evaluate_candidate_vlm_verdict(anom, evaluation_mode="EXTERNAL_VLM", verdict="EXTRACCION_ANTROPICA", model_id="gemini-2.5-pro")

    img_file = tmp_path / "chip_t1.png"
    img_bytes = b"\x89PNG\r\n\x1a\nfake-png-content-for-hash-verification"
    img_file.write_bytes(img_bytes)
    expected_sha = hashlib.sha256(img_bytes).hexdigest()

    ext = evaluate_candidate_vlm_verdict(
        anom,
        evaluation_mode="EXTERNAL_VLM",
        verdict="EXTRACCION_ANTROPICA",
        certainty_pct=88,
        model_id="gemini-2.5-pro",
        model_version="2026-01",
        image_refs=[str(img_file)],
    )
    assert ext.evaluation_mode == "EXTERNAL_VLM"
    assert ext.model_id == "gemini-2.5-pro"
    assert ext.model_version == "2026-01"
    assert ext.image_hashes[str(img_file)] == expected_sha
    assert ext.is_statistical_probability is False
    assert ext.is_forensic_certification is False
    assert ext.human_review_required is True

    # 3. HUMAN_EXPERT exige verdict y evaluator_id
    with pytest.raises(ValueError, match="verdict"):
        evaluate_candidate_vlm_verdict(anom, evaluation_mode="HUMAN_EXPERT", evaluator_id="perito_1")
    with pytest.raises(ValueError, match="evaluator_id"):
        evaluate_candidate_vlm_verdict(anom, evaluation_mode="HUMAN_EXPERT", verdict="DINAMICA_FLUVIAL_NATURAL")

    hum = evaluate_candidate_vlm_verdict(
        anom,
        evaluation_mode="HUMAN_EXPERT",
        verdict="DINAMICA_FLUVIAL_NATURAL",
        certainty_pct=95,
        evaluator_id="perito_conicet_01",
        image_refs=[str(img_file)],
    )
    assert hum.evaluation_mode == "HUMAN_EXPERT"
    assert hum.evaluator_id == "perito_conicet_01"
    assert hum.is_statistical_probability is False
    assert hum.is_forensic_certification is True


# ---------------------------------------------------------------------------
# ETAPA 4: Reproducibilidad y publicación atómica de expedientes
# ---------------------------------------------------------------------------


def test_report_atomic_generation_manifest_hashes_and_tamper_detection(tmp_path: Path) -> None:
    anom = _make_anomaly(anomaly_id="RIO-GEN-01")
    out_dir = tmp_path / "reports"
    dummy_raster = tmp_path / "scene_t1.tif"
    dummy_raster.write_bytes(b"II*\x00synthetic-tiff-bytes")
    dummy_fig = tmp_path / "fig_01.png"
    dummy_fig.write_bytes(b"\x89PNG\r\n\x1a\nfig01")

    multitemporal = [
        {"fecha": "2024-03-10", "scene_id": "S2A_T0", "hidrometro_m": 2.69, "source": "INA_API_A5", "ndvi": 0.42},
        {"fecha": "2026-03-10", "scene_id": "S2B_T1", "hidrometro_m": 2.95, "source": "INA_API_A5", "ndvi": -0.05},
    ]

    rep = generate_enforcement_report(
        anomaly=anom,
        t0_scene_id="S2A_T0",
        t0_date="2024-03-10",
        t1_scene_id="S2B_T1",
        t1_date="2026-03-10",
        multitemporal_series=multitemporal,
        study_area_name="Paraná Medio — Auditoría 3.1",
        output_dir=out_dir,
        detection_metadata={"t1_raster_path": str(dummy_raster)},
        map_artifacts=[str(dummy_fig)],
    )

    gen_dir = Path(rep.generation_dir_path)
    assert gen_dir.parent.name == "generations"
    assert gen_dir.name == rep.report_generation_id
    assert (gen_dir / "report.md").is_file()
    assert (gen_dir / "report.json").is_file()
    assert (gen_dir / "manifest.json").is_file()

    pointer_path = out_dir / "RIO-GEN-01" / "current_generation.json"
    assert pointer_path.is_file()
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["active_generation_id"] == rep.report_generation_id

    report_payload = json.loads((gen_dir / "report.json").read_text(encoding="utf-8"))
    manifest_payload = json.loads((gen_dir / "manifest.json").read_text(encoding="utf-8"))
    assert report_payload["software_commit_sha"] == rep.software_commit_sha
    assert report_payload["markdown_sha256"] == rep.markdown_sha256
    assert report_payload["inputs_provenance"]["multitemporal_series"] == multitemporal
    input_hashes = report_payload["inputs_provenance"]["input_hashes"]
    assert input_hashes[f"t1_raster_path:{dummy_raster}"]["sha256"] == hashlib.sha256(
        b"II*\x00synthetic-tiff-bytes"
    ).hexdigest()
    assert input_hashes[f"map_artifact:{dummy_fig}"]["sha256"] == hashlib.sha256(
        b"\x89PNG\r\n\x1a\nfig01"
    ).hexdigest()
    assert manifest_payload["report_generation_id"] == rep.report_generation_id

    verified = verify_report_generation_integrity(gen_dir)
    assert verified["valid"] is True

    # Alterar report.md dispara ValueError
    md_path = gen_dir / "report.md"
    orig_md = md_path.read_text(encoding="utf-8")
    md_path.write_text(orig_md + "\n<!-- byte adulterado -->\n", encoding="utf-8")
    with pytest.raises(ValueError, match="report.md adulterado"):
        verify_report_generation_integrity(gen_dir)

    # Restaurar report.md y alterar report.json también falla
    md_path.write_text(orig_md, encoding="utf-8")
    report_payload["study_area_name"] = "Modificado"
    (gen_dir / "report.json").write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="report.json adulterado"):
        verify_report_generation_integrity(gen_dir)


def test_report_atomic_failure_before_replace_leaves_no_partial_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anom = _make_anomaly(anomaly_id="RIO-ATOM-01")
    out_dir = tmp_path / "reports_atomic"

    gen1 = generate_enforcement_report(
        anomaly=anom,
        t0_scene_id="S2A_T0",
        t0_date="2024-03-10",
        t1_scene_id="S2B_T1",
        t1_date="2026-03-10",
        output_dir=out_dir,
    )
    pointer_path = out_dir / "RIO-ATOM-01" / "current_generation.json"
    pointer_before = pointer_path.read_text(encoding="utf-8")

    def _fail_replace(src: Path | str, dst: Path | str) -> None:
        raise RuntimeError(f"Fallo atómico simulado al mover {src} -> {dst}")

    monkeypatch.setattr("src.core.report_generator.os.replace", _fail_replace)
    with pytest.raises(RuntimeError, match="Fallo atómico simulado"):
        generate_enforcement_report(
            anomaly=anom,
            t0_scene_id="S2A_T0",
            t0_date="2024-03-10",
            t1_scene_id="S2B_T1",
            t1_date="2026-03-10",
            output_dir=out_dir,
        )

    gens_root = out_dir / "RIO-ATOM-01" / "generations"
    children = list(gens_root.iterdir())
    assert [p.name for p in children] == [gen1.report_generation_id]
    assert not any(p.name.startswith(".staging_") for p in children)
    assert pointer_path.read_text(encoding="utf-8") == pointer_before


# ---------------------------------------------------------------------------
# ETAPA 5: Seguridad de rutas MCP (lectura vs escritura, symlinks, extensiones)
# ---------------------------------------------------------------------------


def test_mcp_path_security_blocks_traversal_forbidden_roots_symlinks_and_bad_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anom = _make_anomaly().model_dump()
    valid_tif = tmp_path / "valid_t0.tif"
    valid_tif.write_bytes(b"fake-tif")
    valid_gpkg = _write_permits_gpkg(
        tmp_path / "valid_permits.gpkg",
        [
            {
                "permit_id": "PERM-SEC-1",
                "titular": "Sec Test",
                "fecha_desde": "2025-01-01",
                "fecha_hasta": "2027-01-01",
                "actividad": "Extracción de arena",
                "geometry": box(5446000.0, 6499000.0, 5446100.0, 6499100.0),
            }
        ],
    )
    valid_img = tmp_path / "valid_chip.png"
    valid_img.write_bytes(b"\x89PNG\r\n\x1a\nvalid")

    # 1. Rutas fuera de raíces permitidas (sistema, src/, configuración, traversal .., tmp global)
    global_tmp_file = Path(tempfile.gettempdir()).resolve() / "rio_alerta_outside_pytest_user.gpkg"
    forbidden_read_targets = [
        "/etc/hosts",
        str(PROJECT_ROOT / "src" / "config.py"),
        str(PROJECT_ROOT / "pyproject.toml"),
        str(tmp_path / ".." / "escape.gpkg"),
        "../../etc/passwd",
        str(global_tmp_file),
    ]
    for bad in forbidden_read_targets:
        with pytest.raises(PermissionError):
            tool_validate_permits(anom, "2026-03-10", permits_gpkg_path=bad)
        with pytest.raises(PermissionError):
            tool_compute_spectral_changes(t0_path=bad, t1_path=str(valid_tif))
        with pytest.raises(PermissionError):
            tool_detect_extraction_anomalies(t0_path=str(valid_tif), t1_path=bad)
        with pytest.raises(PermissionError):
            tool_evaluate_vlm_verdict(
                anom,
                evaluation_mode="EXTERNAL_VLM",
                verdict="EXTRACCION_ANTROPICA",
                model_id="gemini-2.5-pro",
                image_refs=[bad],
            )

    # 2. Escrituras prohibidas en src/, config, raíz o experimentos históricos congelados
    forbidden_write_dirs = [
        "/tmp/unconfined_mcp_out",
        str(PROJECT_ROOT / "src" / "injected"),
        str(PROJECT_ROOT / "data" / "experiments" / "exp_002_validation"),
        str(PROJECT_ROOT / "data" / "raw" / "leak_write"),
        str(PROJECT_ROOT / ".." / "outside_dir"),
    ]
    for bad_dir in forbidden_write_dirs:
        with pytest.raises(PermissionError):
            tool_manage_case_workflow(action="list", storage_dir=bad_dir)
        with pytest.raises(PermissionError):
            tool_generate_enforcement_report(
                anom,
                t0_scene_id="S2A",
                t0_date="2024-03-10",
                t1_scene_id="S2B",
                t1_date="2026-03-10",
                output_dir=bad_dir,
                open_case_workflow=False,
            )
        with pytest.raises(PermissionError):
            tool_generate_enforcement_report(
                anom,
                t0_scene_id="S2A",
                t0_date="2024-03-10",
                t1_scene_id="S2B",
                t1_date="2026-03-10",
                output_dir=str(tmp_path / "ok_reports"),
                open_case_workflow=True,
                case_storage_dir=bad_dir,
            )

    # 3. Symlinks bloqueados incluso dentro de una raíz autorizada
    sym_gpkg = tmp_path / "link_permits.gpkg"
    os.symlink(valid_gpkg, sym_gpkg)
    with pytest.raises(PermissionError, match="Symlink"):
        tool_validate_permits(anom, "2026-03-10", permits_gpkg_path=str(sym_gpkg))

    sym_tif = tmp_path / "link_t0.tif"
    os.symlink(valid_tif, sym_tif)
    with pytest.raises(PermissionError, match="Symlink"):
        tool_detect_extraction_anomalies(t0_path=str(sym_tif), t1_path=str(valid_tif))

    sym_img = tmp_path / "link_chip.png"
    os.symlink(valid_img, sym_img)
    with pytest.raises(PermissionError, match="Symlink"):
        tool_evaluate_vlm_verdict(
            anom,
            evaluation_mode="EXTERNAL_VLM",
            verdict="EXTRACCION_ANTROPICA",
            model_id="gemini-2.5-pro",
            image_refs=[str(sym_img)],
        )

    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "link_dir"
    os.symlink(real_dir, sym_dir)
    with pytest.raises(PermissionError, match="Symlink"):
        tool_manage_case_workflow(action="list", storage_dir=str(sym_dir))
    with pytest.raises(PermissionError, match="Symlink"):
        tool_generate_enforcement_report(
            anom,
            t0_scene_id="S2A",
            t0_date="2024-03-10",
            t1_scene_id="S2B",
            t1_date="2026-03-10",
            output_dir=str(sym_dir),
            open_case_workflow=False,
        )
    with pytest.raises(PermissionError, match="Symlink"):
        tool_generate_enforcement_report(
            anom,
            t0_scene_id="S2A",
            t0_date="2024-03-10",
            t1_scene_id="S2B",
            t1_date="2026-03-10",
            output_dir=str(tmp_path / "ok_out"),
            open_case_workflow=True,
            case_storage_dir=str(sym_dir),
        )

    # 4. Extensiones y tipos de nodo inválidos
    bad_ext = tmp_path / "not_a_raster.txt"
    bad_ext.write_text("hola", encoding="utf-8")
    with pytest.raises(ValueError, match="Extensión"):
        tool_compute_spectral_changes(t0_path=str(bad_ext), t1_path=str(valid_tif))
    with pytest.raises(ValueError, match="Extensión"):
        tool_validate_permits(anom, "2026-03-10", permits_gpkg_path=str(bad_ext))
    with pytest.raises(ValueError, match="Extensión"):
        tool_evaluate_vlm_verdict(
            anom,
            evaluation_mode="EXTERNAL_VLM",
            verdict="EXTRACCION_ANTROPICA",
            model_id="gemini-2.5-pro",
            image_refs=[str(bad_ext)],
        )
    with pytest.raises(ValueError, match="directorio"):
        tool_manage_case_workflow(action="list", storage_dir=str(valid_img))

    # 5. Sin PYTEST_CURRENT_TEST ni RIO_ALERTA_TEMP_DIR, incluso tmp_path queda bloqueado
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("RIO_ALERTA_TEMP_DIR", raising=False)
    with pytest.raises(PermissionError):
        tool_validate_permits(anom, "2026-03-10", permits_gpkg_path=str(valid_gpkg))
