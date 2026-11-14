"""Fixtures sintéticos multitemporales en POSGAR 2007 (EPSG:5347) con resoluciones nativas 10 m / 20 m."""

from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

SENTINEL2_BANDS: Tuple[str, ...] = ("B02", "B03", "B04", "B08", "B11", "B12")
S2_VNIR_BANDS: Tuple[str, ...] = ("B02", "B03", "B04", "B08")
S2_SWIR_SCL_BANDS: Tuple[str, ...] = ("B11", "B12", "SCL")


def _fill_spectral_zone(
    cube: np.ndarray,
    mask: np.ndarray,
    values: Tuple[float, float, float, float, float, float],
) -> None:
    """Asigna valores espectrales (B02, B03, B04, B08, B11, B12) a una máscara booleana 2D."""
    for band_idx, val in enumerate(values):
        cube[band_idx][mask] = val


def _write_native_pair(
    out_dir: Path,
    prefix: str,
    full_cube_10m: np.ndarray,
    scl_10m: np.ndarray,
    west: float,
    north: float,
) -> Dict[str, Path]:
    """Escribe un par nativo Sentinel-2 L2A: VNIR a 10 m (120x120) y SWIR+SCL a 20 m (60x60)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    h10, w10 = full_cube_10m.shape[1], full_cube_10m.shape[2]
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
        dst.write(full_cube_10m[:4].astype(np.float32))
        dst.descriptions = S2_VNIR_BANDS

    # Promediar bloques 2x2 para B11 y B12 a 20 m nativos, y tomar moda/muestreo discreto para SCL 20 m
    b11_20 = full_cube_10m[4].reshape(h20, 2, w20, 2).mean(axis=(1, 3)).astype(np.float32)
    b12_20 = full_cube_10m[5].reshape(h20, 2, w20, 2).mean(axis=(1, 3)).astype(np.float32)
    scl_20 = scl_10m[::2, ::2].astype(np.float32)

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
        dst.descriptions = S2_SWIR_SCL_BANDS

    return {"vnir_10m": vnir_path, "swir_20m": swir_path}


@pytest.fixture
def synthetic_multitemporal_s2_pair(tmp_path: Path) -> Dict[str, Path]:
    """Genera un par de GeoTIFFs multitemporales sintéticos Sentinel-2 L2A (T0 y T1) en EPSG:5347.

    Mantenido para compatibilidad de tests Fase 1 sobre stacks de 6 bandas a 10 m/px.
    """
    height = 120
    width = 120
    res = 10.0
    west = 5440500.0
    north = 6356300.0
    transform = from_origin(west, north, res, res)

    rr, cc = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")

    forest_sig = (350.0, 600.0, 400.0, 3800.0, 1400.0, 700.0)
    water_sig = (700.0, 900.0, 600.0, 250.0, 150.0, 120.0)
    sandbar_sig = (1600.0, 2000.0, 2400.0, 2600.0, 3100.0, 2700.0)
    excavation_water_sig = (650.0, 950.0, 650.0, 280.0, 180.0, 130.0)
    clearing_stockpile_sig = (1800.0, 2200.0, 2700.0, 2400.0, 3600.0, 3100.0)

    t0_cube = np.zeros((len(SENTINEL2_BANDS), height, width), dtype=np.float32)
    _fill_spectral_zone(t0_cube, np.ones((height, width), dtype=bool), forest_sig)
    _fill_spectral_zone(t0_cube, (cc >= 20) & (cc < 50), water_sig)
    _fill_spectral_zone(t0_cube, (cc >= 50) & (cc < 75) & (rr >= 20) & (rr < 100), sandbar_sig)

    t1_cube = t0_cube.copy()
    _fill_spectral_zone(t1_cube, (rr >= 45) & (rr < 60) & (cc >= 55) & (cc < 70), excavation_water_sig)
    _fill_spectral_zone(t1_cube, (rr >= 30) & (rr < 45) & (cc >= 85) & (cc < 100), clearing_stockpile_sig)

    t0_path = tmp_path / "synthetic_s2_t0_20240315_epsg5347.tif"
    t1_path = tmp_path / "synthetic_s2_t1_20260315_epsg5347.tif"

    profile: Dict[str, Any] = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": len(SENTINEL2_BANDS),
        "dtype": "float32",
        "crs": rasterio.crs.CRS.from_epsg(5347),
        "transform": transform,
        "nodata": -9999.0,
    }

    for out_path, cube in ((t0_path, t0_cube), (t1_path, t1_cube)):
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(cube)
            dst.descriptions = SENTINEL2_BANDS

    return {"t0_path": t0_path, "t1_path": t1_path}


@pytest.fixture
def synthetic_native_s2_scenarios(tmp_path: Path) -> Dict[str, Dict[str, Any]]:
    """Genera 6 escenarios sintéticos a resolución nativa real Sentinel-2 (10 m VNIR + 20 m SWIR/SCL).

    Incluye 2 escenarios positivos (extracción) y 4 escenarios negativos (dinámica natural y nubes SCL).
    """
    height = 120
    width = 120
    west = 5440500.0
    north = 6356300.0
    rr, cc = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")

    # Firmas espectrales físicamente realistas (B02, B03, B04, B08, B11, B12)
    forest_sig = (350.0, 600.0, 400.0, 3800.0, 1400.0, 700.0)
    senescent_forest_sig = (480.0, 720.0, 950.0, 2100.0, 2000.0, 1200.0)
    water_sig = (700.0, 900.0, 600.0, 250.0, 150.0, 120.0)
    sandbar_sig = (1600.0, 2000.0, 2400.0, 2600.0, 3100.0, 2700.0)
    excavation_water_sig = (650.0, 950.0, 650.0, 280.0, 180.0, 130.0)
    clearing_stockpile_sig = (1800.0, 2200.0, 2700.0, 2400.0, 3600.0, 3100.0)
    cloud_shadow_sig = (180.0, 220.0, 190.0, 160.0, 120.0, 90.0)

    def _base_river_cube() -> Tuple[np.ndarray, np.ndarray]:
        cube = np.zeros((6, height, width), dtype=np.float32)
        scl = np.full((height, width), 4, dtype=np.int32)  # 4 = Vegetación ribereña
        _fill_spectral_zone(cube, np.ones((height, width), dtype=bool), forest_sig)

        channel_mask = (cc >= 16) & (cc < 40)
        _fill_spectral_zone(cube, channel_mask, water_sig)
        scl[channel_mask] = 6  # 6 = Agua

        sandbar_mask = (cc >= 40) & (cc < 68) & (rr >= 16) & (rr < 104)
        _fill_spectral_zone(cube, sandbar_mask, sandbar_sig)
        scl[sandbar_mask] = 5  # 5 = Suelo desnudo / barra de arena
        return cube, scl

    scenarios: Dict[str, Dict[str, Any]] = {}

    # 1. Positivo: excavación geométrica inundada dentro del banco de arena (140m x 140m = 19,600 m²)
    c0, s0 = _base_river_cube()
    c1, s1 = c0.copy(), s0.copy()
    pit_mask = (rr >= 44) & (rr < 58) & (cc >= 46) & (cc < 60)
    _fill_spectral_zone(c1, pit_mask, excavation_water_sig)
    s1[pit_mask] = 6
    d1 = tmp_path / "pos_flooded_pit"
    scenarios["pos_flooded_pit"] = {
        "expected_positive": True,
        "expected_type": "frente_excavacion_inundado",
        "t0": _write_native_pair(d1, "t0", c0, s0, west, north),
        "t1": _write_native_pair(d1, "t1", c1, s1, west, north),
    }

    # 2. Positivo: desmonte ribereño y playa de acopio (140m x 140m = 19,600 m²)
    c0, s0 = _base_river_cube()
    c1, s1 = c0.copy(), s0.copy()
    clear_mask = (rr >= 30) & (rr < 44) & (cc >= 84) & (cc < 98)
    _fill_spectral_zone(c1, clear_mask, clearing_stockpile_sig)
    s1[clear_mask] = 5
    d2 = tmp_path / "pos_riparian_clearing"
    scenarios["pos_riparian_clearing"] = {
        "expected_positive": True,
        "expected_type": "desmonte_ribera_acopio",
        "t0": _write_native_pair(d2, "t0", c0, s0, west, north),
        "t1": _write_native_pair(d2, "t1", c1, s1, west, north),
    }

    # 3. Negativo natural: crecida uniforme del nivel del río (ensanche paralelo de márgenes en 30 m)
    c0, s0 = _base_river_cube()
    c1, s1 = c0.copy(), s0.copy()
    flood_belt = ((cc >= 13) & (cc < 16)) | ((cc >= 40) & (cc < 43))
    _fill_spectral_zone(c1, flood_belt, water_sig)
    s1[flood_belt] = 6
    d3 = tmp_path / "neg_uniform_stage_rise"
    scenarios["neg_uniform_stage_rise"] = {
        "expected_positive": False,
        "expected_type": None,
        "t0": _write_native_pair(d3, "t0", c0, s0, west, north),
        "t1": _write_native_pair(d3, "t1", c1, s1, west, north),
    }

    # 4. Negativo natural: migración natural aguas abajo de un banco de arena dentro del cauce
    c0 = np.zeros((6, height, width), dtype=np.float32)
    s0 = np.full((height, width), 4, dtype=np.int32)
    _fill_spectral_zone(c0, np.ones((height, width), dtype=bool), forest_sig)
    wide_channel = (cc >= 16) & (cc < 68)
    _fill_spectral_zone(c0, wide_channel, water_sig)
    s0[wide_channel] = 6
    bar_t0 = (rr >= 20) & (rr < 56) & (cc >= 34) & (cc < 54)
    _fill_spectral_zone(c0, bar_t0, sandbar_sig)
    s0[bar_t0] = 5

    c1 = np.zeros((6, height, width), dtype=np.float32)
    s1 = np.full((height, width), 4, dtype=np.int32)
    _fill_spectral_zone(c1, np.ones((height, width), dtype=bool), forest_sig)
    _fill_spectral_zone(c1, wide_channel, water_sig)
    s1[wide_channel] = 6
    bar_t1 = (rr >= 28) & (rr < 64) & (cc >= 34) & (cc < 54)
    _fill_spectral_zone(c1, bar_t1, sandbar_sig)
    s1[bar_t1] = 5
    d4 = tmp_path / "neg_bar_migration"
    scenarios["neg_bar_migration"] = {
        "expected_positive": False,
        "expected_type": None,
        "t0": _write_native_pair(d4, "t0", c0, s0, west, north),
        "t1": _write_native_pair(d4, "t1", c1, s1, west, north),
    }

    # 5. Negativo natural: senescencia estacional uniforme de toda la vegetación ribereña
    c0, s0 = _base_river_cube()
    c1, s1 = c0.copy(), s0.copy()
    veg_mask = s0 == 4
    _fill_spectral_zone(c1, veg_mask, senescent_forest_sig)
    d5 = tmp_path / "neg_seasonal_senescence"
    scenarios["neg_seasonal_senescence"] = {
        "expected_positive": False,
        "expected_type": None,
        "t0": _write_native_pair(d5, "t0", c0, s0, west, north),
        "t1": _write_native_pair(d5, "t1", c1, s1, west, north),
    }

    # 6. Negativo atmosférico: sombra de nube / nube residual etiquetada en SCL (clases 3 y 9)
    c0, s0 = _base_river_cube()
    c1, s1 = c0.copy(), s0.copy()
    cloud_shadow_patch = (rr >= 30) & (rr < 46) & (cc >= 80) & (cc < 96)
    _fill_spectral_zone(c1, cloud_shadow_patch, cloud_shadow_sig)
    s1[cloud_shadow_patch] = 3  # 3 = Sombra de nube en SCL
    s1[(rr >= 34) & (rr < 40) & (cc >= 84) & (cc < 90)] = 9  # 9 = Nube alta
    d6 = tmp_path / "neg_cloud_shadow_scl"
    scenarios["neg_cloud_shadow_scl"] = {
        "expected_positive": False,
        "expected_type": None,
        "t0": _write_native_pair(d6, "t0", c0, s0, west, north),
        "t1": _write_native_pair(d6, "t1", c1, s1, west, north),
    }

    return scenarios
