"""Tests Fase 3.2 P2: estado CAMBIO_REAL_INDETERMINADO, ficha de caso versionada y dominio SCL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from src.core.case_file import CASE_FILE_SCHEMA_VERSION, build_case_file, write_case_file
from src.core.case_workflow import CaseWorkflowManager
from src.core.change_detector import detect_extraction_anomalies
from src.schemas import coerce_anomaly_feature
from src.server import mcp, tool_generate_case_file, tool_manage_case_workflow
from tests.test_phase3_1_audit_hardening import _make_anomaly
from tests.test_review_improvements import _write_local_s2_pair

# ---------------------------------------------------------------------------
# Estado terminal CAMBIO_REAL_INDETERMINADO
# ---------------------------------------------------------------------------


def test_case_status_indefinite_is_accepted_by_workflow(tmp_path: Path) -> None:
    """El estado terminal de primera clase se acepta en apertura y transición append-only."""
    rec = tool_manage_case_workflow(
        action="open",
        anomaly_dict=_make_anomaly(anomaly_id="RIO-P2-IND", candidate_uid="CAND-P2-IND").model_dump(),
        summary="Apertura con cambio real de causa indeterminada",
        storage_dir=str(tmp_path / "wf_ind"),
    )
    assert rec["status"] == "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO"

    rec2 = tool_manage_case_workflow(
        action="append_event",
        case_id=rec["case_id"],
        action_type="CAMBIO_ESTADO",
        summary="Cambio real persistente sin causa determinada (protocolo §4.7)",
        new_status="CAMBIO_REAL_INDETERMINADO",
        storage_dir=str(tmp_path / "wf_ind"),
    )
    assert rec2["status"] == "CAMBIO_REAL_INDETERMINADO"

    cases = tool_manage_case_workflow(
        action="list",
        status_filter="CAMBIO_REAL_INDETERMINADO",
        storage_dir=str(tmp_path / "wf_ind"),
    )
    assert cases["count"] == 1

    tool_names = [t.name for t in __import__("asyncio").run(mcp.list_tools())]
    assert "tool_generate_case_file" in tool_names


# ---------------------------------------------------------------------------
# Ficha de caso versionada (Apéndice B del protocolo)
# ---------------------------------------------------------------------------


def test_case_file_schema_defaults_and_atomic_write(tmp_path: Path) -> None:
    anom = _make_anomaly(anomaly_id="RIO-P2-FICHA", candidate_uid="CAND-P2-FICHA")
    feat = coerce_anomaly_feature(anom.model_dump())

    payload = build_case_file(feat)
    assert payload["ficha_version"] == CASE_FILE_SCHEMA_VERSION
    assert payload["identificador"]["candidate_uid"] == "CAND-P2-FICHA"
    assert payload["identificador"]["anomaly_id_corrida"] == "RIO-P2-FICHA"
    assert payload["geometria"]["crs_metrico"] == 5347
    assert payload["deteccion"]["es_probabilidad_estadistica"] is False
    # Los campos humanos quedan pendientes: la ficha nunca los fabrica
    assert payload["revision_humana_independiente"]["ciego_aplicado"] is None
    assert payload["revision_humana_independiente"]["evaluator_id"] is None
    assert payload["resultado_verificacion"]["nivel_evidencia"] is None
    assert payload["resultado_verificacion"]["etiqueta_referencia"] is None
    # Hipótesis por defecto plausibles y no confirmadas
    estados = {h["estado"] for h in payload["hipotesis_alternativas"]}
    assert estados == {"PLAUSIBLE_NO_CONFIRMADA"}
    # Dominio SCL declarado (C1): interior T1 + buffer con fechas de origen
    assert "interior_t1_10m_counts" in payload["deteccion"]["dominio_scl"]
    assert payload["deteccion"]["dominio_scl"]["buffer"]["scope"] == "adyacencia_buffer_40m"

    # Escenas y verificación con datos completos
    payload2 = build_case_file(
        feat,
        scenes=[
            {
                "scene_id": "S2B_20HQK_20230928_0_L2A",
                "date": "2023-09-28",
                "provider": "earth_search",
                "role": "t0",
                "sha256_rasters": {"vnir_10m": "a" * 64},
            }
        ],
        hypotheses=[{"id": "H1", "descripcion": "Extracción", "estado": "BAJA_PLAUSIBILIDAD", "fundamento": "OSM"}],
        review={"ciego_aplicado": True, "evaluator_id": "perito_01", "etiqueta_pre_desvelado": "DINAMICA_NATURAL"},
        verification={"nivel_evidencia": "E0", "etiqueta_referencia": "CAMBIO_REAL_CAUSA_INDETERMINADA"},
    )
    assert payload2["escenas_y_fuentes"][0]["scene_id"] == "S2B_20HQK_20230928_0_L2A"
    assert payload2["escenas_y_fuentes"][0]["sha256_rasters"]["vnir_10m"] == "a" * 64
    assert payload2["revision_humana_independiente"]["ciego_aplicado"] is True
    assert payload2["resultado_verificacion"]["etiqueta_referencia"] == "CAMBIO_REAL_CAUSA_INDETERMINADA"

    # Escritura atómica e idempotente en disco
    out1 = write_case_file(payload2, tmp_path / "reports", "RIO-P2-FICHA")
    assert out1.is_file() and out1.name == "case_file.json"
    on_disk = json.loads(out1.read_text(encoding="utf-8"))
    assert on_disk["ficha_version"] == CASE_FILE_SCHEMA_VERSION
    out2 = write_case_file(payload2, tmp_path / "reports", "RIO-P2-FICHA")
    assert out2 == out1 and json.loads(out2.read_text(encoding="utf-8")) == on_disk


def test_case_file_links_workflow_case(tmp_path: Path) -> None:
    storage = tmp_path / "wf_link"
    anom = _make_anomaly(anomaly_id="RIO-P2-LINK", candidate_uid="CAND-P2-LINK")
    manager = CaseWorkflowManager(storage_dir=storage)
    rec = manager.open_or_get_case(anom)
    manager.append_event(rec.case_id, "NOTA_ANALISTA", "Nota de gabinete", actor="op")
    _, events = manager.get_case(rec.case_id)

    payload = build_case_file(anom, case_record=rec, events=events)
    assert payload["identificador"]["caso_id"] == rec.case_id
    assert payload["workflow"]["estado"] == rec.status
    assert len(payload["workflow"]["eventos"]) == 2
    assert payload["workflow"]["human_review_required"] is True


def test_tool_generate_case_file_end_to_end(tmp_path: Path) -> None:
    anom = _make_anomaly(anomaly_id="RIO-P2-TOOL", candidate_uid="CAND-P2-TOOL")
    res = tool_generate_case_file(
        anomaly_dict=anom.model_dump(),
        output_dir=str(tmp_path / "reports"),
        verification={"nivel_evidencia": "E0", "etiqueta_referencia": "CAMBIO_REAL_CAUSA_INDETERMINADA"},
    )
    assert res["status"] == "success"
    out = Path(res["case_file_path"])
    assert out.is_file() and out.parent.name == "RIO-P2-TOOL"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["resultado_verificacion"]["etiqueta_referencia"] == "CAMBIO_REAL_CAUSA_INDETERMINADA"

    # Vinculación con expediente vía case_id
    rec = tool_manage_case_workflow(
        action="open",
        anomaly_dict=anom.model_dump(),
        storage_dir=str(tmp_path / "wf"),
    )
    res2 = tool_generate_case_file(
        anomaly_dict=anom.model_dump(),
        case_id=rec["case_id"],
        storage_dir=str(tmp_path / "wf"),
        output_dir=str(tmp_path / "reports"),
    )
    payload2 = json.loads(Path(res2["case_file_path"]).read_text(encoding="utf-8"))
    assert payload2["identificador"]["caso_id"] == rec["case_id"]


# ---------------------------------------------------------------------------
# Dominio SCL en incertidumbre (C1): dilatación por fecha con unión idéntica
# ---------------------------------------------------------------------------


_FOREST_SIG = (350.0, 600.0, 400.0, 3800.0, 1400.0, 700.0)
_BRIGHT_SOIL_SIG = (400.0, 500.0, 700.0, 450.0, 900.0, 600.0)
_H, _W = 60, 60
_WEST, _NORTH = 5440500.0, 6356300.0
_CLEARING = (slice(20, 28), slice(22, 30))  # 8x8 px de desmonte en T1


def _run_case(tmp_path: Path, cloud_t0: bool, cloud_t1: bool) -> Dict[str, Any]:
    rr, cc = np.meshgrid(np.arange(_H), np.arange(_W), indexing="ij")
    cube0 = np.zeros((6, _H, _W), dtype=np.float32)
    for b_idx, val in enumerate(_FOREST_SIG):
        cube0[b_idx] = val
    s0 = np.full((_H, _W), 4, dtype=np.int32)
    cube1 = cube0.copy()
    s1 = s0.copy()
    for b_idx, val in enumerate(_BRIGHT_SOIL_SIG):
        cube1[b_idx][_CLEARING] = val
    s1[_CLEARING] = 5

    if cloud_t0:
        cloud0 = (slice(16, 19), slice(22, 25))  # adyacente por arriba del desmonte
        s0[cloud0] = 9
    if cloud_t1:
        cloud1 = (slice(29, 32), slice(22, 25))  # adyacente por abajo del desmonte
        s1[cloud1] = 9

    t0_v, t0_s = _write_local_s2_pair(tmp_path / "t0d", "t0", cube0, s0, _WEST, _NORTH)
    t1_v, t1_s = _write_local_s2_pair(tmp_path / "t1d", "t1", cube1, s1, _WEST, _NORTH)
    return detect_extraction_anomalies(
        t0_vnir_paths=t0_v,
        t0_swir_scl_paths=t0_s,
        t1_vnir_path=t1_v,
        t1_swir_scl_path=t1_s,
        work_dir=tmp_path / "work",
    )


@pytest.mark.parametrize(
    "cloud_t0,cloud_t1,expected_dates",
    [
        (False, True, ["T1"]),
        (True, False, ["T0"]),
        (True, True, ["T0", "T1"]),
    ],
)
def test_uncertainty_declares_cloud_shadow_source_dates(
    tmp_path: Path, cloud_t0: bool, cloud_t1: bool, expected_dates: list[str]
) -> None:
    """La advertencia de adyacencia declara qué fechas aportan (C1), sin alterar resultados."""
    res = _run_case(tmp_path, cloud_t0, cloud_t1)
    assert res.metadata["candidate_count"] == 1
    props = res.features[0]["properties"]
    unc = props["uncertainty"]
    assert unc["cloud_shadow_adjacent"] is True
    assert unc["cloud_shadow_source_dates"] == expected_dates
    # La limitación del detector declara el dominio combinado
    limitations = " ".join(unc["limitations"])
    assert "combinada de T0 y T1" in limitations


def test_uncertainty_without_clouds_has_empty_source_dates(tmp_path: Path) -> None:
    res = _run_case(tmp_path, cloud_t0=False, cloud_t1=False)
    assert res.metadata["candidate_count"] == 1
    unc = res.features[0]["properties"]["uncertainty"]
    assert unc["cloud_shadow_adjacent"] is False
    assert unc["cloud_shadow_source_dates"] == []
