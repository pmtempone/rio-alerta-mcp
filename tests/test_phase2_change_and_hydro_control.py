"""Tests científicos de Fase 2: resoluciones nativas 10m/20m, máscara SCL, controles hidrológicos y benchmark."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import rasterio
from rasterio.transform import from_origin

from src.core.change_detector import (
    compute_multitemporal_baseline_and_changes,
    detect_extraction_anomalies,
)
from src.core.crs_manager import align_raster_to_reference, ensure_metric_crs
from src.core.optical_providers import (
    _item_bbox_coverage_fraction,
    _item_covers_bbox,
    decode_scl_masks,
    filter_seasonally_comparable_items,
    load_native_s2_scene_bundle,
    query_stac_seasonal_pair_candidates,
)
from src.core.parana_pilot_recon import (
    PARANA_MID_PILOT_BBOX_WGS84,
    evaluate_parana_pilot_readiness,
)
from src.schemas import UncertaintyMetadata
from src.server import (
    tool_compute_spectral_changes,
    tool_detect_extraction_anomalies,
    tool_evaluate_parana_pilot,
)


def test_nearest_resampling_preserves_scl_classes_and_alignment(tmp_path: Path) -> None:
    """Verifica que SCL se remuestree con nearest (sin interpolar códigos) y se co-registre al píxel."""
    wgs_scl = tmp_path / "scl_wgs.tif"
    metric_scl = tmp_path / "scl_posgar.tif"
    scl_arr = np.full((10, 10), 4, dtype=np.uint8)
    scl_arr[2:5, 2:5] = 9  # Nube alta
    scl_arr[5:8, 5:8] = 3  # Sombra de nube

    with rasterio.open(
        wgs_scl,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(-60.65, -32.92, 0.0002, 0.0002),
        nodata=0,
    ) as dst:
        dst.write(scl_arr, 1)

    out_path = ensure_metric_crs(wgs_scl, metric_scl, resampling_method="nearest")
    with rasterio.open(out_path) as src:
        vals = set(np.unique(src.read(1)).tolist())
        assert vals.issubset({0, 3, 4, 9})
        assert 6 not in vals  # Jamás promediar (3+9)/2 = 6

    # Verificar co-registro exacto align_raster_to_reference entre 20 m y 10 m
    ref_10m = tmp_path / "ref_10m.tif"
    src_20m = tmp_path / "src_20m.tif"
    aligned_20m = tmp_path / "aligned_10m.tif"
    with rasterio.open(
        ref_10m,
        "w",
        driver="GTiff",
        height=20,
        width=20,
        count=1,
        dtype="float32",
        crs="EPSG:5347",
        transform=from_origin(5440500.0, 6356300.0, 10.0, 10.0),
    ) as dst:
        dst.write(np.ones((1, 20, 20), dtype=np.float32))

    with rasterio.open(
        src_20m,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="uint8",
        crs="EPSG:5347",
        transform=from_origin(5440500.0, 6356300.0, 20.0, 20.0),
    ) as dst:
        scl20 = np.full((1, 10, 10), 5, dtype=np.uint8)
        scl20[0, 2:4, 2:4] = 8
        dst.write(scl20)

    align_raster_to_reference(src_20m, ref_10m, aligned_20m, resampling_method="nearest")
    with rasterio.open(aligned_20m) as al, rasterio.open(ref_10m) as rf:
        assert al.shape == rf.shape
        assert al.transform.almost_equals(rf.transform, precision=1e-6)
        assert set(np.unique(al.read(1)).tolist()) == {5, 8}


def test_decode_scl_masks_and_native_bundle_loading(
    synthetic_native_s2_scenarios: Dict[str, Dict[str, Any]],
) -> None:
    """Valida decodificación estricta de clases SCL y lectura combinada 10 m VNIR + 20 m SWIR/SCL."""
    scl_test = np.array([[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]], dtype=np.int32)
    valid, cloud_shadow = decode_scl_masks(scl_test)
    expected_valid = np.array(
        [[False, False, False, False], [True, True, True, True], [False, False, False, False]]
    )
    expected_cs = np.array(
        [[False, False, True, True], [False, False, False, False], [True, True, True, False]]
    )
    assert np.array_equal(valid, expected_valid)
    assert np.array_equal(cloud_shadow, expected_cs)

    sc_cloud = synthetic_native_s2_scenarios["neg_cloud_shadow_scl"]
    bundle = load_native_s2_scene_bundle(sc_cloud["t1"]["vnir_10m"], sc_cloud["t1"]["swir_20m"])
    assert bundle["crs_epsg"] == 5347
    assert bundle["grid_resolution_m"] == 10.0
    assert bundle["swir_native_resolution_m"] == 20.0
    assert bundle["valid_pixel_fraction"] < 1.0
    assert np.any(bundle["cloud_or_shadow_mask"])
    assert np.all(np.isnan(bundle["B11"][~bundle["valid_mask"]]))


def test_seasonal_stac_item_filtering() -> None:
    """Verifica selección de línea base T0 en ventana estacional comparable (±45 días del año)."""
    items = [
        {"id": "T0_same_season_2024", "properties": {"datetime": "2024-03-10T14:00:00Z", "eo:cloud_cover": 5.0}},
        {"id": "T0_same_season_2025", "properties": {"datetime": "2025-04-05T14:00:00Z", "eo:cloud_cover": 8.0}},
        {"id": "T0_opposite_season", "properties": {"datetime": "2024-09-15T14:00:00Z", "eo:cloud_cover": 1.0}},
        {"id": "T0_too_cloudy", "properties": {"datetime": "2024-03-12T14:00:00Z", "eo:cloud_cover": 45.0}},
        {"id": "T1_almost_same_day", "properties": {"datetime": "2026-03-14T14:00:00Z", "eo:cloud_cover": 2.0}},
    ]
    selected = filter_seasonally_comparable_items(
        items=items,
        reference_date_iso="2026-03-15",
        window_days=45,
        max_cloud_cover_pct=20.0,
    )
    selected_ids = [it["id"] for it in selected]
    assert selected_ids == ["T0_same_season_2024", "T0_same_season_2025"]


def test_multitemporal_baseline_median_ignores_clouded_t0_scene(
    synthetic_native_s2_scenarios: Dict[str, Dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Verifica que una serie T0 de múltiples escenas recupere la mediana válida ignorando nubes en una escena."""
    sc_pos = synthetic_native_s2_scenarios["pos_flooded_pit"]
    sc_cloud = synthetic_native_s2_scenarios["neg_cloud_shadow_scl"]

    res = compute_multitemporal_baseline_and_changes(
        t0_vnir_paths=[sc_pos["t0"]["vnir_10m"], sc_cloud["t1"]["vnir_10m"]],
        t0_swir_scl_paths=[sc_pos["t0"]["swir_20m"], sc_cloud["t1"]["swir_20m"]],
        t1_vnir_path=sc_pos["t1"]["vnir_10m"],
        t1_swir_scl_path=sc_pos["t1"]["swir_20m"],
        out_dir=tmp_path / "proc",
    )
    assert res.status == "success"
    assert res.effective_resolution_swir_m == 20.0
    assert Path(res.delta_mndwi_path).exists()
    assert res.stats["delta_mndwi_max"] > 0.70


def test_phase2_natural_vs_extraction_benchmark(
    synthetic_native_s2_scenarios: Dict[str, Dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Benchmark cuantitativo sobre 6 escenarios (2 positivos vs 4 negativos naturales/atmosféricos).

    Exige Precision >= 0.85, Recall >= 0.85 y 0 falsos positivos en todos los escenarios negativos.
    """
    tp = 0
    fp = 0
    fn = 0
    negative_fp = 0

    per_scenario_counts: Dict[str, int] = {}
    for name, sc in synthetic_native_s2_scenarios.items():
        det = detect_extraction_anomalies(
            t0_vnir_paths=sc["t0"]["vnir_10m"],
            t0_swir_scl_paths=sc["t0"]["swir_20m"],
            t1_vnir_path=sc["t1"]["vnir_10m"],
            t1_swir_scl_path=sc["t1"]["swir_20m"],
            min_area_m2=1600.0,
            work_dir=tmp_path / f"work_{name}",
        )
        feats = det.features
        per_scenario_counts[name] = len(feats)

        if sc["expected_positive"]:
            matched = [f for f in feats if f["properties"]["anomaly_type"] == sc["expected_type"]]
            if matched:
                tp += 1
                fp += max(0, len(feats) - 1)
                props = matched[0]["properties"]
                assert props["effective_resolution_m"] == 20.0
                assert props["area_m2"] >= 1600.0
                assert props["natural_filter_status"] == "CANDIDATO_RESIDUAL"
                unc = UncertaintyMetadata.model_validate(props["uncertainty"])
                assert unc.effective_resolution_m == 20.0
                assert unc.valid_pixel_fraction > 0.7
            else:
                fn += 1
                fp += len(feats)
        else:
            fp += len(feats)
            negative_fp += len(feats)

    precision = tp / float(tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / float(tp + fn) if (tp + fn) > 0 else 0.0

    assert negative_fp == 0, f"Falsos positivos en escenarios naturales/SCL: {per_scenario_counts}"
    assert precision >= 0.85, f"Precision insuficiente ({precision}): {per_scenario_counts}"
    assert recall >= 0.85, f"Recall insuficiente ({recall}): {per_scenario_counts}"


def test_parana_pilot_readiness_and_phase2_mcp_tools(
    synthetic_native_s2_scenarios: Dict[str, Dict[str, Any]],
) -> None:
    """Valida evaluación piloto Río Paraná Medio (offline y mock STAC) y herramientas MCP de Fase 2."""
    offline_info = evaluate_parana_pilot_readiness(offline_mode=True)
    assert offline_info["crs_epsg"] == 5347
    assert offline_info["bbox_wgs84"] == PARANA_MID_PILOT_BBOX_WGS84
    assert offline_info["stac_readiness"]["status"] == "offline_skipped"
    assert len(offline_info["institutional_gaps"]) >= 1  # No inventa permisos si no hay permits.gpkg

    class _FakeItem:
        def __init__(self, item_id: str, dt_iso: str, cloud: float) -> None:
            self.id = item_id
            self.datetime = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            self.properties = {"datetime": dt_iso, "eo:cloud_cover": cloud}

    class _FakeSearch:
        def __init__(self, items: List[_FakeItem]) -> None:
            self._items = items

        def items(self) -> List[_FakeItem]:
            return self._items

    class _FakeClient:
        def search(self, **kwargs: Any) -> _FakeSearch:
            return _FakeSearch(
                [
                    _FakeItem("S2B_20260318", "2026-03-18T14:00:00Z", 3.5),
                    _FakeItem("S2A_20250320", "2025-03-20T14:00:00Z", 6.0),
                    _FakeItem("S2A_20240310", "2024-03-10T14:00:00Z", 4.0),
                ]
            )

    mocked_info = evaluate_parana_pilot_readiness(offline_mode=False, stac_client=_FakeClient())
    assert mocked_info["stac_readiness"]["status"] == "ready"
    assert mocked_info["stac_readiness"]["comparable_t0_count"] == 2

    sc = synthetic_native_s2_scenarios["pos_riparian_clearing"]
    spec_res = tool_compute_spectral_changes(
        t0_path=str(sc["t0"]["vnir_10m"]),
        t1_path=str(sc["t1"]["vnir_10m"]),
        t0_swir_20m_path=str(sc["t0"]["swir_20m"]),
        t1_swir_20m_path=str(sc["t1"]["swir_20m"]),
    )
    assert spec_res.status == "success"
    assert spec_res.effective_resolution_swir_m == 20.0

    det_res = tool_detect_extraction_anomalies(
        t0_path=str(sc["t0"]["vnir_10m"]),
        t1_path=str(sc["t1"]["vnir_10m"]),
        t0_swir_20m_path=str(sc["t0"]["swir_20m"]),
        t1_swir_20m_path=str(sc["t1"]["swir_20m"]),
    )
    assert len(det_res.features) == 1
    assert det_res.features[0]["properties"]["anomaly_type"] == "desmonte_ribera_acopio"

    pilot_tool_out = tool_evaluate_parana_pilot(offline_mode=True)
    assert pilot_tool_out["crs_epsg"] == 5347


def test_stac_coverage_prioritizes_full_aoi_over_edge_tile() -> None:
    """Verifica que una celda MGRS completa tenga prioridad sobre un borde truncado más reciente."""
    aoi = (-60.66, -31.74, -60.53, -31.64)

    class _ItemWithBbox:
        def __init__(self, item_id: str, dt_iso: str, cloud: float, bbox: List[float]) -> None:
            self.id = item_id
            self.datetime = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            self.properties = {"datetime": dt_iso, "eo:cloud_cover": cloud}
            self.bbox = bbox

    edge_recent = _ItemWithBbox(
        "S2B_20JQL_20261111_0_L2A",
        "2026-11-11T14:11:00Z",
        6.0,
        [-60.9118, -31.7058, -59.7326, -30.6922],
    )
    full_t1 = _ItemWithBbox(
        "S2A_20HQK_20261108_1_L2A",
        "2026-11-08T14:11:00Z",
        7.8,
        [-60.8919, -32.6075, -59.7003, -31.5933],
    )
    edge_t0 = _ItemWithBbox(
        "S2A_20JQL_20241106_0_L2A",
        "2024-11-06T14:11:00Z",
        0.01,
        [-60.9118, -31.7058, -59.7326, -30.6922],
    )
    full_t0 = _ItemWithBbox(
        "S2A_20HQK_20241106_0_L2A",
        "2024-11-06T14:11:00Z",
        1.7,
        [-60.8919, -32.6075, -59.7003, -31.5933],
    )

    assert _item_bbox_coverage_fraction(edge_recent, aoi) < 0.70
    assert not _item_covers_bbox(edge_recent, aoi)
    assert _item_covers_bbox(full_t1, aoi)

    class _FakeSearch:
        def __init__(self, items: List[_ItemWithBbox]) -> None:
            self._items = items

        def items(self) -> List[_ItemWithBbox]:
            return self._items

    class _FakeClient:
        def search(self, **kwargs: Any) -> _FakeSearch:
            dt = kwargs.get("datetime", "")
            if dt.startswith("2026-"):
                return _FakeSearch([edge_recent, full_t1])
            return _FakeSearch([edge_t0, full_t0])

    t1_sel, t0_list, _ = query_stac_seasonal_pair_candidates(
        client=_FakeClient(),
        bbox_wgs84=aoi,
        end_dt=datetime(2026, 11, 14, tzinfo=timezone.utc),
        lookback_years=2,
    )
    assert t1_sel is not None
    assert t1_sel.id == "S2A_20HQK_20261108_1_L2A"
    assert [it.id for it in t0_list] == ["S2A_20HQK_20241106_0_L2A"]
