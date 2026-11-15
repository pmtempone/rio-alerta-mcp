"""Tests unitarios para validar el cierre de los 8 puntos de la revisión técnica."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from src.config import settings
from src.core.change_detector import detect_extraction_anomalies
from src.core.hydrological_control import compute_turbidity_confounder_flag
from src.core.optical_providers import (
    _item_covers_bbox,
    _read_stac_asset_window_posgar,
    query_stac_seasonal_pair_candidates,
)
from src.schemas import MultitemporalScenePair, SceneMetadata
from src.server import (
    mcp,
    tool_compute_spectral_changes,
    tool_detect_extraction_anomalies,
    tool_search_multitemporal_pair,
)


def test_mcp_tool_search_multitemporal_pair_and_input_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verifica el registro MCP de `tool_search_multitemporal_pair` y las validaciones de entrada."""
    registered_names = [t.name for t in asyncio.run(mcp.list_tools())]
    assert "tool_search_multitemporal_pair" in registered_names

    # Validaciones de entrada en tool_search_multitemporal_pair
    with pytest.raises(ValueError, match="Bounding box inválido"):
        tool_search_multitemporal_pair(
            min_lon=-60.50,
            min_lat=-31.74,
            max_lon=-60.66,
            max_lat=-31.64,
        )
    with pytest.raises(ValueError, match="fuera de rango"):
        tool_search_multitemporal_pair(
            min_lon=-200.0,
            min_lat=-31.74,
            max_lon=-60.53,
            max_lat=-31.64,
        )
    with pytest.raises(ValueError, match="lookback_years"):
        tool_search_multitemporal_pair(
            min_lon=-60.66,
            min_lat=-31.74,
            max_lon=-60.53,
            max_lat=-31.64,
            lookback_years=0,
        )

    captured_calls: List[Dict[str, Any]] = []
    dummy_pair = MultitemporalScenePair(
        status="success",
        t0_scene=SceneMetadata(
            scene_id="S2A_T0_TEST",
            provider="sentinel2_stac",
            acquisition_date="2023-11-08",
            cloud_cover_pct=2.0,
            crs_epsg=5347,
            resolution_m=10.0,
            bands_available=["B02", "B03", "B04", "B08", "B11", "B12", "SCL"],
        ),
        t1_scene=SceneMetadata(
            scene_id="S2B_T1_TEST",
            provider="sentinel2_stac",
            acquisition_date="2026-11-08",
            cloud_cover_pct=1.5,
            crs_epsg=5347,
            resolution_m=10.0,
            bands_available=["B02", "B03", "B04", "B08", "B11", "B12", "SCL"],
        ),
        t0_raster_path=str(tmp_path / "t0_10m.tif"),
        t1_raster_path=str(tmp_path / "t1_10m.tif"),
        t0_swir_raster_path=str(tmp_path / "t0_20m.tif"),
        t1_swir_raster_path=str(tmp_path / "t1_20m.tif"),
        crs_epsg=5347,
        bounds_posgar=(5437000.0, 6487000.0, 5450000.0, 6498000.0),
        resolution_m=10.0,
    )

    def _fake_search_and_fetch(
        bbox_wgs84: tuple[float, float, float, float],
        t1_end_date: str | None = None,
        lookback_years: int = 3,
        out_dir: Path | None = None,
    ) -> MultitemporalScenePair:
        captured_calls.append(
            {
                "bbox_wgs84": bbox_wgs84,
                "t1_end_date": t1_end_date,
                "lookback_years": lookback_years,
                "out_dir": out_dir,
            }
        )
        return dummy_pair

    monkeypatch.setattr("src.server.search_and_fetch_stac_multitemporal", _fake_search_and_fetch)
    res = tool_search_multitemporal_pair(
        min_lon=-60.66,
        min_lat=-31.74,
        max_lon=-60.53,
        max_lat=-31.64,
        t1_end_date="2026-11-08",
        lookback_years=3,
    )
    assert res.status == "success"
    assert res.t1_scene.scene_id == "S2B_T1_TEST"
    assert len(captured_calls) == 1
    assert captured_calls[0]["bbox_wgs84"] == (-60.66, -31.74, -60.53, -31.64)
    assert captured_calls[0]["t1_end_date"] == "2026-11-08"
    assert captured_calls[0]["lookback_years"] == 3

    # Validaciones en tools de detección y cambio espectral
    missing_t0 = str(tmp_path / "no_existe_t0.tif")
    missing_t1 = str(tmp_path / "no_existe_t1.tif")
    with pytest.raises(ValueError, match="min_area_m2"):
        tool_detect_extraction_anomalies(
            t0_path=missing_t0,
            t1_path=missing_t1,
            min_area_m2=0.0,
        )
    with pytest.raises(FileNotFoundError, match="No existe el archivo raster requerido"):
        tool_detect_extraction_anomalies(
            t0_path=missing_t0,
            t1_path=missing_t1,
            min_area_m2=1600.0,
        )
    with pytest.raises(FileNotFoundError, match="No existe el archivo raster requerido"):
        tool_compute_spectral_changes(
            t0_path=missing_t0,
            t1_path=missing_t1,
        )


def test_turbidity_confounder_all_branches_reachable() -> None:
    """Verifica que las tres ramas físicas de `compute_turbidity_confounder_flag` sean alcanzables."""
    mask = np.ones((8, 8), dtype=bool)

    # 1. Agua abierta preexistente en T0 (MNDWI > 0.20) -> True
    assert compute_turbidity_confounder_flag(
        mndwi_t0=np.full((8, 8), 0.35, dtype=np.float32),
        b04_t0=np.full((8, 8), 400.0, dtype=np.float32),
        b04_t1=np.full((8, 8), 410.0, dtype=np.float32),
        candidate_mask=mask,
    )

    # 2. Tierra emergida / barra seca en T0 (MNDWI <= 0.05) -> False incluso con salto fuerte en B04
    assert not compute_turbidity_confounder_flag(
        mndwi_t0=np.full((8, 8), -0.15, dtype=np.float32),
        b04_t0=np.full((8, 8), 500.0, dtype=np.float32),
        b04_t1=np.full((8, 8), 1200.0, dtype=np.float32),
        candidate_mask=mask,
    )

    # 3a. Transición húmeda (0.05 < MNDWI <= 0.20) con pulso de turbidez (|ΔB04| > 150 DN) -> True
    assert compute_turbidity_confounder_flag(
        mndwi_t0=np.full((8, 8), 0.12, dtype=np.float32),
        b04_t0=np.full((8, 8), 500.0, dtype=np.float32),
        b04_t1=np.full((8, 8), 720.0, dtype=np.float32),
        candidate_mask=mask,
    )

    # 3b. Transición húmeda con banco somero brillante en T0 (B04_T0 > 750 DN) -> True
    assert compute_turbidity_confounder_flag(
        mndwi_t0=np.full((8, 8), 0.12, dtype=np.float32),
        b04_t0=np.full((8, 8), 850.0, dtype=np.float32),
        b04_t1=np.full((8, 8), 860.0, dtype=np.float32),
        candidate_mask=mask,
    )

    # 3c. Transición húmeda sin pulso de turbidez ni barra brillante -> False
    assert not compute_turbidity_confounder_flag(
        mndwi_t0=np.full((8, 8), 0.12, dtype=np.float32),
        b04_t0=np.full((8, 8), 500.0, dtype=np.float32),
        b04_t1=np.full((8, 8), 550.0, dtype=np.float32),
        candidate_mask=mask,
    )


def test_deterministic_candidate_uid_and_discarded_candidates_log(
    synthetic_native_s2_scenarios: Dict[str, Dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Verifica ID determinista `candidate_uid`, `signal_score`, 25 umbrales y bitácora de descartados."""
    sc_clear = synthetic_native_s2_scenarios["pos_riparian_clearing"]
    res1 = detect_extraction_anomalies(
        t0_vnir_paths=sc_clear["t0"]["vnir_10m"],
        t1_vnir_path=sc_clear["t1"]["vnir_10m"],
        t0_swir_scl_paths=sc_clear["t0"]["swir_20m"],
        t1_swir_scl_path=sc_clear["t1"]["swir_20m"],
        work_dir=tmp_path / "run1",
    )
    res2 = detect_extraction_anomalies(
        t0_vnir_paths=sc_clear["t0"]["vnir_10m"],
        t1_vnir_path=sc_clear["t1"]["vnir_10m"],
        t0_swir_scl_paths=sc_clear["t0"]["swir_20m"],
        t1_swir_scl_path=sc_clear["t1"]["swir_20m"],
        work_dir=tmp_path / "run2",
    )
    assert len(res1.features) == 1
    assert len(res2.features) == 1
    p1 = res1.features[0]["properties"]
    p2 = res2.features[0]["properties"]
    assert str(p1["candidate_uid"]).startswith("RIO-5347-")
    assert p1["candidate_uid"] == p2["candidate_uid"]
    assert p1["signal_score"] == p1["confidence"]

    cal_params = res1.metadata["calibration_parameters"]
    expected_cal = settings.get_calibration_parameters()
    assert len(expected_cal) == 25
    assert cal_params == expected_cal

    # Registro explícito de descartados en migración natural de bancos
    sc_bar = synthetic_native_s2_scenarios["neg_bar_migration"]
    res_bar = detect_extraction_anomalies(
        t0_vnir_paths=sc_bar["t0"]["vnir_10m"],
        t1_vnir_path=sc_bar["t1"]["vnir_10m"],
        t0_swir_scl_paths=sc_bar["t0"]["swir_20m"],
        t1_swir_scl_path=sc_bar["t1"]["swir_20m"],
        work_dir=tmp_path / "run_bar",
    )
    assert res_bar.metadata["candidate_count"] == 0
    assert res_bar.metadata["discarded_count"] >= 1
    bar_statuses = {d["filter_status"] for d in res_bar.metadata["discarded_candidates"]}
    assert "DESCARTADO_MIGRACION_BARRA" in bar_statuses

    # Registro explícito de descartados en crecida hidrométrica uniforme
    sc_flood = synthetic_native_s2_scenarios["neg_uniform_stage_rise"]
    res_flood = detect_extraction_anomalies(
        t0_vnir_paths=sc_flood["t0"]["vnir_10m"],
        t1_vnir_path=sc_flood["t1"]["vnir_10m"],
        t0_swir_scl_paths=sc_flood["t0"]["swir_20m"],
        t1_swir_scl_path=sc_flood["t1"]["swir_20m"],
        work_dir=tmp_path / "run_flood",
    )
    assert res_flood.metadata["candidate_count"] == 0
    assert res_flood.metadata["discarded_count"] >= 1
    flood_statuses = {d["filter_status"] for d in res_flood.metadata["discarded_candidates"]}
    assert "DESCARTADO_PULSO_HIDROMETRICO" in flood_statuses


def test_stac_network_query_and_resolution_degradation_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Testea `query_stac_seasonal_pair_candidates`, `_item_covers_bbox` y warning de degradación."""
    aoi = (-60.66, -31.74, -60.53, -31.64)

    class _FakeItem:
        def __init__(self, item_id: str, dt_iso: str, cloud: float, bbox: List[float]) -> None:
            self.id = item_id
            self.datetime = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            self.properties = {"datetime": dt_iso, "eo:cloud_cover": cloud}
            self.bbox = bbox

    full_bbox = [-60.90, -32.60, -59.70, -31.59]
    partial_bbox = [-60.90, -31.70, -59.70, -30.69]

    t1_partial = _FakeItem("S2B_T1_PARTIAL", "2026-11-12T14:10:00Z", 2.0, partial_bbox)
    t1_full = _FakeItem("S2A_T1_FULL", "2026-11-08T14:10:00Z", 5.0, full_bbox)
    t0_yr1_dup = _FakeItem("S2A_T0_2025", "2025-11-09T14:10:00Z", 4.0, full_bbox)
    t0_yr2 = _FakeItem("S2A_T0_2024", "2024-11-06T14:10:00Z", 1.0, full_bbox)

    assert _item_covers_bbox(t1_full, aoi) is True
    assert _item_covers_bbox(t1_partial, aoi) is False

    class _FakeSearch:
        def __init__(self, items: List[_FakeItem]) -> None:
            self._items = items

        def items(self) -> List[_FakeItem]:
            return self._items

    class _StatefulFakeClient:
        def __init__(self) -> None:
            self.calls: List[Dict[str, Any]] = []

        def search(self, **kwargs: Any) -> _FakeSearch:
            self.calls.append(kwargs)
            idx = len(self.calls)
            if idx == 1:
                # Ventana reciente 180d vacía -> fuerza fallback de 365d
                return _FakeSearch([])
            if idx == 2:
                return _FakeSearch([t1_partial, t1_full])
            if idx == 3:
                return _FakeSearch([t1_full, t0_yr1_dup])
            return _FakeSearch([t0_yr1_dup, t0_yr2])

    end_dt = datetime(2026, 11, 15, tzinfo=timezone.utc)
    fake_client = _StatefulFakeClient()
    t1_sel, t0_list, recent_cnt = query_stac_seasonal_pair_candidates(
        client=fake_client,
        bbox_wgs84=aoi,
        end_dt=end_dt,
        lookback_years=2,
        window_days=45,
        max_cloud_cover_pct=20.0,
    )
    assert len(fake_client.calls) == 4
    assert recent_cnt == 2
    assert t1_sel is not None and t1_sel.id == "S2A_T1_FULL"
    assert [it.id for it in t0_list] == ["S2A_T0_2024", "S2A_T0_2025"]
    assert all((t1_sel.datetime - it.datetime) >= timedelta(days=300) for it in t0_list)

    # Verificar WARNING cuando el recorte solicitado supera settings.max_master_side_px
    west, north = 5440500.0, 6356300.0
    src_tif = tmp_path / "wide_posgar.tif"
    with rasterio.open(
        src_tif,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(5347),
        transform=from_origin(west, north, 10.0, 10.0),
        nodata=0.0,
    ) as dst:
        dst.write(np.full((1, 100, 100), 1234.0, dtype=np.float32))

    monkeypatch.setattr(settings, "max_master_side_px", 32)
    with caplog.at_level(logging.WARNING):
        arr, _, out_shape = _read_stac_asset_window_posgar(
            href=str(src_tif),
            bounds_posgar=(west, north - 1000.0, west + 1000.0, north),
            dst_crs_epsg=5347,
            dst_res_m=10.0,
            resampling_method="bilinear",
        )
    assert out_shape == (32, 32)
    assert arr.shape == (32, 32)
    assert any("max_master_side_px" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records)
