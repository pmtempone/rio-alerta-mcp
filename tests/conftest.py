"""Fixtures sintéticos multitemporales en POSGAR 2007 (EPSG:5347) para pruebas de RíoAlerta."""

from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

SENTINEL2_BANDS: Tuple[str, ...] = ("B02", "B03", "B04", "B08", "B11", "B12")


def _fill_spectral_zone(
    cube: np.ndarray,
    mask: np.ndarray,
    values: Tuple[float, float, float, float, float, float],
) -> None:
    """Asigna valores espectrales (B02, B03, B04, B08, B11, B12) a una máscara booleana 2D."""
    for band_idx, val in enumerate(values):
        cube[band_idx][mask] = val


@pytest.fixture
def synthetic_multitemporal_s2_pair(tmp_path: Path) -> Dict[str, Path]:
    """Genera un par de GeoTIFFs multitemporales sintéticos Sentinel-2 L2A (T0 y T1) en EPSG:5347.

    Características del par sintético:
    - Proyección: POSGAR 2007 Faja 5 (`EPSG:5347`, tramo Río Paraná / Rosario).
    - Grilla: `120 x 120` píxeles a `10.0 m/px` (extensión `1200 m x 1200 m`), 6 bandas (`B02`, `B03`,
      `B04`, `B08`, `B11`, `B12`) en reflectancia escalada Sentinel-2 L2A (`float32`).
    - Escena `T0` (`2024-03-15`): cauce fluvial (cols 20..50), banco de arena emergido (cols 50..75,
      rows 20..100) y bosque en galería ribereño en el resto de la escena.
    - Escena `T1` (`2026-03-15`): inyecta dos alteraciones antrópicas de extracción de áridos:
      1. Excavación inundada en banco de arena (rows 45..60, cols 55..70 -> `22.500 m²`, fuerte `ΔMNDWI > 0`).
      2. Desmonte ribereño y playa de acopio (rows 30..45, cols 85..100 -> `22.500 m²`, fuerte `ΔBSI > 0` y `ΔNDVI < 0`).
    """
    height = 120
    width = 120
    res = 10.0
    west = 5440500.0
    north = 6356300.0
    transform = from_origin(west, north, res, res)

    rr, cc = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")

    # Firmas espectrales base (B02, B03, B04, B08, B11, B12)
    forest_sig = (350.0, 600.0, 400.0, 3800.0, 1400.0, 700.0)
    water_sig = (700.0, 900.0, 600.0, 250.0, 150.0, 120.0)
    sandbar_sig = (1600.0, 2000.0, 2400.0, 2600.0, 3100.0, 2700.0)
    excavation_water_sig = (650.0, 950.0, 650.0, 280.0, 180.0, 130.0)
    clearing_stockpile_sig = (1800.0, 2200.0, 2700.0, 2400.0, 3600.0, 3100.0)

    t0_cube = np.zeros((len(SENTINEL2_BANDS), height, width), dtype=np.float32)
    _fill_spectral_zone(t0_cube, np.ones((height, width), dtype=bool), forest_sig)

    channel_mask = (cc >= 20) & (cc < 50)
    _fill_spectral_zone(t0_cube, channel_mask, water_sig)

    sandbar_mask = (cc >= 50) & (cc < 75) & (rr >= 20) & (rr < 100)
    _fill_spectral_zone(t0_cube, sandbar_mask, sandbar_sig)

    t1_cube = t0_cube.copy()
    excavation_mask = (rr >= 45) & (rr < 60) & (cc >= 55) & (cc < 70)
    _fill_spectral_zone(t1_cube, excavation_mask, excavation_water_sig)

    clearing_mask = (rr >= 30) & (rr < 45) & (cc >= 85) & (cc < 100)
    _fill_spectral_zone(t1_cube, clearing_mask, clearing_stockpile_sig)

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

    return {
        "t0_path": t0_path,
        "t1_path": t1_path,
    }
