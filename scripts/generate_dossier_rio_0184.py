"""Genera automáticamente el Dossier de Investigación Territorial exacto para RIO-0184 (P0/P1)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.change_detector import compute_spectral_indices  # noqa: E402
from src.core.crs_manager import reproject_geometry  # noqa: E402
from src.core.dossier_generator import generate_candidate_dossier, write_dossier_markdown  # noqa: E402
from src.core.hydrological_control import fetch_ina_stage_height_with_provenance  # noqa: E402
from src.core.optical_providers import load_native_s2_scene_bundle  # noqa: E402

TARGET_ID = "RIO-0184"
EXP1_GEOJSON = PROJECT_ROOT / "data" / "experiments" / "exp_001_parana_medio" / "06_anomalias_detectadas.geojson"
RECAL_GEOJSON = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated" / "exp_001_recalibrated.geojson"
EXP1_RASTERS = PROJECT_ROOT / "data" / "experiments" / "exp_001_parana_medio" / "rasters"
OUT_DOSSIER = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated" / "dossier_RIO-0184_auto.md"
OUT_MAP = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated" / "mapa_candidato_RIO-0184.png"
OUT_FIG2 = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated" / "figura_2_serie_temporal_RIO-0184.png"

SCENES = [
    ("2023-11-07", "S2B_20HQK_20231107_0_L2A", 3.51, "t0_S2B_20HQK_20231107_0_L2A_vnir10m.tif", "t0_S2B_20HQK_20231107_0_L2A_swir_scl20m.tif"),
    ("2024-11-06", "S2A_20HQK_20241106_0_L2A", 0.67, "t0_S2A_20HQK_20241106_0_L2A_vnir10m.tif", "t0_S2A_20HQK_20241106_0_L2A_swir_scl20m.tif"),
    ("2025-11-13", "S2A_20HQK_20251113_1_L2A", 2.64, "t0_S2A_20HQK_20251113_1_L2A_vnir10m.tif", "t0_S2A_20HQK_20251113_1_L2A_swir_scl20m.tif"),
    ("2026-11-08", "S2A_20HQK_20261108_1_L2A", 4.85, "t1_S2A_20HQK_20261108_1_L2A_vnir10m.tif", "t1_S2A_20HQK_20261108_1_L2A_swir_scl20m.tif"),
]


def _find_feature_by_id(fc: Dict[str, Any], target_id: str) -> Dict[str, Any]:
    for feat in fc.get("features", []):
        fid = str(feat.get("id") or feat.get("properties", {}).get("id") or "")
        if fid == target_id:
            return feat
    raise RuntimeError(f"Candidato '{target_id}' no encontrado en {EXP1_GEOJSON}. Ejecución abortada.")


def _rgb_patch(bundle: Dict[str, Any], r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
    r = np.clip(bundle["B04"][r0:r1, c0:c1] / 2500.0, 0.0, 1.0)
    g = np.clip(bundle["B03"][r0:r1, c0:c1] / 2500.0, 0.0, 1.0)
    b = np.clip(bundle["B02"][r0:r1, c0:c1] / 2500.0, 0.0, 1.0)
    return np.nan_to_num(np.dstack([r, g, b]), nan=0.0)


def _generate_candidate_map(
    poly: Any,
    b_t0: Dict[str, Any],
    b_t1: Dict[str, Any],
    out_png: Path,
) -> Path:
    inv = ~b_t1["transform"]
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = poly.centroid.x, poly.centroid.y
    col_c, row_c = [int(round(v)) for v in (inv * (cx, cy))]
    half = 45
    h, w = b_t1["shape_hw"]
    r0, r1 = max(0, row_c - half), min(h, row_c + half)
    c0, c1 = max(0, col_c - half), min(w, col_c + half)

    rgb0 = _rgb_patch(b_t0, r0, r1, c0, c1)
    rgb1 = _rgb_patch(b_t1, r0, r1, c0, c1)

    xs, ys = poly.exterior.xy
    px_cols, px_rows = [], []
    for x_val, y_val in zip(xs, ys):
        col_f, row_f = inv * (x_val, y_val)
        px_cols.append(col_f - c0)
        px_rows.append(row_f - r0)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8), dpi=150)
    axes[0].imshow(rgb0)
    axes[0].plot(px_cols, px_rows, color="yellow", linewidth=1.6)
    axes[0].set_title("T0 (2025-11-13) — RGB Sentinel-2 10m")
    axes[0].axis("off")

    axes[1].imshow(rgb1)
    axes[1].plot(px_cols, px_rows, color="red", linewidth=1.6)
    axes[1].set_title("T1 (2026-11-08) — RIO-0184 (3,097.80 m²)")
    axes[1].axis("off")

    fig.suptitle("Evidencia espacial específica — Candidato RIO-0184 (POSGAR EPSG:5347)", fontsize=11)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    return out_png


def _generate_multitemporal_figure(
    poly: Any,
    bundles: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    out_png: Path,
) -> Path:
    inv = ~bundles[-1]["transform"]
    cx, cy = poly.centroid.x, poly.centroid.y
    col_c, row_c = [int(round(v)) for v in (inv * (cx, cy))]
    half = 35
    h, w = bundles[-1]["shape_hw"]
    r0, r1 = max(0, row_c - half), min(h, row_c + half)
    c0, c1 = max(0, col_c - half), min(w, col_c + half)

    xs, ys = poly.exterior.xy
    px_cols, px_rows = [], []
    for x_val, y_val in zip(xs, ys):
        col_f, row_f = inv * (x_val, y_val)
        px_cols.append(col_f - c0)
        px_rows.append(row_f - r0)

    fig = plt.figure(figsize=(12, 6.5), dpi=150)
    gs = fig.add_gridspec(2, 4, height_ratios=[1.1, 1.0], hspace=0.35, wspace=0.15)
    for i, (bundle, row) in enumerate(zip(bundles, rows)):
        ax = fig.add_subplot(gs[0, i])
        ax.imshow(_rgb_patch(bundle, r0, r1, c0, c1))
        ax.plot(px_cols, px_rows, color="cyan" if i < 3 else "red", linewidth=1.4)
        ax.set_title(f"{row['date']}\n({row['_stage_val']:.2f} m)", fontsize=9)
        ax.axis("off")

    ax_ts = fig.add_subplot(gs[1, :])
    dates = [r["date"] for r in rows]
    ndvis = [r["_ndvi_med"] for r in rows]
    bsis = [r["_bsi_med"] for r in rows]
    stages = [r["_stage_val"] for r in rows]

    ax_ts.plot(dates, ndvis, marker="o", color="#2e7d32", linewidth=2, label="NDVI mediano")
    ax_ts.plot(dates, bsis, marker="s", color="#d84315", linewidth=2, label="BSI mediano")
    ax_ts.axhline(0.0, color="gray", linestyle="--", linewidth=0.8)
    ax_ts.set_ylabel("Índice espectral (mediana polígono)")
    ax_ts.set_ylim(-0.6, 0.9)
    ax_ts.grid(alpha=0.25)

    ax_h = ax_ts.twinx()
    ax_h.plot(dates, stages, marker="^", linestyle=":", color="#1565c0", linewidth=1.8, label="Altura Puerto Paraná (m)")
    ax_h.set_ylabel("Altura hidrométrica INA (m)", color="#1565c0")
    ax_h.tick_params(axis="y", labelcolor="#1565c0")

    lines1, labels1 = ax_ts.get_legend_handles_labels()
    lines2, labels2 = ax_h.get_legend_handles_labels()
    ax_ts.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)
    ax_ts.set_title("Trayectoria espectral e hidrométrica 2023–2026 sobre RIO-0184", fontsize=10)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    return out_png


def main() -> None:
    fc_exp1 = json.loads(EXP1_GEOJSON.read_text(encoding="utf-8"))
    target_feat = _find_feature_by_id(fc_exp1, TARGET_ID)
    recal_fc = json.loads(RECAL_GEOJSON.read_text(encoding="utf-8"))
    meta = dict(recal_fc["metadata"])

    crs_epsg = int(meta["crs_metric_epsg"])
    geom_posgar = reproject_geometry(target_feat["geometry"], "EPSG:4326", f"EPSG:{crs_epsg}")
    poly = shape(geom_posgar)
    exact_area_m2 = round(float(poly.area), 2)

    bundles: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    stage_sources: List[str] = []

    for date_str, scene_id, fallback_stage, v_name, s_name in SCENES:
        bundle = load_native_s2_scene_bundle(EXP1_RASTERS / v_name, EXP1_RASTERS / s_name)
        bundles.append(bundle)
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

        ina_val, ina_src = fetch_ina_stage_height_with_provenance(series_id=30, target_date_iso=date_str)
        stage_val: Optional[float] = ina_val if ina_val is not None else fallback_stage
        eff_src = ina_src if ina_val is not None else "FALLBACK_EXP001"
        stage_sources.append(f"{date_str}:{eff_src}")

        rows.append(
            {
                "date": date_str,
                "scene_id": scene_id,
                "stage_m": f"{stage_val:.2f} m" if stage_val is not None else "N/D",
                "stage_source": eff_src,
                "b11_median": f"{float(np.median(bundle['B11'][mv])):.0f}",
                "ndvi_median": f"{float(np.median(idx['ndvi'][mv])):+.3f}",
                "bsi_median": f"{float(np.median(idx['bsi'][mv])):+.3f}",
                "mndwi_median": f"{float(np.median(idx['mndwi'][mv])):+.3f}",
                "scl_summary": scl_str,
                "_stage_val": stage_val,
                "_stage_src": eff_src,
                "_b11_med": float(np.median(bundle["B11"][mv])),
                "_b04_med": float(np.median(bundle["B04"][mv])),
                "_ndvi_med": float(np.median(idx["ndvi"][mv])),
                "_bsi_med": float(np.median(idx["bsi"][mv])),
                "_mndwi_med": float(np.median(idx["mndwi"][mv])),
                "_scl_dict": {str(int(k)): int(v) for k, v in zip(scl_vals, scl_cnts)},
                "_px": int(np.sum(mask)),
            }
        )

    # Sincronizar exactamente los controles hidrométricos de metadata con la tabla multitemporal
    hydro_meta = dict(meta.get("hydrological_controls") or {})
    t0_stage_val = rows[2]["_stage_val"]
    t1_stage_val = rows[3]["_stage_val"]
    hydro_meta["stage_t0_m"] = t0_stage_val
    hydro_meta["stage_t0_source"] = rows[2]["_stage_src"]
    hydro_meta["stage_t1_m"] = t1_stage_val
    hydro_meta["stage_t1_source"] = rows[3]["_stage_src"]
    if t0_stage_val is not None and t1_stage_val is not None:
        hydro_meta["stage_delta_m"] = round(float(t1_stage_val) - float(t0_stage_val), 3)
    hydro_meta["stage_station"] = "Puerto Paraná (INA seriesId=30)"
    hydro_meta["stage_queried_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    meta["hydrological_controls"] = hydro_meta

    # Enriquecer props de RIO-0184 con geometría vectorial exacta y estadísticas reales
    props = dict(target_feat["properties"])
    props["area_m2"] = exact_area_m2
    props["area_ha"] = round(exact_area_m2 / 10000.0, 4)
    props["geomorphological_zone"] = props.get("geomorphological_zone", "PLANICIE_INUNDABLE_E_ISLAS")
    props["spectral_stats"] = {
        "area_method": "shapely_polygon_posgar_2007",
        "pixel_count_10m": rows[3]["_px"],
        "effective_swir_pixels_20m": round(rows[3]["_px"] / 4.0, 2),
        "t0_b11_median": round(rows[2]["_b11_med"], 1),
        "t1_b11_median": round(rows[3]["_b11_med"], 1),
        "t1_b04_median": round(rows[3]["_b04_med"], 1),
        "t0_ndvi_median": round(rows[2]["_ndvi_med"], 3),
        "t1_ndvi_median": round(rows[3]["_ndvi_med"], 3),
        "t0_bsi_median": round(rows[2]["_bsi_med"], 3),
        "t1_bsi_median": round(rows[3]["_bsi_med"], 3),
        "t1_mndwi_median": round(rows[3]["_mndwi_med"], 3),
        "t1_scl_counts": rows[3]["_scl_dict"],
    }
    props["uncertainty"] = dict(props.get("uncertainty") or {})
    props["uncertainty"]["limitations"] = list(props["uncertainty"].get("limitations") or []) + [
        "En Exp001 recalibrado (Fase 2.1), el núcleo de este mismo sitio sobrevive como RIO-0013 (1,848.69 m²) tras excluir píxeles de borde con SCL=7.",
        f"Procedencia hidrométrica por fecha: {'; '.join(stage_sources)}.",
    ]

    enriched_feat = dict(target_feat)
    enriched_feat["properties"] = props

    map_png = _generate_candidate_map(poly, bundles[2], bundles[3], OUT_MAP)
    fig2_png = _generate_multitemporal_figure(poly, bundles, rows, OUT_FIG2)
    md = generate_candidate_dossier(
        feature=enriched_feat,
        detection_metadata=meta,
        aoi_name="Paraná Medio — Sector Isla/Ribera Santa Fe–Paraná (RIO-0184 Exp 001)",
        period_label="2023-11-07 .. 2025-11-13 (T0) -> 2026-11-08 (T1)",
        multitemporal_series=rows,
        map_artifacts=[
            map_png.name,
            fig2_png.name,
            str(EXP1_GEOJSON.relative_to(PROJECT_ROOT)),
        ],
        figure_captions={
            map_png.name: "Comparación RGB Sentinel-2 (10 m) entre T0 (2025-11-13) y T1 (2026-11-08) con contorno vectorial POSGAR de RIO-0184 (3,097.80 m²).",
            fig2_png.name: "Serie visual RGB 2023–2026 sobre RIO-0184 y evolución conjunta de NDVI mediano, BSI mediano y altura hidrométrica INA (Puerto Paraná).",
        },
    )
    out = write_dossier_markdown(md, OUT_DOSSIER)
    print(f"Dossier exacto generado: {out} | id={TARGET_ID} | area_m2={exact_area_m2} | map={map_png.name}")


if __name__ == "__main__":
    main()
