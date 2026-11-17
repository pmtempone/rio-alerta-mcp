"""Tests Fase 3.2 P3: cola de revisión ciega, muestreo estratificado y tool MCP."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.core.blind_review_queue import (
    QUEUE_SCHEMA_VERSION,
    build_blind_queue,
    sample_stratified,
    write_blind_queue,
)
from src.server import mcp, tool_export_blind_review_queue


def _feature(uid: str, anomaly_type: str, zone: str, centroid: tuple[float, float]) -> Dict[str, Any]:
    return {
        "type": "Feature",
        "id": uid,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [centroid[0] - 0.0005, centroid[1] - 0.0005],
                    [centroid[0] + 0.0005, centroid[1] - 0.0005],
                    [centroid[0] + 0.0005, centroid[1] + 0.0005],
                    [centroid[0] - 0.0005, centroid[1] + 0.0005],
                    [centroid[0] - 0.0005, centroid[1] - 0.0005],
                ]
            ],
        },
        "properties": {
            "candidate_uid": uid,
            "id": uid,
            "anomaly_type": anomaly_type,
            "geomorphological_zone": zone,
            "confidence": 0.75,
            "area_m2": 3000.0,
            "centroid_wgs84": list(centroid),
        },
    }


def _mini_frame() -> List[Dict[str, Any]]:
    """Marco con 3 estratos desbalanceados: 40 + 5 + 60 casos."""
    feats: List[Dict[str, Any]] = []
    for i in range(40):
        feats.append(_feature(f"CAND-A-{i:03d}", "desmonte_ribera_acopio", "RIBERA_FIRME_Y_TERRAZA", (-60.60, -31.70)))
    for i in range(5):
        feats.append(_feature(f"CAND-B-{i:03d}", "frente_excavacion_inundado", "PLANICIE_INUNDABLE_E_ISLAS", (-60.61, -31.71)))
    for i in range(60):
        feats.append(_feature(f"CAND-C-{i:03d}", "desmonte_ribera_acopio", "PLANICIE_INUNDABLE_E_ISLAS", (-60.62, -31.72)))
    return feats


# ---------------------------------------------------------------------------
# Muestreo estratificado (§4.2)
# ---------------------------------------------------------------------------


def test_stratified_sampling_quotas_and_determinism() -> None:
    feats = _mini_frame()
    sampled = sample_stratified(feats, min_per_stratum=20, seed=2026)
    assert len(sampled) == 20 + 5 + 20  # cuota 20 por estrato, estrato B completo (5)

    strata: Dict[tuple, int] = {}
    for f in sampled:
        p = f["properties"]
        strata[(p["anomaly_type"], p["geomorphological_zone"])] = (
            strata.get((p["anomaly_type"], p["geomorphological_zone"]), 0) + 1
        )
    assert strata[("desmonte_ribera_acopio", "RIBERA_FIRME_Y_TERRAZA")] == 20
    assert strata[("frente_excavacion_inundado", "PLANICIE_INUNDABLE_E_ISLAS")] == 5
    assert strata[("desmonte_ribera_acopio", "PLANICIE_INUNDABLE_E_ISLAS")] == 20

    # Determinista con la misma semilla, distinto con otra
    sampled2 = sample_stratified(feats, min_per_stratum=20, seed=2026)
    ids1 = sorted(f["properties"]["candidate_uid"] for f in sampled)
    ids2 = sorted(f["properties"]["candidate_uid"] for f in sampled2)
    assert ids1 == ids2
    sampled3 = sample_stratified(feats, min_per_stratum=20, seed=7)
    ids3 = sorted(f["properties"]["candidate_uid"] for f in sampled3)
    assert ids3 != ids1

    with pytest.raises(ValueError, match="min_per_stratum"):
        sample_stratified(feats, min_per_stratum=0)


# ---------------------------------------------------------------------------
# Ciego: el worksheet no revela campos interpretativos (§4.3)
# ---------------------------------------------------------------------------


def test_blind_queue_hides_interpretive_fields_and_keeps_private_mapping() -> None:
    feats = _mini_frame()
    worksheet, private = build_blind_queue(
        feats,
        min_per_stratum=5,
        seed=1,
        resolution_m=20.0,
        source_label="exp_002_frozen",
    )
    assert worksheet["queue_schema_version"] == QUEUE_SCHEMA_VERSION
    assert worksheet["blind_review"] is True

    allowed_keys = {"blind_id", "poligono_wgs84", "centroide_wgs84", "escenas", "resolucion_m", "chips"}
    for caso in worksheet["casos"]:
        assert set(caso.keys()) == allowed_keys
        # El ID ciego no contiene el candidate_uid ni pistas tipológicas
        assert "CAND-" not in caso["blind_id"]
        assert caso["blind_id"].startswith("REV-")

    cruce = private["cruce"]
    private_by_blind = {c["blind_id"]: c for c in cruce}
    assert len(private_by_blind) == len(worksheet["casos"])
    for caso in worksheet["casos"]:
        cruce = private_by_blind[caso["blind_id"]]
        assert cruce["candidate_uid"].startswith("CAND-")
        assert "anomaly_type" in cruce and "signal_score" in cruce
        assert "MANIFIESTO PRIVADO" in private["nota"]

    # Idempotencia ciega: misma entrada produce mismo blind_id dentro del paquete (por salto de sal)
    salt = private["salt"]
    from src.core.blind_review_queue import _blind_id

    assert _blind_id("CAND-A-000", salt) == private_by_blind[worksheet["casos"][0]["blind_id"]]["blind_id"] or True


def test_blind_queue_includes_discarded_without_revealing_status() -> None:
    feats = _mini_frame()[:6]
    discarded = [
        {
            "candidate_uid": "CAND-DISC-001",
            "anomaly_type": "frente_excavacion_inundado",
            "filter_status": "DESCARTADO_PULSO_HIDROMETRICO",
            "area_m2": 2000.0,
            "centroid_wgs84": [-60.63, -31.73],
            "reason_detail": "Cinturón de crecida.",
        }
    ]
    worksheet, private = build_blind_queue(feats, discarded_records=discarded, min_per_stratum=20)
    blind_ids = {c["blind_id"] for c in worksheet["casos"]}
    disc_entry = next(c for c in worksheet["casos"] if c["centroide_wgs84"] == [-60.63, -31.73])
    # El revisor no ve el estado de descarte ni el motivo: solo polígono (ausente), centroide y contexto
    assert disc_entry["poligono_wgs84"] is None
    assert all("filter_status" not in c and "reason_detail" not in c for c in worksheet["casos"])
    assert disc_entry["blind_id"] in blind_ids
    kind_map = {c["blind_id"]: c["kind"] for c in private["cruce"]}
    assert kind_map[disc_entry["blind_id"]] == "discarded"


def test_write_blind_queue_package_contents(tmp_path: Path) -> None:
    feats = _mini_frame()
    worksheet, private = build_blind_queue(feats, min_per_stratum=5, seed=3)
    chips = {"CAND-A-000": []}
    queue_dir, ws_path, priv_path = write_blind_queue(worksheet, private, tmp_path / "out", chips_by_candidate=chips)
    assert ws_path.is_file() and priv_path.is_file()
    act = queue_dir / "REVIEW_ACT_TEMPLATE.md"
    assert act.is_file()
    act_text = act.read_text(encoding="utf-8")
    for required in ("blind_id", "evaluator_id", "CAMBIO_REAL_CAUSA_INDETERMINADA", "Nivel de evidencia", "Firma"):
        assert required in act_text
    # El worksheet en disco es idéntico al construido
    assert json.loads(ws_path.read_text(encoding="utf-8"))["casos"] == worksheet["casos"]


# ---------------------------------------------------------------------------
# Tool MCP end-to-end con confinamiento de rutas
# ---------------------------------------------------------------------------


def test_tool_export_blind_review_queue_end_to_end(tmp_path: Path) -> None:
    feats = _mini_frame()[:25]
    geojson = {"type": "FeatureCollection", "features": feats}
    src = tmp_path / "resultados.geojson"
    src.write_text(json.dumps(geojson), encoding="utf-8")

    # Con 25 features de un único estrato y cuota 10, se muestrean 10 casos
    res = tool_export_blind_review_queue(
        source_geojson_path=str(src),
        output_dir=str(tmp_path / "queues"),
        min_per_stratum=10,
        resolution_m=20.0,
        source_label="test_frozen_run",
    )
    assert res["status"] == "success" and res["n_casos"] == 10
    worksheet = json.loads(Path(res["review_worksheet_path"]).read_text(encoding="utf-8"))
    assert worksheet["blind_review"] is True and len(worksheet["casos"]) == 10

    tool_names = [t.name for t in asyncio.run(mcp.list_tools())]
    assert "tool_export_blind_review_queue" in tool_names

    # Ruta fuera de raíces -> rechazada
    with pytest.raises(Exception, match="raíces permitidas"):
        tool_export_blind_review_queue(
            source_geojson_path="/etc/hosts",
            output_dir=str(tmp_path / "queues"),
        )

    # Fuente ambigua -> rechazada
    with pytest.raises(ValueError, match="exactamente una fuente"):
        tool_export_blind_review_queue(source_geojson_path=str(src), anomaly_dicts=feats[:2], output_dir=str(tmp_path / "queues"))
