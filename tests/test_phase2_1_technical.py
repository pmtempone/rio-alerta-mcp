"""Tests técnicos P0.1 / P0.2 (T1-T3) de calibración física, geométrica y geomorfológica."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine
from shapely.geometry import shape

from src.core.change_detector import detect_extraction_anomalies
from src.core.crs_manager import reproject_geometry


def _write_s2_pair(
    out_dir: Path,
    prefix: str,
    b02: np.ndarray,
    b03: np.ndarray,
    b04: np.ndarray,
    b08: np.ndarray,
    b11: np.ndarray,
    scl: np.ndarray,
) -> Tuple[Path, Path]:
    """Escribe un par sintético VNIR 10m + SWIR/SCL 20m en EPSG:5347."""
    out_dir.mkdir(parents=True, exist_ok=True)
    h, w = b02.shape
    origin_x, origin_y = 5430000.0, 6490000.0
    t10 = Affine.translation(origin_x, origin_y) @ Affine.scale(10.0, -10.0)
    t20 = Affine.translation(origin_x, origin_y) @ Affine.scale(20.0, -20.0)

    vnir_path = out_dir / f"{prefix}_vnir10m.tif"
    with rasterio.open(
        vnir_path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=4,
        dtype="uint16",
        crs="EPSG:5347",
        transform=t10,
    ) as dst:
        dst.write(b02.astype(np.uint16), 1)
        dst.write(b03.astype(np.uint16), 2)
        dst.write(b04.astype(np.uint16), 3)
        dst.write(b08.astype(np.uint16), 4)
        dst.descriptions = ("B02", "B03", "B04", "B08")

    # Submuestreo 20m (promedio simple para reflectancia, clase modal/centro para SCL)
    b11_20 = b11.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).astype(np.uint16)
    scl_20 = scl[::2, ::2].astype(np.uint16)

    swir_path = out_dir / f"{prefix}_swir_scl20m.tif"
    with rasterio.open(
        swir_path,
        "w",
        driver="GTiff",
        height=h // 2,
        width=w // 2,
        count=3,
        dtype="uint16",
        crs="EPSG:5347",
        transform=t20,
    ) as dst:
        dst.write(b11_20, 1)
        dst.write(b11_20, 2)
        dst.write(scl_20, 3)
        dst.descriptions = ("B11", "B12", "SCL")

    return vnir_path, swir_path


def _fill_scene(
    h: int,
    w: int,
    sig: Tuple[int, int, int, int, int, int],
) -> Dict[str, np.ndarray]:
    b02, b03, b04, b08, b11, scl = sig
    return {
        "B02": np.full((h, w), b02, dtype=np.float32),
        "B03": np.full((h, w), b03, dtype=np.float32),
        "B04": np.full((h, w), b04, dtype=np.float32),
        "B08": np.full((h, w), b08, dtype=np.float32),
        "B11": np.full((h, w), b11, dtype=np.float32),
        "SCL": np.full((h, w), scl, dtype=np.uint16),
    }


def _paint_block(
    scene: Dict[str, np.ndarray],
    r0: int,
    r1: int,
    c0: int,
    c1: int,
    sig: Tuple[int, int, int, int, int, int],
) -> None:
    b02, b03, b04, b08, b11, scl = sig
    scene["B02"][r0:r1, c0:c1] = b02
    scene["B03"][r0:r1, c0:c1] = b03
    scene["B04"][r0:r1, c0:c1] = b04
    scene["B08"][r0:r1, c0:c1] = b08
    scene["B11"][r0:r1, c0:c1] = b11
    scene["SCL"][r0:r1, c0:c1] = scl


def test_t1_area_matches_vector_geometry_and_spectral_stats_separation(tmp_path: Path) -> None:
    """T1: area_m2 coincide con la geometría vectorial y spectral_stats separa medianas por fecha de deltas."""
    h, w = 80, 80
    forest = (350, 600, 400, 3800, 1400, 4)
    clearing = (1800, 2200, 2700, 2400, 3600, 5)

    t0 = _fill_scene(h, w, forest)
    t1 = _fill_scene(h, w, forest)
    # Bloque de 6x6 píxeles (60m x 60m = 3600 m2 exactos) en ribera firme
    _paint_block(t1, 30, 36, 30, 36, clearing)

    t0_v, t0_s = _write_s2_pair(tmp_path, "t0", t0["B02"], t0["B03"], t0["B04"], t0["B08"], t0["B11"], t0["SCL"])
    t1_v, t1_s = _write_s2_pair(tmp_path, "t1", t1["B02"], t1["B03"], t1["B04"], t1["B08"], t1["B11"], t1["SCL"])

    res = detect_extraction_anomalies(
        t0_vnir_paths=t0_v,
        t0_swir_scl_paths=t0_s,
        t1_vnir_path=t1_v,
        t1_swir_scl_path=t1_s,
        min_area_m2=1600.0,
        work_dir=tmp_path / "work",
    )

    assert res.metadata["candidate_count"] == 1
    feat = res.features[0]
    props = feat["properties"]

    geom_posgar = reproject_geometry(feat["geometry"], "EPSG:4326", f"EPSG:{props['crs_epsg']}")
    exact_area = float(shape(geom_posgar).area)
    assert props["area_m2"] == pytest.approx(exact_area, abs=0.1)

    stats = props["spectral_stats"]
    assert stats is not None
    assert stats["pixel_count_10m"] >= 25
    assert stats["t0_b11_median"] == pytest.approx(1400.0, abs=1.0)
    assert stats["t1_b11_median"] == pytest.approx(3600.0, abs=1.0)
    assert stats["t0_ndvi_median"] > 0.70
    assert stats["t1_ndvi_median"] < 0.0
    assert stats["t0_bsi_median"] < -0.20
    assert stats["t1_bsi_median"] > 0.15
    assert stats["t1_scl_counts"].get("5", 0) == stats["pixel_count_10m"]
    assert props["delta_bsi_mean"] > 0.30
    assert props["geomorphological_zone"] == "RIBERA_FIRME_Y_TERRAZA"


def test_t2_dark_water_filter_suppresses_humic_water_but_preserves_wet_sand(tmp_path: Path) -> None:
    """T2: agua oscura (B11<600 / SCL=6) se filtra; arena húmeda expuesta (B11=680, B04=720) se conserva."""
    h, w = 80, 80
    forest = (350, 600, 400, 3800, 1400, 4)
    dark_water = (220, 260, 240, 220, 180, 6)  # Agua oscura con SCL=6 y B11=180
    dark_water_unclassified = (250, 300, 290, 280, 350, 5)  # Agua oscura mal etiquetada como SCL=5
    wet_sand = (500, 600, 720, 500, 680, 5)  # Arena húmeda real (B11=680>=600, B04=720>=420, B08=500>=350)

    t0 = _fill_scene(h, w, forest)
    t1 = _fill_scene(h, w, forest)
    _paint_block(t1, 10, 18, 10, 18, dark_water)
    _paint_block(t1, 10, 18, 40, 48, dark_water_unclassified)
    _paint_block(t1, 44, 52, 44, 52, wet_sand)

    t0_v, t0_s = _write_s2_pair(tmp_path, "t0", t0["B02"], t0["B03"], t0["B04"], t0["B08"], t0["B11"], t0["SCL"])
    t1_v, t1_s = _write_s2_pair(tmp_path, "t1", t1["B02"], t1["B03"], t1["B04"], t1["B08"], t1["B11"], t1["SCL"])

    res = detect_extraction_anomalies(
        t0_vnir_paths=t0_v,
        t0_swir_scl_paths=t0_s,
        t1_vnir_path=t1_v,
        t1_swir_scl_path=t1_s,
        min_area_m2=1600.0,
        work_dir=tmp_path / "work",
    )

    assert res.metadata["candidate_count"] == 1
    props = res.features[0]["properties"]
    assert props["anomaly_type"] == "desmonte_ribera_acopio"
    assert props["spectral_stats"]["t1_b11_median"] == pytest.approx(680.0, abs=1.0)


def test_t3_geomorphological_flood_suppression_preserves_ribera_firme(tmp_path: Path) -> None:
    """T3: crecida hidrométrica suprime inundación en planicie_islas pero preserva excavación en ribera_firme."""
    h, w = 100, 100
    forest = (350, 600, 400, 3800, 1400, 4)
    water = (450, 750, 500, 220, 110, 6)

    t0 = _fill_scene(h, w, forest)
    # Cauce activo grande en T0 (columnas 0..25 -> > 5 ha? 100*25 = 2500 px = 25 ha)
    _paint_block(t0, 0, 100, 0, 25, water)
    # Laguna interna en planicie (filas 40..46, cols 40..46 -> 36 px = 0.36 ha < 5 ha)
    _paint_block(t0, 40, 46, 40, 46, water)

    t1 = {k: v.copy() for k, v in t0.items()}
    # Expansión natural de laguna en planicie_islas (dentro de 450m de la laguna T0)
    _paint_block(t1, 34, 52, 34, 52, water)
    # Excavación inundada real en ribera_firme (lejos de cauce y laguna: filas 70..78, cols 80..88)
    _paint_block(t1, 70, 78, 80, 88, water)

    t0_v, t0_s = _write_s2_pair(tmp_path, "t0", t0["B02"], t0["B03"], t0["B04"], t0["B08"], t0["B11"], t0["SCL"])
    t1_v, t1_s = _write_s2_pair(tmp_path, "t1", t1["B02"], t1["B03"], t1["B04"], t1["B08"], t1["B11"], t1["SCL"])

    res = detect_extraction_anomalies(
        t0_vnir_paths=t0_v,
        t0_swir_scl_paths=t0_s,
        t1_vnir_path=t1_v,
        t1_swir_scl_path=t1_s,
        min_area_m2=1600.0,
        work_dir=tmp_path / "work",
        stage_t0_m=1.0,
        stage_t1_m=2.5,
    )

    assert res.metadata["hydrological_controls"]["is_systemic_stage_rise"] is True
    assert res.metadata["candidate_count"] == 1
    props = res.features[0]["properties"]
    assert props["anomaly_type"] == "frente_excavacion_inundado"
    assert props["geomorphological_zone"] == "RIBERA_FIRME_Y_TERRAZA"
