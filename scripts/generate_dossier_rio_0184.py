"""Genera automáticamente el primer Dossier de Investigación Territorial para RIO-0184 (P1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.change_detector import compute_spectral_indices  # noqa: E402
from src.core.crs_manager import reproject_geometry  # noqa: E402
from src.core.dossier_generator import generate_candidate_dossier, write_dossier_markdown  # noqa: E402
from src.core.optical_providers import load_native_s2_scene_bundle  # noqa: E402

EXP1_RASTERS = PROJECT_ROOT / "data" / "experiments" / "exp_001_parana_medio" / "rasters"
RECAL_GEOJSON = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated" / "exp_001_recalibrated.geojson"
OUT_DOSSIER = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated" / "dossier_RIO-0184_auto.md"

SCENES = [
    ("2023-11-07", "S2B_20HQK_20231107_0_L2A", 3.51, "t0_S2B_20HQK_20231107_0_L2A_vnir10m.tif", "t0_S2B_20HQK_20231107_0_L2A_swir_scl20m.tif"),
    ("2024-11-06", "S2A_20HQK_20241106_0_L2A", 0.67, "t0_S2A_20HQK_20241106_0_L2A_vnir10m.tif", "t0_S2A_20HQK_20241106_0_L2A_swir_scl20m.tif"),
    ("2025-11-13", "S2A_20HQK_20251113_1_L2A", 2.64, "t0_S2A_20HQK_20251113_1_L2A_vnir10m.tif", "t0_S2A_20HQK_20251113_1_L2A_swir_scl20m.tif"),
    ("2026-11-08", "S2A_20HQK_20261108_1_L2A", 4.85, "t1_S2A_20HQK_20261108_1_L2A_vnir10m.tif", "t1_S2A_20HQK_20261108_1_L2A_swir_scl20m.tif"),
]


def _extract_series_for_feature(feat: Dict[str, Any], crs_epsg: int) -> List[Dict[str, Any]]:
    geom_posgar = reproject_geometry(feat["geometry"], "EPSG:4326", f"EPSG:{crs_epsg}")
    poly = shape(geom_posgar)
    rows: List[Dict[str, Any]] = []
    for date_str, scene_id, stage_m, v_name, s_name in SCENES:
        bundle = load_native_s2_scene_bundle(EXP1_RASTERS / v_name, EXP1_RASTERS / s_name)
        idx = compute_spectral_indices(
            b02=bundle["B02"],
            b03=bundle["B03"],
            b04=bundle["B04"],
            b08=bundle["B08"],
            b11=bundle["B11"],
            valid_mask=bundle["valid_mask"],
        )
        mask = rasterize(
            [(poly, 1)],
            out_shape=bundle["shape_hw"],
            transform=bundle["transform"],
            fill=0,
            dtype="uint8",
            all_touched=False,
        ) == 1
        mv = mask & bundle["valid_mask"]
        scl_vals, scl_cnts = np.unique(bundle["scl"][mask].astype(int), return_counts=True)
        scl_str = ", ".join(f"{int(k)}:{int(v)}" for k, v in zip(scl_vals, scl_cnts))
        rows.append(
            {
                "date": date_str,
                "scene_id": scene_id,
                "stage_m": f"{stage_m:.2f} m",
                "b11_median": f"{float(np.median(bundle['B11'][mv])):.0f}",
                "ndvi_median": f"{float(np.median(idx['ndvi'][mv])):+.3f}",
                "bsi_median": f"{float(np.median(idx['bsi'][mv])):+.3f}",
                "mndwi_median": f"{float(np.median(idx['mndwi'][mv])):+.3f}",
                "scl_summary": scl_str,
            }
        )
    return rows


def main() -> None:
    fc = json.loads(RECAL_GEOJSON.read_text(encoding="utf-8"))
    meta = fc["metadata"]
    clearing_feats = [
        f for f in fc["features"] if f["properties"]["anomaly_type"] == "desmonte_ribera_acopio"
    ]
    if not clearing_feats:
        raise RuntimeError("No se encontró el candidato desmonte_ribera_acopio en exp_001_recalibrated.geojson.")
    target_feat = clearing_feats[0]
    series = _extract_series_for_feature(target_feat, int(meta["crs_metric_epsg"]))
    md = generate_candidate_dossier(
        feature=target_feat,
        detection_metadata=meta,
        aoi_name="Paraná Medio — Sector Isla/Ribera Santa Fe–Paraná (contraste Exp 001 / RIO-0184)",
        period_label="2023-11-07 .. 2025-11-13 (T0) -> 2026-11-08 (T1)",
        multitemporal_series=series,
        map_artifacts=[
            str(RECAL_GEOJSON.relative_to(PROJECT_ROOT)),
            "data/experiments/exp_001_parana_medio/08_recortes_zoom_anomalias_principales.png",
        ],
    )
    out = write_dossier_markdown(md, OUT_DOSSIER)
    print(
        f"Dossier generado: {out} | id={target_feat['id']} | "
        f"area_m2={target_feat['properties']['area_m2']} | "
        f"zone={target_feat['properties']['geomorphological_zone']} | "
        f"confidence={target_feat['properties']['confidence']}"
    )


if __name__ == "__main__":
    main()
