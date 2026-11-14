"""Script reproducible del Experimento 001 (Paraná Medio) usando el motor de Fase 2 sin modificaciones."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from pystac_client import Client
from shapely.geometry import shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.core.change_detector import (
    _compute_baseline_median_indices,
    compute_multitemporal_baseline_and_changes,
    compute_spectral_indices,
    detect_extraction_anomalies,
)
from src.core.crs_manager import bbox_wgs84_to_posgar
from src.core.hydrological_control import (
    compute_reach_hydrological_state,
    detect_natural_bar_migration_dipoles,
    normalize_seasonal_riparian_indices,
)
from src.core.optical_providers import (
    _export_stac_item_to_posgar_geotiffs,
    load_native_s2_scene_bundle,
)

# AOI acotado del corredor Santa Fe (Canal de Acceso / Colastiné) - Islas - Bajada Grande / Paraná
AOI_BBOX_WGS84: Tuple[float, float, float, float] = (-60.66, -31.74, -60.53, -31.64)

# Escenas Sentinel-2 L2A seleccionadas en celda MGRS-20HQK (cobertura 100% del AOI)
STAC_URL: str = settings.earth_search_stac_url
COLLECTION: str = "sentinel-2-l2a"
T1_SCENE_ID: str = "S2A_20HQK_20261108_1_L2A"
T0_PRIMARY_SCENE_ID: str = "S2A_20HQK_20241106_0_L2A"
T0_BASELINE_SCENE_IDS: Tuple[str, ...] = (
    "S2B_20HQK_20231107_0_L2A",
    "S2A_20HQK_20241106_0_L2A",
    "S2A_20HQK_20251113_1_L2A",
)

EXP_DIR: Path = PROJECT_ROOT / "data" / "experiments" / "exp_001_parana_medio"
RASTERS_DIR: Path = EXP_DIR / "rasters"
PROCESSED_DIR: Path = EXP_DIR / "processed"


def _fetch_or_load_scene_tifs(
    items_by_id: Dict[str, Any],
    scene_id: str,
    prefix: str,
    bounds_posgar: Tuple[float, float, float, float],
    crs_epsg: int,
) -> Tuple[Path, Path, Dict[str, Any]]:
    """Descarga ventanas COG POSGAR 2007 si no existen localmente y retorna metadatos."""
    vnir_expected = RASTERS_DIR / f"{prefix}_{scene_id}_vnir10m.tif"
    swir_expected = RASTERS_DIR / f"{prefix}_{scene_id}_swir_scl20m.tif"

    item = items_by_id[scene_id]
    if vnir_expected.exists() and swir_expected.exists():
        dt_str = item.datetime.strftime("%Y-%m-%d")
        meta_dict = {
            "scene_id": scene_id,
            "acquisition_date": dt_str,
            "datetime_utc": item.datetime.isoformat(),
            "cloud_cover_tile_pct": round(float(item.properties.get("eo:cloud_cover", 0.0)), 3),
            "grid_code": item.properties.get("grid:code"),
            "boa_offset_applied": item.properties.get("earthsearch:boa_offset_applied"),
            "cached": True,
        }
        return vnir_expected, swir_expected, meta_dict

    vnir_p, swir_p, meta = _export_stac_item_to_posgar_geotiffs(
        item=item,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
        out_dir=RASTERS_DIR,
        prefix=prefix,
        sign_planetary=False,
    )
    meta_dict = {
        "scene_id": meta.scene_id,
        "acquisition_date": meta.acquisition_date,
        "datetime_utc": item.datetime.isoformat(),
        "cloud_cover_tile_pct": meta.cloud_cover_pct,
        "grid_code": item.properties.get("grid:code"),
        "boa_offset_applied": item.properties.get("earthsearch:boa_offset_applied"),
        "cached": False,
    }
    return vnir_p, swir_p, meta_dict


def _compute_shared_rgb_bounds(
    bundles: Sequence[Dict[str, Any]],
) -> Tuple[np.ndarray, np.ndarray]:
    """Calcula percentiles 2-98 compartidos por canal RGB sobre píxeles válidos."""
    lows = []
    highs = []
    for ch_key in ("B04", "B03", "B02"):
        vals = np.concatenate(
            [b[ch_key][b["valid_mask"] & np.isfinite(b[ch_key])] for b in bundles]
        )
        lows.append(float(np.percentile(vals, 2)))
        highs.append(float(np.percentile(vals, 98)))
    return np.array(lows, dtype=np.float32), np.array(highs, dtype=np.float32)


def _bundle_to_rgb(
    bundle: Dict[str, Any],
    p_low: np.ndarray,
    p_high: np.ndarray,
    gamma: float = 1.25,
) -> np.ndarray:
    """Construye matriz RGB uint8 [H, W, 3] normalizada con límites compartidos."""
    stack = np.stack([bundle["B04"], bundle["B03"], bundle["B02"]], axis=-1).astype(np.float32)
    valid = bundle["valid_mask"]
    rgb = np.zeros_like(stack, dtype=np.float32)
    for c in range(3):
        denom = max(1e-3, float(p_high[c] - p_low[c]))
        norm = np.clip((stack[..., c] - p_low[c]) / denom, 0.0, 1.0)
        rgb[..., c] = np.power(np.where(np.isfinite(norm), norm, 0.0), 1.0 / gamma)
    rgb[~valid] = 0.12
    return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)


def _format_posgar_axes(
    ax: plt.Axes,
    bounds_posgar: Tuple[float, float, float, float],
    crs_epsg: int,
    show_scale_bar: bool = True,
    scale_loc: str = "lower_left",
) -> None:
    """Agrega grilla POSGAR 2007, barra de escala 2 km y flecha Norte."""
    minx, miny, maxx, maxy = bounds_posgar
    ax.set_xlabel(f"Este POSGAR 2007 / EPSG:{crs_epsg} (m)", fontsize=9)
    ax.set_ylabel(f"Norte POSGAR 2007 / EPSG:{crs_epsg} (m)", fontsize=9)
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(axis="both", labelsize=8)
    ax.grid(color="white", linestyle=":", linewidth=0.5, alpha=0.45)

    if show_scale_bar:
        bar_len = 2000.0
        x0 = (
            maxx - bar_len - 0.04 * (maxx - minx)
            if scale_loc == "lower_right"
            else minx + 0.04 * (maxx - minx)
        )
        y0 = miny + 0.05 * (maxy - miny)
        ax.plot([x0, x0 + bar_len], [y0, y0], color="white", linewidth=3.5, solid_capstyle="butt")
        ax.plot([x0, x0 + bar_len], [y0, y0], color="black", linewidth=1.8, solid_capstyle="butt")
        ax.text(
            x0 + bar_len / 2.0,
            y0 + 0.015 * (maxy - miny),
            "2 km",
            color="white",
            fontsize=8,
            fontweight="bold",
            ha="center",
            va="bottom",
            bbox={"boxstyle": "round,pad=0.15", "facecolor": "black", "alpha": 0.65, "edgecolor": "none"},
        )
        nx = maxx - 0.05 * (maxx - minx)
        ny = maxy - 0.09 * (maxy - miny)
        ax.annotate(
            "N",
            xy=(nx, ny + 0.055 * (maxy - miny)),
            xytext=(nx, ny),
            ha="center",
            va="center",
            color="white",
            fontsize=9,
            fontweight="bold",
            arrowprops={"arrowstyle": "->", "color": "white", "lw": 2.0},
            bbox={"boxstyle": "circle,pad=0.15", "facecolor": "black", "alpha": 0.65, "edgecolor": "none"},
        )


def _explain_anomaly(props: Dict[str, Any]) -> str:
    """Explica en español por qué el algoritmo de Fase 2 disparó la anomalía candidata."""
    atype = props["anomaly_type"]
    dm = props["delta_mndwi_mean"]
    db = props["delta_bsi_mean"]
    dn = props["delta_ndvi_mean"]
    unc = props["uncertainty"]
    rect = props["rectangularity"]
    elong = props["elongation"]
    parts: List[str] = []
    if atype == "frente_excavacion_inundado":
        parts.append(
            f"Disparada por incremento neto de lámina/humedad (ΔMNDWI={dm:+.3f} > +0.40 y MNDWI_T1 > +0.20) "
            "tras descontar cinturón de crecida uniforme, dipolos de bancos y turbidez en cauce."
        )
    elif atype == "apertura_camino_acceso":
        parts.append(
            f"Disparada por pérdida vegetal y exposición de suelo seco alargado (elongación={elong:.1f} >= 4.0, "
            f"ΔBSI_adj={db:+.3f} > +0.25, ΔNDVI_adj={dn:+.3f} < -0.30)."
        )
    else:
        parts.append(
            f"Disparada por incremento de suelo desnudo/acopio y caída de vigor vegetal "
            f"(ΔBSI_adj={db:+.3f} > +0.25, ΔNDVI_adj={dn:+.3f} < -0.30, BSI_T1 > -0.02, MNDWI_T1 < +0.10)."
        )
    parts.append(f"Morfología: rect={rect:.2f}, elong={elong:.2f}.")
    if unc["cloud_shadow_adjacent"]:
        parts.append("ATENCIÓN: borde adyacente (<=40 m) a máscara nube/sombra SCL.")
    if abs(unc["water_stage_delta_pct"]) >= 8.0:
        parts.append(f"Pulso hidrométrico regional fuerte en el tramo ({unc['water_stage_delta_pct']:+.1f}% área de agua).")
    return " ".join(parts)


def _reconstruct_diagnostic_masks(
    t0_bundles: Sequence[Dict[str, Any]],
    t1_bundle: Dict[str, Any],
    min_area_m2: float,
) -> Dict[str, Any]:
    """Recalcula exactamente las capas diagnósticas internas de Fase 2 sin alterar el algoritmo."""
    t0_base = _compute_baseline_median_indices(t0_bundles)
    t1_idx = compute_spectral_indices(
        b02=t1_bundle["B02"],
        b03=t1_bundle["B03"],
        b04=t1_bundle["B04"],
        b08=t1_bundle["B08"],
        b11=t1_bundle["B11"],
        valid_mask=t1_bundle["valid_mask"],
    )
    combined_valid = t0_base["valid_mask"] & t1_bundle["valid_mask"]
    combined_cloud_shadow = t0_base["cloud_or_shadow_mask"] | t1_bundle["cloud_or_shadow_mask"]
    grid_res_m = float(t1_bundle["grid_resolution_m"])
    pixel_area_m2 = grid_res_m * grid_res_m

    delta_mndwi = np.where(combined_valid, t1_idx["mndwi"] - t0_base["mndwi"], np.nan).astype(np.float32)
    hydro_state = compute_reach_hydrological_state(t0_base["mndwi"], t1_idx["mndwi"], combined_valid)
    bar_migration_mask, dipole_pairs = detect_natural_bar_migration_dipoles(
        delta_mndwi=delta_mndwi,
        mndwi_t0=t0_base["mndwi"],
        ndvi_t0=t0_base["ndvi"],
        valid_mask=combined_valid,
        pixel_area_m2=pixel_area_m2,
        grid_res_m=grid_res_m,
        min_area_m2=min_area_m2,
    )
    riparian_norm = normalize_seasonal_riparian_indices(
        ndvi_t0=t0_base["ndvi"],
        ndvi_t1=t1_idx["ndvi"],
        bsi_t0=t0_base["bsi"],
        bsi_t1=t1_idx["bsi"],
        valid_mask=combined_valid,
    )
    delta_bsi_adj = riparian_norm["delta_bsi_adj"]
    delta_ndvi_adj = riparian_norm["delta_ndvi_adj"]

    flooded_raw = (
        combined_valid
        & np.isfinite(delta_mndwi)
        & (delta_mndwi > 0.40)
        & (t1_idx["mndwi"] > 0.20)
        & (~hydro_state["uniform_stage_belt_mask"])
        & (~bar_migration_mask)
    )
    clearing_raw = (
        combined_valid
        & np.isfinite(delta_bsi_adj)
        & np.isfinite(delta_ndvi_adj)
        & (delta_bsi_adj > 0.25)
        & (delta_ndvi_adj < -0.30)
        & (t1_idx["bsi"] > -0.02)
        & (t1_idx["mndwi"] < 0.10)
        & (~hydro_state["uniform_stage_belt_mask"])
        & (~hydro_state["drawdown_belt_mask"])
    )
    k = np.ones((3, 3), dtype=np.uint8)
    flooded_closed = cv2.morphologyEx(flooded_raw.astype(np.uint8), cv2.MORPH_CLOSE, k) > 0
    clearing_closed = cv2.morphologyEx(clearing_raw.astype(np.uint8), cv2.MORPH_CLOSE, k) > 0

    return {
        "t0_base": t0_base,
        "t1_idx": t1_idx,
        "combined_valid": combined_valid,
        "combined_cloud_shadow": combined_cloud_shadow,
        "delta_mndwi": delta_mndwi,
        "delta_bsi_adj": np.where(combined_valid, delta_bsi_adj, np.nan).astype(np.float32),
        "delta_ndvi_adj": np.where(combined_valid, delta_ndvi_adj, np.nan).astype(np.float32),
        "hydro_state": hydro_state,
        "bar_migration_mask": bar_migration_mask,
        "dipole_pairs": dipole_pairs,
        "riparian_norm": riparian_norm,
        "flooded_mask": flooded_closed,
        "clearing_mask": clearing_closed,
    }


def _export_vectors(
    features: List[Dict[str, Any]],
    crs_epsg: int,
    geojson_path: Path,
    gpkg_path: Path,
) -> List[Dict[str, Any]]:
    """Enriquece propiedades, ordena por área y exporta a GeoJSON + GeoPackage."""
    enriched: List[Dict[str, Any]] = []
    # Ordenar por área descendente para asignar rank legible sin alterar el ID original
    sorted_feats = sorted(features, key=lambda f: float(f["properties"]["area_m2"]), reverse=True)
    rows_for_gdf: List[Dict[str, Any]] = []

    for rank, feat in enumerate(sorted_feats, start=1):
        props = dict(feat["properties"])
        props["rank_area"] = rank
        props["explanation"] = _explain_anomaly(props)
        new_feat = {
            "type": "Feature",
            "id": props["id"],
            "geometry": feat["geometry"],
            "properties": props,
        }
        enriched.append(new_feat)

        unc = props["uncertainty"]
        rows_for_gdf.append(
            {
                "rank_area": rank,
                "id": props["id"],
                "anomaly_type": props["anomaly_type"],
                "area_m2": props["area_m2"],
                "area_ha": props["area_ha"],
                "confidence": props["confidence"],
                "rectangularity": props["rectangularity"],
                "elongation": props["elongation"],
                "delta_mndwi_mean": props["delta_mndwi_mean"],
                "delta_bsi_mean": props["delta_bsi_mean"],
                "delta_ndvi_mean": props["delta_ndvi_mean"],
                "posgar_x": props["centroid_posgar"][0],
                "posgar_y": props["centroid_posgar"][1],
                "lon_wgs84": props["centroid_wgs84"][0],
                "lat_wgs84": props["centroid_wgs84"][1],
                "cloud_shadow_adjacent": unc["cloud_shadow_adjacent"],
                "valid_pixel_fraction": unc["valid_pixel_fraction"],
                "water_stage_delta_pct": unc["water_stage_delta_pct"],
                "confounder_score": unc["natural_dynamics_confounder_score"],
                "explanation": props["explanation"],
                "geometry": shape(feat["geometry"]),
            }
        )

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "OGC:CRS84"}},
        "features": enriched,
    }
    geojson_path.write_text(json.dumps(fc, indent=2, ensure_ascii=False), encoding="utf-8")

    if rows_for_gdf:
        gdf_wgs = gpd.GeoDataFrame(rows_for_gdf, crs="EPSG:4326")
        gdf_posgar = gdf_wgs.to_crs(epsg=crs_epsg)
        gdf_posgar.to_file(gpkg_path, driver="GPKG", layer="anomalias_parana_medio")
    else:
        empty_gdf = gpd.GeoDataFrame({"id": [], "geometry": []}, crs=f"EPSG:{crs_epsg}")
        empty_gdf.to_file(gpkg_path, driver="GPKG", layer="anomalias_parana_medio")

    return enriched


def _render_visual_suite(
    t0_rgb: np.ndarray,
    t1_rgb: np.ndarray,
    t0_bundle: Dict[str, Any],
    t1_bundle: Dict[str, Any],
    diag: Dict[str, Any],
    enriched_features: List[Dict[str, Any]],
    t0_meta: Dict[str, Any],
    t1_meta: Dict[str, Any],
    bounds_posgar: Tuple[float, float, float, float],
    crs_epsg: int,
) -> None:
    """Genera las láminas PNG exigidas en Etapa 3."""
    minx, miny, maxx, maxy = bounds_posgar
    extent = [minx, maxx, miny, maxy]

    # 1. Imagen T0 RGB
    fig, ax = plt.subplots(figsize=(10, 9), dpi=160)
    ax.imshow(t0_rgb, extent=extent, origin="upper")
    _format_posgar_axes(ax, bounds_posgar, crs_epsg)
    ax.set_title(
        f"RíoAlerta Exp 001 — Paraná Medio | Escena Histórica T0 ({t0_meta['acquisition_date']})\n"
        f"ID: {t0_meta['scene_id']} | Nubosidad escena: {t0_meta['cloud_cover_tile_pct']}% | "
        f"AOI válido SCL: {100.0 * t0_bundle['valid_pixel_fraction']:.2f}%",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(EXP_DIR / "01_imagen_t0_rgb.png", dpi=160)
    plt.close(fig)

    # 2. Imagen T1 RGB
    fig, ax = plt.subplots(figsize=(10, 9), dpi=160)
    ax.imshow(t1_rgb, extent=extent, origin="upper")
    _format_posgar_axes(ax, bounds_posgar, crs_epsg)
    ax.set_title(
        f"RíoAlerta Exp 001 — Paraná Medio | Escena Actual T1 ({t1_meta['acquisition_date']})\n"
        f"ID: {t1_meta['scene_id']} | Nubosidad escena: {t1_meta['cloud_cover_tile_pct']}% | "
        f"AOI válido SCL: {100.0 * t1_bundle['valid_pixel_fraction']:.2f}%",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(EXP_DIR / "02_imagen_t1_rgb.png", dpi=160)
    plt.close(fig)

    # 3. Comparación Antes / Después T0 vs T1 con máscara SCL superpuesta
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), dpi=160, sharex=True, sharey=True)
    hydro = diag["hydro_state"]
    t0_cloud = t0_bundle["cloud_or_shadow_mask"]
    t1_cloud = t1_bundle["cloud_or_shadow_mask"]

    for ax_i, rgb_i, cloud_i, label_i, w_px in (
        (
            axes[0],
            t0_rgb,
            t0_cloud,
            f"T0 ({t0_meta['acquisition_date']}) — Agua MNDWI>0.15: "
            f"{100.0 * hydro['water_area_t0_px'] / max(1, np.sum(diag['combined_valid'])):.1f}%",
            t0_meta["scene_id"],
        ),
        (
            axes[1],
            t1_rgb,
            t1_cloud,
            f"T1 ({t1_meta['acquisition_date']}) — Agua MNDWI>0.15: "
            f"{100.0 * hydro['water_area_t1_px'] / max(1, np.sum(diag['combined_valid'])):.1f}% "
            f"(Δ={hydro['water_stage_delta_pct']:+.1f}%)",
            t1_meta["scene_id"],
        ),
    ):
        ax_i.imshow(rgb_i, extent=extent, origin="upper")
        cloud_rgba = np.zeros((*cloud_i.shape, 4), dtype=np.float32)
        cloud_rgba[cloud_i] = [1.0, 0.0, 0.8, 0.65]
        ax_i.imshow(cloud_rgba, extent=extent, origin="upper")
        _format_posgar_axes(ax_i, bounds_posgar, crs_epsg)
        ax_i.set_title(f"{label_i}\n{w_px}", fontsize=10, fontweight="bold")

    axes[1].legend(
        handles=[Patch(facecolor=(1.0, 0.0, 0.8, 0.65), edgecolor="black", label="Máscara SCL nube/sombra")],
        loc="lower right",
        fontsize=8,
    )
    fig.suptitle(
        "Comparación Multitemporal T0 vs T1 — Corredor Santa Fe / Colastiné / Bajada Grande / Paraná",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.955))
    fig.savefig(EXP_DIR / "03_comparacion_antes_despues_t0_t1.png", dpi=160)
    plt.close(fig)

    # 4. Mapa triple de diferencias espectrales (ΔMNDWI, ΔBSI_adj, ΔNDVI_adj)
    fig, axes = plt.subplots(1, 3, figsize=(19, 6.8), dpi=160, sharex=True, sharey=True)
    panels = [
        ("ΔMNDWI (T1 - T0)\n(+Agua/Humedad | -Banco/Arena)", diag["delta_mndwi"], "RdBu", -0.8, 0.8),
        (
            f"ΔBSI ajustado (shift={diag['riparian_norm']['riparian_bsi_seasonal_shift']:+.3f})\n"
            "(+Suelo Desnudo/Acopio | -Agua/Vegetación)",
            diag["delta_bsi_adj"],
            "PuOr_r",
            -0.6,
            0.6,
        ),
        (
            f"ΔNDVI ajustado (shift={diag['riparian_norm']['riparian_ndvi_seasonal_shift']:+.3f})\n"
            "(+Vigor Vegetal | -Desmonte/Inundación)",
            diag["delta_ndvi_adj"],
            "RdYlGn",
            -0.8,
            0.8,
        ),
    ]
    for ax_p, (title_p, arr_p, cmap_p, vmin_p, vmax_p) in zip(axes, panels):
        cmap_obj = plt.get_cmap(cmap_p).copy()
        cmap_obj.set_bad(color="#222222")
        im = ax_p.imshow(arr_p, extent=extent, origin="upper", cmap=cmap_obj, vmin=vmin_p, vmax=vmax_p)
        _format_posgar_axes(ax_p, bounds_posgar, crs_epsg, show_scale_bar=False)
        ax_p.set_title(title_p, fontsize=10, fontweight="bold")
        fig.colorbar(im, ax=ax_p, fraction=0.046, pad=0.03)

    fig.suptitle(
        f"Diferencias Espectrales Calibradas | {t0_meta['acquisition_date']} -> {t1_meta['acquisition_date']} | Gris oscuro = Enmascarado SCL",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.94))
    fig.savefig(EXP_DIR / "04_mapa_diferencias_espectrales.png", dpi=160)
    plt.close(fig)

    # 5. Máscara diagnóstica de cambios y filtros hidrológicos
    fig, ax = plt.subplots(figsize=(11, 9.5), dpi=160)
    gray_bg = np.mean(t1_rgb.astype(np.float32), axis=2) / 255.0
    ax.imshow(gray_bg, extent=extent, origin="upper", cmap="gray", vmin=0.0, vmax=1.0)

    cat = np.zeros(gray_bg.shape, dtype=np.uint8)
    cat[diag["hydro_state"]["uniform_stage_belt_mask"]] = 1
    cat[diag["hydro_state"]["drawdown_belt_mask"]] = 2
    cat[diag["bar_migration_mask"]] = 3
    cat[diag["combined_cloud_shadow"]] = 4
    cat[diag["flooded_mask"]] = 5
    cat[diag["clearing_mask"]] = 6

    rgba_overlay = np.zeros((*cat.shape, 4), dtype=np.float32)
    palette = {
        1: [0.25, 0.70, 0.95, 0.55],
        2: [0.85, 0.70, 0.30, 0.55],
        3: [0.15, 0.85, 0.45, 0.75],
        4: [0.80, 0.20, 0.85, 0.70],
        5: [0.00, 0.35, 1.00, 0.90],
        6: [1.00, 0.22, 0.00, 0.90],
    }
    for k_id, col in palette.items():
        rgba_overlay[cat == k_id] = col
    ax.imshow(rgba_overlay, extent=extent, origin="upper")
    _format_posgar_axes(ax, bounds_posgar, crs_epsg, scale_loc="lower_right")

    legend_handles = [
        Patch(facecolor=palette[1], edgecolor="black", label=f"Cinturón crecida uniforme filtrado ({int(np.sum(cat==1))} px)"),
        Patch(facecolor=palette[3], edgecolor="black", label=f"Dipolos migración bancos filtrados ({len(diag['dipole_pairs'])} pares)"),
        Patch(facecolor=palette[4], edgecolor="black", label=f"Máscara SCL nubes/sombras ({int(np.sum(cat==4))} px)"),
        Patch(facecolor=palette[5], edgecolor="black", label=f"Candidato residual: frente_excavacion_inundado ({int(np.sum(cat==5))} px)"),
        Patch(facecolor=palette[6], edgecolor="black", label=f"Candidato residual: desmonte_ribera / camino ({int(np.sum(cat==6))} px)"),
    ]
    ax.legend(handles=legend_handles, loc="lower left", fontsize=8.5, framealpha=0.92)
    ax.set_title(
        f"Máscara Diagnóstica de Controles Naturales y Cambios Detectados\n"
        f"Pulso hidrométrico AOI: {hydro['water_stage_delta_pct']:+.2f}% área agua | "
        f"Fracción inundación orilla={hydro['shoreline_flooding_fraction']:.2f} | "
        f"Crecida sistémica={hydro['is_systemic_stage_rise']}",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(EXP_DIR / "05_mascara_cambios_detectados.png", dpi=160)
    plt.close(fig)

    # 7. Mapa final con anomalías vectoriales numeradas + focos costeros
    fig, ax = plt.subplots(figsize=(11, 9.5), dpi=170)
    ax.imshow(t1_rgb, extent=extent, origin="upper")
    _format_posgar_axes(ax, bounds_posgar, crs_epsg, scale_loc="lower_right")

    type_colors = {
        "frente_excavacion_inundado": "#00E5FF",
        "desmonte_ribera_acopio": "#FF5252",
        "apertura_camino_acceso": "#FFD600",
    }
    priority_coastal_ids = {"RIO-0184", "RIO-0225", "RIO-0199", "RIO-0365", "RIO-0027", "RIO-0093"}
    max_labels = 25
    labeled_count = 0
    for feat in enriched_features:
        props = feat["properties"]
        rank = props["rank_area"]
        atype = props["anomaly_type"]
        is_prio = props["id"] in priority_coastal_ids
        color = "#00FF66" if is_prio else type_colors.get(atype, "#FFFFFF")
        geom = gpd.GeoSeries([shape(feat["geometry"])], crs="EPSG:4326").to_crs(epsg=crs_epsg).iloc[0]
        x_ring, y_ring = geom.exterior.xy
        ax.plot(x_ring, y_ring, color=color, linewidth=1.8 if is_prio else (1.3 if rank <= max_labels else 0.7), alpha=0.95)
        ax.fill(x_ring, y_ring, color=color, alpha=0.28 if is_prio else 0.20)
        if rank <= max_labels or is_prio:
            cx_m, cy_m = props["centroid_posgar"]
            ax.text(
                cx_m,
                cy_m,
                f"#{rank}\n{props['id']}",
                color="white",
                fontsize=6.8,
                fontweight="bold",
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.14", "facecolor": "black", "alpha": 0.78, "edgecolor": color, "linewidth": 0.9},
            )
            labeled_count += 1

    legend_vec = [
        Patch(facecolor=type_colors["frente_excavacion_inundado"], edgecolor="white", label="frente_excavacion_inundado (177)"),
        Patch(facecolor=type_colors["desmonte_ribera_acopio"], edgecolor="white", label="desmonte_ribera_acopio (255)"),
        Patch(facecolor=type_colors["apertura_camino_acceso"], edgecolor="white", label="apertura_camino_acceso (20)"),
        Patch(facecolor="#00FF66", edgecolor="white", label="Focos costeros suelo brillante / canal (inspección)"),
    ]
    ax.legend(handles=legend_vec, loc="lower left", fontsize=8.5, framealpha=0.92)
    ax.set_title(
        f"Mapa Final de Anomalías Candidatas Numeradas sobre T1 ({t1_meta['acquisition_date']})\n"
        f"Total detectadas: {len(enriched_features)} polígonos (>= 1600 m²) | Etiquetadas {labeled_count} (Top-{max_labels} + focos ribera)",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(EXP_DIR / "07_mapa_final_anomalias_numeradas.png", dpi=170)
    plt.close(fig)

    # 8. Paneles de recortes zoom (Top-6 por área y Top-6 focos costeros / suelo seco real)
    _render_zoom_strip(
        selected_feats=enriched_features[:6],
        out_path=EXP_DIR / "08_recortes_zoom_anomalias_principales.png",
        title="Inspección Visual por Recortes (Top-6 Anomalías por Superficie) — Antes / Después / Espectral",
        t0_rgb=t0_rgb,
        t1_rgb=t1_rgb,
        t1_bundle=t1_bundle,
        diag=diag,
        t0_meta=t0_meta,
        t1_meta=t1_meta,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
        type_colors=type_colors,
    )
    feat_by_id = {f["properties"]["id"]: f for f in enriched_features}
    coastal_targets = [
        feat_by_id[fid]
        for fid in ("RIO-0184", "RIO-0225", "RIO-0199", "RIO-0365", "RIO-0027", "RIO-0093")
        if fid in feat_by_id
    ]
    _render_zoom_strip(
        selected_feats=coastal_targets,
        out_path=EXP_DIR / "09_recortes_zoom_sospechosos_ribera.png",
        title="Inspección Visual de Focos Costeros con Suelo Brillante Real (B11>=700) y Formas Regulares",
        t0_rgb=t0_rgb,
        t1_rgb=t1_rgb,
        t1_bundle=t1_bundle,
        diag=diag,
        t0_meta=t0_meta,
        t1_meta=t1_meta,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
        type_colors=type_colors,
    )


def _render_zoom_strip(
    selected_feats: List[Dict[str, Any]],
    out_path: Path,
    title: str,
    t0_rgb: np.ndarray,
    t1_rgb: np.ndarray,
    t1_bundle: Dict[str, Any],
    diag: Dict[str, Any],
    t0_meta: Dict[str, Any],
    t1_meta: Dict[str, Any],
    bounds_posgar: Tuple[float, float, float, float],
    crs_epsg: int,
    type_colors: Dict[str, str],
) -> None:
    """Renderiza tiras 4 columnas [RGB T0 | RGB T1 | ΔMNDWI | ΔBSI_adj] sin superposición."""
    if not selected_feats:
        return
    minx, miny, maxx, maxy = bounds_posgar
    top_n = len(selected_feats)
    fig, axes = plt.subplots(top_n, 4, figsize=(16, 3.35 * top_n + 0.6), dpi=150)
    if top_n == 1:
        axes = np.expand_dims(axes, axis=0)
    tr = t1_bundle["transform"]
    h_img, w_img = t1_rgb.shape[:2]

    for row_idx, feat in enumerate(selected_feats):
        props = feat["properties"]
        rank = props["rank_area"]
        bminx, bminy, bmaxx, bmaxy = props["bbox_posgar"]
        pad_m = max(250.0, 0.9 * max(bmaxx - bminx, bmaxy - bminy))
        zminx = max(minx, bminx - pad_m)
        zmaxx = min(maxx, bmaxx + pad_m)
        zminy = max(miny, bminy - pad_m)
        zmaxy = min(maxy, bmaxy + pad_m)

        c0 = max(0, int((zminx - tr.c) / tr.a))
        c1 = min(w_img, int((zmaxx - tr.c) / tr.a))
        r0 = max(0, int((zmaxy - tr.f) / tr.e))
        r1 = min(h_img, int((zminy - tr.f) / tr.e))
        sub_ext = [
            tr.c + c0 * tr.a,
            tr.c + c1 * tr.a,
            tr.f + r1 * tr.e,
            tr.f + r0 * tr.e,
        ]
        geom = gpd.GeoSeries([shape(feat["geometry"])], crs="EPSG:4326").to_crs(epsg=crs_epsg).iloc[0]
        rx, ry = geom.exterior.xy
        pcol = type_colors.get(props["anomaly_type"], "#FFFFFF")

        panels_row = [
            (t0_rgb[r0:r1, c0:c1], None, None, None, f"#{rank} {props['id']} | T0 RGB ({t0_meta['acquisition_date']})"),
            (t1_rgb[r0:r1, c0:c1], None, None, None, f"T1 RGB ({t1_meta['acquisition_date']}) | {props['area_ha']:.2f} ha"),
            (diag["delta_mndwi"][r0:r1, c0:c1], "RdBu", -0.8, 0.8, f"ΔMNDWI (med={props['delta_mndwi_mean']:+.2f})"),
            (diag["delta_bsi_adj"][r0:r1, c0:c1], "PuOr_r", -0.6, 0.6, f"ΔBSI_adj ({props['delta_bsi_mean']:+.2f}) | {props['anomaly_type']}"),
        ]
        for col_idx, (arr_z, cmap_z, vmin_z, vmax_z, title_z) in enumerate(panels_row):
            ax_z = axes[row_idx, col_idx]
            if cmap_z is None:
                ax_z.imshow(arr_z, extent=sub_ext, origin="upper")
            else:
                ax_z.imshow(arr_z, extent=sub_ext, origin="upper", cmap=cmap_z, vmin=vmin_z, vmax=vmax_z)
            ax_z.plot(rx, ry, color=pcol, linewidth=1.6)
            ax_z.set_title(title_z, fontsize=8.5, fontweight="bold")
            ax_z.set_xticks([])
            ax_z.set_yticks([])

    fig.suptitle(title, fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.965))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    RASTERS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    bounds_posgar, crs_epsg = bbox_wgs84_to_posgar(AOI_BBOX_WGS84)
    print(f"[Etapa 1] AOI WGS84={AOI_BBOX_WGS84} | POSGAR EPSG:{crs_epsg}={bounds_posgar}")

    client = Client.open(STAC_URL)
    target_ids = list(dict.fromkeys([T1_SCENE_ID, *T0_BASELINE_SCENE_IDS]))
    items = list(client.search(collections=[COLLECTION], ids=target_ids).items())
    items_by_id = {it.id: it for it in items}
    missing = [sid for sid in target_ids if sid not in items_by_id]
    if missing:
        raise RuntimeError(f"Faltan ítems STAC requeridos en Earth Search: {missing}")

    downloaded: Dict[str, Tuple[Path, Path, Dict[str, Any]]] = {}
    for sid in target_ids:
        prefix = "t1" if sid == T1_SCENE_ID else "t0"
        vnir_p, swir_p, meta = _fetch_or_load_scene_tifs(
            items_by_id=items_by_id,
            scene_id=sid,
            prefix=prefix,
            bounds_posgar=bounds_posgar,
            crs_epsg=crs_epsg,
        )
        downloaded[sid] = (vnir_p, swir_p, meta)
        print(f"  -> Escena {sid} ({meta['acquisition_date']}): cached={meta['cached']}")

    # Preparar rutas para Corrida A (bitemporal directa T0 2024-11-06 vs T1 2026-11-08)
    t0_prim_v, t0_prim_s, t0_prim_meta = downloaded[T0_PRIMARY_SCENE_ID]
    t1_v, t1_s, t1_meta = downloaded[T1_SCENE_ID]

    print("[Etapa 2A] Ejecutando motor Fase 2 bitemporal directo (2024-11-06 -> 2026-11-08)...")
    spectral_res_bi = compute_multitemporal_baseline_and_changes(
        t0_vnir_paths=t0_prim_v,
        t1_vnir_path=t1_v,
        out_dir=PROCESSED_DIR / "bitemporal_2024_2026",
        t0_swir_scl_paths=t0_prim_s,
        t1_swir_scl_path=t1_s,
    )
    det_bi = detect_extraction_anomalies(
        t0_vnir_paths=t0_prim_v,
        t1_vnir_path=t1_v,
        t0_swir_scl_paths=t0_prim_s,
        t1_swir_scl_path=t1_s,
        work_dir=PROCESSED_DIR / "bitemporal_2024_2026",
    )

    # Preparar rutas para Corrida B (línea base mediana 2023+2024+2025 vs T1 2026-11-08)
    t0_all_v = [downloaded[sid][0] for sid in T0_BASELINE_SCENE_IDS]
    t0_all_s = [downloaded[sid][1] for sid in T0_BASELINE_SCENE_IDS]
    print("[Etapa 2B] Ejecutando motor Fase 2 con línea base mediana 3 años (2023+2024+2025 -> 2026-11-08)...")
    spectral_res_med = compute_multitemporal_baseline_and_changes(
        t0_vnir_paths=t0_all_v,
        t1_vnir_path=t1_v,
        out_dir=PROCESSED_DIR / "mediana_2023_2025_vs_2026",
        t0_swir_scl_paths=t0_all_s,
        t1_swir_scl_path=t1_s,
    )
    det_med = detect_extraction_anomalies(
        t0_vnir_paths=t0_all_v,
        t1_vnir_path=t1_v,
        t0_swir_scl_paths=t0_all_s,
        t1_swir_scl_path=t1_s,
        work_dir=PROCESSED_DIR / "mediana_2023_2025_vs_2026",
    )

    print(
        f"  -> Resultado Bitemporal (2024->2026): {len(det_bi.features)} candidatos | "
        f"water_stage_delta_pct={det_bi.metadata['hydrological_controls']['water_stage_delta_pct']:+.2f}%"
    )
    print(
        f"  -> Resultado Mediana 3 años (2023-2025->2026): {len(det_med.features)} candidatos | "
        f"water_stage_delta_pct={det_med.metadata['hydrological_controls']['water_stage_delta_pct']:+.2f}%"
    )

    # Exportar ambos GeoJSON y seleccionar como conjunto principal la corrida bitemporal 2024->2026
    # (par estacional más limpio de nube: 1.70% vs 7.78%, comparación directa 2 años antes/después visualmente interpretable)
    t0_bundle = load_native_s2_scene_bundle(t0_prim_v, t0_prim_s, work_dir=PROCESSED_DIR)
    t1_bundle = load_native_s2_scene_bundle(t1_v, t1_s, work_dir=PROCESSED_DIR)
    diag = _reconstruct_diagnostic_masks([t0_bundle], t1_bundle, min_area_m2=settings.min_anomaly_area_m2)

    p_low, p_high = _compute_shared_rgb_bounds([t0_bundle, t1_bundle])
    t0_rgb = _bundle_to_rgb(t0_bundle, p_low, p_high)
    t1_rgb = _bundle_to_rgb(t1_bundle, p_low, p_high)

    print("[Etapa 3] Exportando vectores GeoJSON/GPKG y renderizando láminas visuales...")
    enriched_bi = _export_vectors(
        features=det_bi.features,
        crs_epsg=crs_epsg,
        geojson_path=EXP_DIR / "06_anomalias_detectadas.geojson",
        gpkg_path=EXP_DIR / "06_anomalias_detectadas.gpkg",
    )
    enriched_med = _export_vectors(
        features=det_med.features,
        crs_epsg=crs_epsg,
        geojson_path=EXP_DIR / "06_anomalias_mediana_3anios.geojson",
        gpkg_path=EXP_DIR / "06_anomalias_mediana_3anios.gpkg",
    )

    _render_visual_suite(
        t0_rgb=t0_rgb,
        t1_rgb=t1_rgb,
        t0_bundle=t0_bundle,
        t1_bundle=t1_bundle,
        diag=diag,
        enriched_features=enriched_bi,
        t0_meta=t0_prim_meta,
        t1_meta=t1_meta,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
    )

    summary_payload = {
        "experiment_id": "exp_001_parana_medio",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "aoi_wgs84": AOI_BBOX_WGS84,
        "aoi_posgar_epsg": crs_epsg,
        "aoi_bounds_posgar": bounds_posgar,
        "aoi_dimensions_m": [
            round(bounds_posgar[2] - bounds_posgar[0], 2),
            round(bounds_posgar[3] - bounds_posgar[1], 2),
        ],
        "aoi_area_ha": round(((bounds_posgar[2] - bounds_posgar[0]) * (bounds_posgar[3] - bounds_posgar[1])) / 10000.0, 2),
        "stac_endpoint": STAC_URL,
        "collection": COLLECTION,
        "parameters": {
            "grid_resolution_m": 10.0,
            "swir_scl_native_resolution_m": 20.0,
            "min_anomaly_area_m2": settings.min_anomaly_area_m2,
            "seasonal_window_days": settings.seasonal_window_days,
            "max_cloud_cover_pct": settings.max_cloud_cover_pct,
            "delta_mndwi_threshold": 0.40,
            "delta_bsi_threshold": 0.25,
            "delta_ndvi_threshold": -0.30,
        },
        "scenes": {sid: downloaded[sid][2] for sid in target_ids},
        "run_bitemporal_2024_2026": {
            "spectral": spectral_res_bi.model_dump(),
            "detection_metadata": det_bi.metadata,
            "total_candidates": len(enriched_bi),
            "by_type": {
                t: sum(1 for f in enriched_bi if f["properties"]["anomaly_type"] == t)
                for t in sorted({f["properties"]["anomaly_type"] for f in enriched_bi})
            },
            "top_anomalies": [f["properties"] for f in enriched_bi[:20]],
        },
        "run_baseline_median_2023_2025_vs_2026": {
            "spectral": spectral_res_med.model_dump(),
            "detection_metadata": det_med.metadata,
            "total_candidates": len(enriched_med),
            "by_type": {
                t: sum(1 for f in enriched_med if f["properties"]["anomaly_type"] == t)
                for t in sorted({f["properties"]["anomaly_type"] for f in enriched_med})
            },
            "top_anomalies": [f["properties"] for f in enriched_med[:20]],
        },
    }
    (EXP_DIR / "experiment_summary.json").write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] Productos guardados en {EXP_DIR}")


if __name__ == "__main__":
    main()
