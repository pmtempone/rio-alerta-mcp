"""Tests unitarios para validar el cierre de los 8 puntos de la revisión técnica."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from src.config import settings
from src.core.change_detector import (
    _build_coarse_location_uid,
    _collect_filtered_components,
    detect_extraction_anomalies,
)
from src.core.hydrological_control import compute_turbidity_confounder_flag, fetch_ina_stage_height
from src.core.optical_providers import (
    _item_covers_bbox,
    _read_stac_asset_window_posgar,
    query_stac_seasonal_pair_candidates,
)
from src.schemas import ChangeDetectionResult, MultitemporalScenePair, SceneMetadata
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
    with pytest.raises(ValueError, match="t1_end_date inválido"):
        tool_search_multitemporal_pair(
            min_lon=-60.66,
            min_lat=-31.74,
            max_lon=-60.53,
            max_lat=-31.64,
            t1_end_date="fecha-invalida",
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
    with pytest.raises(ValueError, match="min_clearing_area_m2"):
        tool_detect_extraction_anomalies(
            t0_path=missing_t0,
            t1_path=missing_t1,
            min_clearing_area_m2=0.0,
        )
    with pytest.raises(ValueError, match="max_discarded_records"):
        tool_detect_extraction_anomalies(
            t0_path=missing_t0,
            t1_path=missing_t1,
            max_discarded_records=0,
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

    # Verifica passthrough de min_clearing_area_m2, stage_t0_m, stage_t1_m y max_discarded_records
    dummy_t0 = tmp_path / "dummy_t0.tif"
    dummy_t1 = tmp_path / "dummy_t1.tif"
    dummy_t0.write_bytes(b"tif")
    dummy_t1.write_bytes(b"tif")
    det_calls: List[Dict[str, Any]] = []

    def _fake_detect(**kwargs: Any) -> ChangeDetectionResult:
        det_calls.append(kwargs)
        return ChangeDetectionResult(
            type="FeatureCollection",
            crs={"type": "name", "properties": {"name": "OGC:CRS84"}},
            metadata={"candidate_count": 0},
            features=[],
        )

    monkeypatch.setattr("src.server.detect_extraction_anomalies", _fake_detect)
    tool_detect_extraction_anomalies(
        t0_path=str(dummy_t0),
        t1_path=str(dummy_t1),
        min_area_m2=1600.0,
        min_clearing_area_m2=750.0,
        stage_t0_m=1.25,
        stage_t1_m=4.60,
        max_discarded_records=50,
    )
    assert len(det_calls) == 1
    assert det_calls[0]["min_clearing_area_m2"] == 750.0
    assert det_calls[0]["stage_t0_m"] == 1.25
    assert det_calls[0]["stage_t1_m"] == 4.60
    assert det_calls[0]["max_discarded_records"] == 50


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
    assert str(p1["coarse_location_uid"]).startswith("RIO-CELL100-5347-")
    assert _build_coarse_location_uid(5347, 5446607.0, 6490103.0) == _build_coarse_location_uid(
        5347, 5446628.0, 6490119.0
    )
    assert p1["signal_score"] == p1["confidence"]

    cal_params = res1.metadata["calibration_parameters"]
    expected_cal = settings.get_calibration_parameters()
    assert len(expected_cal) == 42
    assert cal_params == expected_cal
    assert res1.metadata["min_clearing_area_m2"] == 800.0

    # Dos corridas con stems distintos en el mismo work_dir generan bitácoras separadas sin pisarse
    import shutil

    shared_work = tmp_path / "shared_work"
    alt_dir = tmp_path / "alt_stems"
    alt_dir.mkdir(parents=True, exist_ok=True)
    alt_t0_v = alt_dir / "t0_epoch_b_vnir10m.tif"
    alt_t0_s = alt_dir / "t0_epoch_b_swir20m.tif"
    alt_t1_v = alt_dir / "t1_epoch_b_vnir10m.tif"
    alt_t1_s = alt_dir / "t1_epoch_b_swir20m.tif"
    shutil.copy2(sc_clear["t0"]["vnir_10m"], alt_t0_v)
    shutil.copy2(sc_clear["t0"]["swir_20m"], alt_t0_s)
    shutil.copy2(sc_clear["t1"]["vnir_10m"], alt_t1_v)
    shutil.copy2(sc_clear["t1"]["swir_20m"], alt_t1_s)

    res_shared_a = detect_extraction_anomalies(
        t0_vnir_paths=sc_clear["t0"]["vnir_10m"],
        t1_vnir_path=sc_clear["t1"]["vnir_10m"],
        t0_swir_scl_paths=sc_clear["t0"]["swir_20m"],
        t1_swir_scl_path=sc_clear["t1"]["swir_20m"],
        work_dir=shared_work,
    )
    res_shared_b = detect_extraction_anomalies(
        t0_vnir_paths=alt_t0_v,
        t1_vnir_path=alt_t1_v,
        t0_swir_scl_paths=alt_t0_s,
        t1_swir_scl_path=alt_t1_s,
        work_dir=shared_work,
    )
    log_a = Path(res_shared_a.metadata["discarded_log_path"])
    log_b = Path(res_shared_b.metadata["discarded_log_path"])
    assert log_a.name.startswith("discarded_candidates_")
    assert log_b.name.startswith("discarded_candidates_")
    assert log_a != log_b
    assert log_a.exists() and log_b.exists()

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

    # Registro explícito de descartados en crecida hidrométrica uniforme + truncamiento y persistencia JSON
    sc_flood = synthetic_native_s2_scenarios["neg_uniform_stage_rise"]
    res_flood = detect_extraction_anomalies(
        t0_vnir_paths=sc_flood["t0"]["vnir_10m"],
        t1_vnir_path=sc_flood["t1"]["vnir_10m"],
        t0_swir_scl_paths=sc_flood["t0"]["swir_20m"],
        t1_swir_scl_path=sc_flood["t1"]["swir_20m"],
        work_dir=tmp_path / "run_flood",
        max_discarded_records=1,
    )
    assert res_flood.metadata["candidate_count"] == 0
    assert res_flood.metadata["discarded_count"] >= 1
    assert len(res_flood.metadata["discarded_candidates"]) <= 1
    assert res_flood.metadata["discarded_truncated"] is (res_flood.metadata["discarded_count"] > 1)
    log_path = Path(res_flood.metadata["discarded_log_path"])
    assert log_path.exists()
    full_log = json.loads(log_path.read_text(encoding="utf-8"))
    assert len(full_log) == res_flood.metadata["discarded_count"]
    flood_statuses = {d["filter_status"] for d in full_log}
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


def _write_local_s2_pair(
    out_dir: Path,
    prefix: str,
    cube: np.ndarray,
    scl: np.ndarray,
    west: float,
    north: float,
) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    h10, w10 = cube.shape[1], cube.shape[2]
    h20, w20 = h10 // 2, w10 // 2
    vnir_path = out_dir / f"{prefix}_vnir10m.tif"
    swir_path = out_dir / f"{prefix}_swir_scl20m.tif"
    with rasterio.open(
        vnir_path,
        "w",
        driver="GTiff",
        height=h10,
        width=w10,
        count=4,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(5347),
        transform=from_origin(west, north, 10.0, 10.0),
        nodata=-9999.0,
    ) as dst:
        dst.write(cube[:4].astype(np.float32))
        dst.descriptions = ("B02", "B03", "B04", "B08")

    b11_20 = cube[4].reshape(h20, 2, w20, 2).mean(axis=(1, 3)).astype(np.float32)
    b12_20 = cube[5].reshape(h20, 2, w20, 2).mean(axis=(1, 3)).astype(np.float32)
    scl_20 = scl[::2, ::2].astype(np.float32)
    with rasterio.open(
        swir_path,
        "w",
        driver="GTiff",
        height=h20,
        width=w20,
        count=3,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(5347),
        transform=from_origin(west, north, 20.0, 20.0),
        nodata=-9999.0,
    ) as dst:
        dst.write(np.stack([b11_20, b12_20, scl_20], axis=0))
        dst.descriptions = ("B11", "B12", "SCL")
    return vnir_path, swir_path


def test_discard_attribution_ignores_cloud_adjacency_and_ina_wrapper(tmp_path: Path) -> None:
    """Verifica atribución exacta de descartes, buffer de medio píxel, simetría y wrapper INA."""
    from pyproj import CRS, Transformer

    height, width = 80, 80
    west, north = 5440500.0, 6356300.0
    rr, cc = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")

    forest_sig = (350.0, 600.0, 400.0, 3800.0, 1400.0, 700.0)
    water_sig = (700.0, 900.0, 600.0, 250.0, 150.0, 120.0)
    dark_soil_sig = (250.0, 300.0, 380.0, 280.0, 500.0, 350.0)
    clearing_sig = (1800.0, 2200.0, 2700.0, 2400.0, 3600.0, 3100.0)
    wet_bar_t0_sig = (500.0, 900.0, 600.0, 700.0, 700.0, 500.0)
    turbid_water_sig = (700.0, 950.0, 950.0, 250.0, 150.0, 120.0)

    c0 = np.zeros((6, height, width), dtype=np.float32)
    s0 = np.full((height, width), 4, dtype=np.int32)
    for b_idx, val in enumerate(forest_sig):
        c0[b_idx] = val

    channel = (cc >= 2) & (cc < 18)
    for b_idx, val in enumerate(water_sig):
        c0[b_idx][channel] = val
    s0[channel] = 6

    # Barra húmeda en T0 (MNDWI_T0 = 0.125 en franja 0.05..0.20) situada en ribera_firme
    wet_bar_patch = (rr >= 34) & (rr < 42) & (cc >= 50) & (cc < 58)
    for b_idx, val in enumerate(wet_bar_t0_sig):
        c0[b_idx][wet_bar_patch] = val
    s0[wet_bar_patch] = 5

    c1, s1 = c0.copy(), s0.copy()

    # 1. Parche de desmonte que falla compuerta mineral (B11=500 < 600 DN) con SCL=5 y nube adyacente a <40m
    mineral_patch = (rr >= 20) & (rr < 28) & (cc >= 22) & (cc < 30)
    for b_idx, val in enumerate(dark_soil_sig):
        c1[b_idx][mineral_patch] = val
    s1[mineral_patch] = 5
    # Nube en T1 a 20m del borde del parche (dilatación 40m lo solapa, pero SCL=9 está fuera del parche)
    s1[(rr >= 18) & (rr < 20) & (cc >= 22) & (cc < 24)] = 9

    # 2. Barra húmeda recibe pulso de turbidez en T1 (|ΔB04| = 350 > 150 DN)
    for b_idx, val in enumerate(turbid_water_sig):
        c1[b_idx][wet_bar_patch] = val
    s1[wet_bar_patch] = 6

    # 3. Parche estrecho con cuello de 1 píxel en ribera_firme (antes degeneraba y caía < 1600 m², ahora >= 800 m²)
    neck_clearing = (
        ((rr >= 50) & (rr < 54) & (cc >= 22) & (cc < 24))
        | ((rr == 52) & (cc >= 24) & (cc < 28))
        | ((rr >= 51) & (rr < 55) & (cc >= 28) & (cc < 30))
    )
    for b_idx, val in enumerate(clearing_sig):
        c1[b_idx][neck_clearing] = val
    s1[neck_clearing] = 5

    # 4. Parche pequeño (2x2 = 400 m² < 800 m²) -> DESCARTADO_AREA_MINIMA
    small_clearing = (rr >= 10) & (rr < 12) & (cc >= 50) & (cc < 52)
    for b_idx, val in enumerate(clearing_sig):
        c1[b_idx][small_clearing] = val
    s1[small_clearing] = 5

    # 5. Tira degenerada de 1x2 píxeles (< 3 vértices de contorno) -> DESCARTADO_GEOMETRIA_DEGENERADA
    deg_strip = (rr == 68) & (cc >= 50) & (cc < 52)
    for b_idx, val in enumerate(clearing_sig):
        c1[b_idx][deg_strip] = val
    s1[deg_strip] = 5

    t0_v, t0_s = _write_local_s2_pair(tmp_path / "attr_t0", "t0", c0, s0, west, north)
    t1_v, t1_s = _write_local_s2_pair(tmp_path / "attr_t1", "t1", c1, s1, west, north)

    res = detect_extraction_anomalies(
        t0_vnir_paths=t0_v,
        t1_vnir_path=t1_v,
        t0_swir_scl_paths=t0_s,
        t1_swir_scl_path=t1_s,
        work_dir=tmp_path / "attr_work",
    )
    assert res.metadata["candidate_count"] == 1
    assert res.features[0]["properties"]["anomaly_type"] == "desmonte_ribera_acopio"
    assert res.features[0]["properties"]["area_m2"] >= 800.0

    statuses = [d["filter_status"] for d in res.metadata["discarded_candidates"]]
    assert "DESCARTADO_COMPUERTA_MINERAL" in statuses
    assert "DESCARTADO_NUBE_SOMBRA" not in statuses
    assert "DESCARTADO_CONFUSOR_TURBIDEZ" in statuses
    assert "DESCARTADO_AREA_MINIMA" in statuses
    assert "DESCARTADO_GEOMETRIA_DEGENERADA" in statuses

    # Simetría check_bar_migration=False para desmontes solapados con migración de barra y crecida
    raw_m = np.zeros((20, 20), dtype=bool)
    raw_m[4:10, 4:10] = True
    final_m = np.zeros((20, 20), dtype=bool)
    empty_m = np.zeros((20, 20), dtype=bool)
    bar_m = raw_m.copy()
    flood_m = raw_m.copy()
    tf = from_origin(west, north, 10.0, 10.0)
    to_wgs = Transformer.from_crs(CRS.from_epsg(5347), CRS.from_epsg(4326), always_xy=True)

    rec_clearing = _collect_filtered_components(
        raw_mask=raw_m,
        final_mask=final_m,
        anomaly_type="desmonte_ribera_acopio",
        transform=tf,
        crs_epsg=5347,
        to_wgs=to_wgs,
        pixel_area_m2=100.0,
        min_area_m2=800.0,
        bar_migration_mask=bar_m,
        uniform_stage_belt_mask=flood_m,
        drawdown_belt_mask=empty_m,
        flood_zone_excl=empty_m,
        scl_mineral_gate=np.ones((20, 20), dtype=bool),
        check_mineral_gate=True,
        check_bar_migration=False,
    )
    assert len(rec_clearing) == 1
    assert rec_clearing[0].filter_status == "DESCARTADO_PULSO_HIDROMETRICO"

    rec_excav = _collect_filtered_components(
        raw_mask=raw_m,
        final_mask=final_m,
        anomaly_type="frente_excavacion_inundado",
        transform=tf,
        crs_epsg=5347,
        to_wgs=to_wgs,
        pixel_area_m2=100.0,
        min_area_m2=800.0,
        bar_migration_mask=bar_m,
        uniform_stage_belt_mask=flood_m,
        drawdown_belt_mask=empty_m,
        flood_zone_excl=empty_m,
        scl_mineral_gate=np.ones((20, 20), dtype=bool),
        check_mineral_gate=False,
        check_bar_migration=True,
    )
    assert len(rec_excav) == 1
    assert rec_excav[0].filter_status == "DESCARTADO_MIGRACION_BARRA"

    # Cobertura de wrapper fetch_ina_stage_height (dict payload y ConnectError)
    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.json.return_value = {"data": [{"valor": 3.12}]}
    with patch("src.core.hydrological_control.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_ok
        assert fetch_ina_stage_height(30, "2026-11-08") == 3.12

    with patch("src.core.hydrological_control.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("offline")
        assert fetch_ina_stage_height(30, "2026-11-08") is None
