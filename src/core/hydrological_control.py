"""Controles de dinámica fluvial natural y estacionalidad para evitar falsos positivos en ríos."""

from typing import Any, Dict, List, Tuple

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt


def compute_reach_hydrological_state(
    mndwi_t0: np.ndarray,
    mndwi_t1: np.ndarray,
    valid_mask: np.ndarray,
    water_threshold: float = 0.15,
) -> Dict[str, Any]:
    """Evalúa el pulso hidrométrico global del tramo (crecida/bajante) entre T0 y T1.

    Mide la variación total de lámina de agua y detecta si la ganancia hídrica se distribuye de forma
    uniforme como un cinturón perimetral continuo a lo largo de la orilla original del cauce.
    En ese caso, devuelve `uniform_stage_belt_mask` para descontar el efecto de crecida sin suprimir
    excavaciones cerradas internas ni cortes antrópicos profundos.
    """
    water_t0 = valid_mask & np.isfinite(mndwi_t0) & (mndwi_t0 > water_threshold)
    water_t1 = valid_mask & np.isfinite(mndwi_t1) & (mndwi_t1 > water_threshold)

    area_t0_px = int(np.sum(water_t0))
    area_t1_px = int(np.sum(water_t1))
    water_stage_delta_pct = (
        100.0 * float(area_t1_px - area_t0_px) / float(max(1, area_t0_px))
        if area_t0_px > 0
        else 0.0
    )

    kernel_3 = np.ones((3, 3), dtype=np.uint8)
    shoreline_ring = (cv2.dilate(water_t0.astype(np.uint8), kernel_3) > 0) & (~water_t0) & valid_mask
    water_gain = water_t1 & (~water_t0) & valid_mask
    water_loss = water_t0 & (~water_t1) & valid_mask

    shoreline_px = int(np.sum(shoreline_ring))
    shoreline_flooded_px = int(np.sum(shoreline_ring & water_t1))
    shoreline_flooding_fraction = (
        float(shoreline_flooded_px) / float(max(1, shoreline_px)) if shoreline_px > 0 else 0.0
    )

    # Anillo interno de pérdida (bajante)
    inner_shoreline_ring = water_t0 & (~(cv2.erode(water_t0.astype(np.uint8), kernel_3) > 0)) & valid_mask
    inner_px = int(np.sum(inner_shoreline_ring))
    shoreline_drawdown_fraction = (
        float(np.sum(inner_shoreline_ring & (~water_t1))) / float(max(1, inner_px))
        if inner_px > 0
        else 0.0
    )

    dist_from_water_t0 = distance_transform_edt(~water_t0)
    dist_from_land_t0 = distance_transform_edt(water_t0)

    uniform_stage_belt_mask = np.zeros(mndwi_t0.shape, dtype=bool)
    is_systemic_stage_rise = (
        water_stage_delta_pct >= 8.0 and shoreline_flooding_fraction >= 0.35 and int(np.sum(water_gain)) > 0
    )
    is_systemic_stage_drop = (
        water_stage_delta_pct <= -8.0 and shoreline_drawdown_fraction >= 0.35 and int(np.sum(water_loss)) > 0
    )

    if is_systemic_stage_rise:
        # Solo considerar píxeles de ganancia conectados al cauce principal que forman una franja uniforme
        num_labels, labels = cv2.connectedComponents(water_gain.astype(np.uint8), connectivity=8)
        for lbl in range(1, num_labels):
            comp = labels == lbl
            min_dist = float(np.min(dist_from_water_t0[comp]))
            if min_dist <= 1.5:
                d90 = float(np.percentile(dist_from_water_t0[comp], 90))
                d50 = float(np.median(dist_from_water_t0[comp]))
                # Franja perimetral relativamente uniforme paralela a la costa
                if d90 <= max(6.0, 2.2 * max(1.0, d50)):
                    uniform_stage_belt_mask |= comp & (dist_from_water_t0 <= d90 + 0.5)

    drawdown_belt_mask = np.zeros(mndwi_t0.shape, dtype=bool)
    if is_systemic_stage_drop:
        num_labels, labels = cv2.connectedComponents(water_loss.astype(np.uint8), connectivity=8)
        for lbl in range(1, num_labels):
            comp = labels == lbl
            if float(np.min(dist_from_land_t0[comp])) <= 1.5:
                d90 = float(np.percentile(dist_from_land_t0[comp], 90))
                d50 = float(np.median(dist_from_land_t0[comp]))
                if d90 <= max(6.0, 2.2 * max(1.0, d50)):
                    drawdown_belt_mask |= comp & (dist_from_land_t0 <= d90 + 0.5)

    delta_mndwi = mndwi_t1 - mndwi_t0
    interface_mask = (shoreline_ring | inner_shoreline_ring) & valid_mask
    interface_mndwi_shift = (
        float(np.nanmedian(delta_mndwi[interface_mask])) if np.any(interface_mask) else 0.0
    )

    return {
        "water_area_t0_px": area_t0_px,
        "water_area_t1_px": area_t1_px,
        "water_stage_delta_pct": round(water_stage_delta_pct, 2),
        "shoreline_flooding_fraction": round(shoreline_flooding_fraction, 3),
        "shoreline_drawdown_fraction": round(shoreline_drawdown_fraction, 3),
        "interface_mndwi_shift": round(interface_mndwi_shift, 4),
        "is_systemic_stage_rise": bool(is_systemic_stage_rise),
        "is_systemic_stage_drop": bool(is_systemic_stage_drop),
        "uniform_stage_belt_mask": uniform_stage_belt_mask,
        "drawdown_belt_mask": drawdown_belt_mask,
    }


def _extract_component_stats(
    binary_mask: np.ndarray,
    pixel_area_m2: float,
    min_area_m2: float,
) -> List[Dict[str, Any]]:
    """Extrae componentes conexas con área, centroide en píxeles y bbox."""
    comps: List[Dict[str, Any]] = []
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_mask.astype(np.uint8), connectivity=8)
    for lbl in range(1, num_labels):
        px_count = int(stats[lbl, cv2.CC_STAT_AREA])
        area_m2 = float(px_count) * pixel_area_m2
        if area_m2 < min_area_m2 * 0.5:
            continue
        x0 = int(stats[lbl, cv2.CC_STAT_LEFT])
        y0 = int(stats[lbl, cv2.CC_STAT_TOP])
        w = int(stats[lbl, cv2.CC_STAT_WIDTH])
        h = int(stats[lbl, cv2.CC_STAT_HEIGHT])
        cx, cy = float(centroids[lbl][0]), float(centroids[lbl][1])
        comps.append(
            {
                "label": lbl,
                "mask": labels == lbl,
                "area_m2": area_m2,
                "centroid_xy": (cx, cy),
                "bbox_px": (x0, y0, x0 + w, y0 + h),
            }
        )
    return comps


def detect_natural_bar_migration_dipoles(
    delta_mndwi: np.ndarray,
    mndwi_t0: np.ndarray,
    ndvi_t0: np.ndarray,
    valid_mask: np.ndarray,
    pixel_area_m2: float,
    grid_res_m: float = 10.0,
    min_area_m2: float = 1600.0,
    max_dipole_span_m: float = 450.0,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """Detecta dipolos acoplados de erosión/acreción por migración natural de bancos de arena.

    Cuando un banco de arena migra aguas abajo de forma natural, su frente aguas arriba retrocede
    (`ΔMNDWI > +0.35`, pasando a agua) y simultáneamente su cola aguas abajo acrece sedimento nuevo
    (`ΔMNDWI < -0.35`, pasando de agua a arena) con áreas comparables en el mismo corredor no
    vegetado (`NDVI_T0 < 0.35`). En contraste, una excavación antrópica extrae material fuera del
    cauce y no genera acreción compensatoria de banco nuevo aguas abajo.
    """
    erosion_bin = valid_mask & np.isfinite(delta_mndwi) & (delta_mndwi > 0.35) & (ndvi_t0 < 0.35)
    accretion_bin = valid_mask & np.isfinite(delta_mndwi) & (delta_mndwi < -0.35) & (mndwi_t0 > 0.05)

    erosion_comps = _extract_component_stats(erosion_bin, pixel_area_m2, min_area_m2)
    accretion_comps = _extract_component_stats(accretion_bin, pixel_area_m2, min_area_m2)

    migration_mask = np.zeros(delta_mndwi.shape, dtype=bool)
    dipole_pairs: List[Dict[str, Any]] = []
    used_accretion: set[int] = set()

    for er in erosion_comps:
        ecx, ecy = er["centroid_xy"]
        best_acc_idx = -1
        best_dist_m = float("inf")
        for idx, ac in enumerate(accretion_comps):
            if idx in used_accretion:
                continue
            ratio = er["area_m2"] / max(1.0, ac["area_m2"])
            if not (0.40 <= ratio <= 2.50):
                continue
            acx, acy = ac["centroid_xy"]
            dist_m = float(np.hypot(ecx - acx, ecy - acy)) * grid_res_m
            if dist_m <= max_dipole_span_m and dist_m < best_dist_m:
                best_dist_m = dist_m
                best_acc_idx = idx

        if best_acc_idx >= 0:
            ac = accretion_comps[best_acc_idx]
            used_accretion.add(best_acc_idx)
            migration_mask |= er["mask"]
            dipole_pairs.append(
                {
                    "erosion_area_m2": round(er["area_m2"], 1),
                    "accretion_area_m2": round(ac["area_m2"], 1),
                    "area_ratio": round(er["area_m2"] / max(1.0, ac["area_m2"]), 3),
                    "centroid_separation_m": round(best_dist_m, 1),
                }
            )

    return migration_mask, dipole_pairs


def normalize_seasonal_riparian_indices(
    ndvi_t0: np.ndarray,
    ndvi_t1: np.ndarray,
    bsi_t0: np.ndarray,
    bsi_t1: np.ndarray,
    valid_mask: np.ndarray,
) -> Dict[str, Any]:
    """Descuenta el corrimiento fenológico/estacional mediano de la vegetación ribereña.

    Si todo el bosque en galería sufre senescencia otoñal o estrés hídrico uniforme, la mediana
    regional absorbe esa deriva (`delta_ndvi_adj ≈ 0`, `delta_bsi_adj ≈ 0`), preservando únicamente
    los desmontes localizados y acopios abruptos de áridos.
    """
    delta_ndvi = ndvi_t1 - ndvi_t0
    delta_bsi = bsi_t1 - bsi_t0

    veg_mask_t0 = (
        valid_mask
        & np.isfinite(ndvi_t0)
        & np.isfinite(delta_ndvi)
        & np.isfinite(delta_bsi)
        & (ndvi_t0 > 0.45)
    )

    if int(np.sum(veg_mask_t0)) >= 20:
        ndvi_shift = float(np.nanmedian(delta_ndvi[veg_mask_t0]))
        bsi_shift = float(np.nanmedian(delta_bsi[veg_mask_t0]))
    else:
        ndvi_shift = 0.0
        bsi_shift = 0.0

    # Solo compensar descensos estacionales de NDVI / aumentos estacionales de BSI en vegetación;
    # no amplificar cuando el fondo no tuvo caída fenológica
    ndvi_offset = min(0.0, ndvi_shift)
    bsi_offset = max(0.0, bsi_shift)

    delta_ndvi_adj = delta_ndvi - ndvi_offset
    delta_bsi_adj = delta_bsi - bsi_offset

    return {
        "delta_ndvi_raw": delta_ndvi.astype(np.float32),
        "delta_bsi_raw": delta_bsi.astype(np.float32),
        "delta_ndvi_adj": delta_ndvi_adj.astype(np.float32),
        "delta_bsi_adj": delta_bsi_adj.astype(np.float32),
        "riparian_ndvi_seasonal_shift": round(ndvi_shift, 4),
        "riparian_bsi_seasonal_shift": round(bsi_shift, 4),
        "riparian_veg_pixels": int(np.sum(veg_mask_t0)),
    }


def compute_turbidity_confounder_flag(
    mndwi_t0: np.ndarray,
    b04_t0: np.ndarray,
    b04_t1: np.ndarray,
    candidate_mask: np.ndarray,
) -> bool:
    """Detecta si un candidato está dominado por un pulso de turbidez dentro del cauce preexistente."""
    vals_mndwi0 = mndwi_t0[candidate_mask & np.isfinite(mndwi_t0)]
    if vals_mndwi0.size == 0:
        return False
    was_open_water = float(np.nanmean(vals_mndwi0)) > 0.20
    red_t0 = b04_t0[candidate_mask & np.isfinite(b04_t0)]
    red_t1 = b04_t1[candidate_mask & np.isfinite(b04_t1)]
    if red_t0.size == 0 or red_t1.size == 0:
        return was_open_water
    red_increase = float(np.nanmean(red_t1) - np.nanmean(red_t0)) > 150.0
    return bool(was_open_water and red_increase)
