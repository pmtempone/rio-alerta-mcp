"""Controles de dinámica fluvial natural y estacionalidad para evitar falsos positivos en ríos."""

from typing import Any, Dict, List, Optional, Tuple

import cv2
import httpx
import numpy as np
from scipy.ndimage import distance_transform_edt

from src.config import settings


def fetch_ina_stage_height_with_provenance(
    series_id: int,
    target_date_iso: str,
    timeout_seconds: float = 10.0,
) -> Tuple[Optional[float], str]:
    """Consulta la altura hidrométrica diaria oficial (m) en la API pública A5 del INA.

    Retorna `(valor_m, procedencia)` donde `procedencia` es `"INA_API_A5"` si hubo
    observaciones válidas o `"NO_DISPONIBLE"` si falló la consulta o no hubo datos.
    """
    url = (
        f"https://alerta.ina.gob.ar/a5/obs/puntual/series/{series_id}/observaciones"
        f"?timestart={target_date_iso}T00:00:00Z&timeend={target_date_iso}T23:59:59Z"
    )
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None, "NO_DISPONIBLE"
            payload = resp.json()
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        vals = [float(obs["valor"]) for obs in rows if isinstance(obs, dict) and obs.get("valor") is not None]
        if not vals:
            return None, "NO_DISPONIBLE"
        return round(sum(vals) / len(vals), 3), "INA_API_A5"
    except Exception:
        return None, "NO_DISPONIBLE"


def fetch_ina_stage_height(
    series_id: int,
    target_date_iso: str,
    timeout_seconds: float = 10.0,
) -> Optional[float]:
    """Consulta la altura hidrométrica diaria oficial (m) en la API pública A5 del INA."""
    val, _ = fetch_ina_stage_height_with_provenance(
        series_id=series_id,
        target_date_iso=target_date_iso,
        timeout_seconds=timeout_seconds,
    )
    return val


def compute_reach_hydrological_state(
    mndwi_t0: np.ndarray,
    mndwi_t1: np.ndarray,
    valid_mask: np.ndarray,
    water_threshold: Optional[float] = None,
    stage_t0_m: Optional[float] = None,
    stage_t1_m: Optional[float] = None,
    stage_rise_alert_m: Optional[float] = None,
) -> Dict[str, Any]:
    """Evalúa el pulso hidrométrico global del tramo (crecida/bajante) entre T0 y T1.

    Mide la variación total de lámina de agua y detecta si la ganancia hídrica se distribuye de forma
    uniforme como un cinturón perimetral continuo a lo largo de la orilla original del cauce.
    En ese caso, devuelve `uniform_stage_belt_mask` para descontar el efecto de crecida sin suprimir
    excavaciones cerradas internas ni cortes antrópicos profundos.
    """
    eff_water_thr = float(
        water_threshold if water_threshold is not None else settings.reach_water_mndwi_threshold
    )
    eff_stage_alert = float(
        stage_rise_alert_m if stage_rise_alert_m is not None else settings.hydrometric_stage_rise_alert_m
    )
    water_t0 = valid_mask & np.isfinite(mndwi_t0) & (mndwi_t0 > eff_water_thr)
    water_t1 = valid_mask & np.isfinite(mndwi_t1) & (mndwi_t1 > eff_water_thr)

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

    stage_delta_m = (
        round(float(stage_t1_m) - float(stage_t0_m), 3)
        if stage_t0_m is not None and stage_t1_m is not None
        else None
    )

    uniform_stage_belt_mask = np.zeros(mndwi_t0.shape, dtype=bool)
    warn_pct = settings.water_stage_warning_pct
    min_shore_frac = settings.shoreline_change_min_fraction
    strong_pct = settings.systemic_stage_strong_pct
    is_systemic_stage_rise = int(np.sum(water_gain)) > 0 and (
        (
            water_stage_delta_pct >= warn_pct
            and (shoreline_flooding_fraction >= min_shore_frac or water_stage_delta_pct >= strong_pct)
        )
        or (stage_delta_m is not None and stage_delta_m >= eff_stage_alert)
    )
    is_systemic_stage_drop = (
        water_stage_delta_pct <= -warn_pct
        and shoreline_drawdown_fraction >= min_shore_frac
        and int(np.sum(water_loss)) > 0
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
        "stage_t0_m": stage_t0_m,
        "stage_t1_m": stage_t1_m,
        "stage_delta_m": stage_delta_m,
        "shoreline_flooding_fraction": round(shoreline_flooding_fraction, 3),
        "shoreline_drawdown_fraction": round(shoreline_drawdown_fraction, 3),
        "interface_mndwi_shift": round(interface_mndwi_shift, 4),
        "is_systemic_stage_rise": bool(is_systemic_stage_rise),
        "is_systemic_stage_drop": bool(is_systemic_stage_drop),
        "uniform_stage_belt_mask": uniform_stage_belt_mask,
        "drawdown_belt_mask": drawdown_belt_mask,
    }


def classify_aoi_geomorphology(
    mndwi_t0: np.ndarray,
    ndvi_t0: np.ndarray,
    valid_mask: np.ndarray,
    scl_t0: Optional[np.ndarray] = None,
    grid_res_m: float = 10.0,
) -> Dict[str, Any]:
    """Clasifica el AOI en 4 unidades geomorfológicas a partir de la línea base T0.

    Unidades:
    - `cauce_activo`: cuerpos de agua conexos >= 5 ha (o componente mayor) en T0.
    - `barras_moviles`: sedimento no vegetado (`NDVI_T0 < 0.25`) dentro de 300 m del cauce activo.
    - `planicie_islas`: lagunas internas (< 5 ha), su entorno aluvial (<= 450 m) y sectores isleños
      encerrados entre brazos activos de agua.
    - `ribera_firme`: márgenes continentales consolidadas fuera de las zonas anteriores.
    """
    scl6 = (scl_t0 == 6) if scl_t0 is not None else np.zeros(mndwi_t0.shape, dtype=bool)
    scl5 = (scl_t0 == 5) if scl_t0 is not None else (mndwi_t0 > -0.15)
    water_t0 = valid_mask & np.isfinite(mndwi_t0) & ((mndwi_t0 > settings.geomorph_water_mndwi_min) | scl6)

    px_area_m2 = grid_res_m * grid_res_m
    min_channel_px = max(50, int(round(settings.geomorph_min_channel_area_m2 / max(1.0, px_area_m2))))

    num_w, w_labels, w_stats, _ = cv2.connectedComponentsWithStats(water_t0.astype(np.uint8), connectivity=8)
    cauce_activo = np.zeros(mndwi_t0.shape, dtype=bool)
    internal_lagoons = np.zeros(mndwi_t0.shape, dtype=bool)
    largest_lbl = 0
    largest_area = 0
    for lbl in range(1, num_w):
        area_px = int(w_stats[lbl, cv2.CC_STAT_AREA])
        if area_px > largest_area:
            largest_area = area_px
            largest_lbl = lbl
        if area_px >= min_channel_px:
            cauce_activo |= w_labels == lbl
        else:
            internal_lagoons |= w_labels == lbl

    if not np.any(cauce_activo) and largest_lbl > 0:
        cauce_activo = w_labels == largest_lbl
        internal_lagoons &= ~cauce_activo

    dist_cauce_m = distance_transform_edt(~cauce_activo) * grid_res_m
    barras_moviles = (
        (~water_t0)
        & valid_mask
        & scl5
        & np.isfinite(ndvi_t0)
        & (ndvi_t0 < settings.geomorph_bar_max_ndvi)
        & (dist_cauce_m <= settings.geomorph_bar_max_dist_m)
    )

    # Detectar sectores entre brazos activos (islas aluviales) mediante barrido direccional N/S y E/O
    has_north = np.maximum.accumulate(cauce_activo, axis=0)
    has_south = np.maximum.accumulate(cauce_activo[::-1, :], axis=0)[::-1, :]
    has_west = np.maximum.accumulate(cauce_activo, axis=1)
    has_east = np.maximum.accumulate(cauce_activo[:, ::-1], axis=1)[:, ::-1]
    between_branches = (has_north & has_south) | (has_west & has_east)

    if np.any(internal_lagoons):
        dist_lagoon_m = distance_transform_edt(~internal_lagoons) * grid_res_m
        lagoon_belt = dist_lagoon_m <= settings.geomorph_lagoon_buffer_m
    else:
        lagoon_belt = np.zeros(mndwi_t0.shape, dtype=bool)

    planicie_islas = (
        valid_mask
        & (~cauce_activo)
        & (~barras_moviles)
        & (internal_lagoons | lagoon_belt | (between_branches & (dist_cauce_m <= settings.geomorph_island_max_dist_m)))
    )
    ribera_firme = valid_mask & (~cauce_activo) & (~barras_moviles) & (~planicie_islas)

    return {
        "cauce_activo": cauce_activo,
        "barras_moviles": barras_moviles,
        "planicie_islas": planicie_islas,
        "ribera_firme": ribera_firme,
        "dist_cauce_m": dist_cauce_m,
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
    max_dipole_span_m: Optional[float] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """Detecta dipolos acoplados de erosión/acreción por migración natural de bancos de arena.

    Cuando un banco de arena migra aguas abajo de forma natural, su frente aguas arriba retrocede
    (`ΔMNDWI > +0.35`, pasando a agua) y simultáneamente su cola aguas abajo acrece sedimento nuevo
    (`ΔMNDWI < -0.35`, pasando de agua a arena) con áreas comparables en el mismo corredor no
    vegetado (`NDVI_T0 < 0.35`). En contraste, una excavación antrópica extrae material fuera del
    cauce y no genera acreción compensatoria de banco nuevo aguas abajo.
    """
    eff_span_m = float(max_dipole_span_m if max_dipole_span_m is not None else settings.dipole_max_span_m)
    min_dm = settings.dipole_min_abs_delta_mndwi
    erosion_bin = (
        valid_mask
        & np.isfinite(delta_mndwi)
        & (delta_mndwi > min_dm)
        & (ndvi_t0 < settings.dipole_max_erosion_ndvi)
    )
    accretion_bin = (
        valid_mask
        & np.isfinite(delta_mndwi)
        & (delta_mndwi < -min_dm)
        & (mndwi_t0 > settings.dipole_min_accretion_mndwi_t0)
    )

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
            if not (settings.dipole_min_area_ratio <= ratio <= settings.dipole_max_area_ratio):
                continue
            acx, acy = ac["centroid_xy"]
            dist_m = float(np.hypot(ecx - acx, ecy - acy)) * grid_res_m
            if dist_m <= eff_span_m and dist_m < best_dist_m:
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
        & (ndvi_t0 > settings.riparian_veg_min_ndvi_t0)
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
    """Detecta si un candidato ya pertenecía al cauce/barra húmeda en T0 o está dominado por turbidez.

    Reglas físicas:
    1. Si `mean_mndwi0 > settings.turbidity_open_water_mndwi` (0.20), ya era agua abierta en T0 -> True.
    2. Si `mean_mndwi0 <= settings.turbidity_wet_bar_mndwi` (0.05), era tierra emergida/vegetación/barra seca -> False.
    3. En la franja de transición húmeda (`0.05 < mean_mndwi0 <= 0.20`), se marca como confusor si
       hay variación intensa en el rojo (`|ΔB04| > 150 DN` por pulso de turbidez o sumersión de banco)
       o si `B04_T0 > 750 DN` (banco somero brillante en T0).
    """
    vals_mndwi0 = mndwi_t0[candidate_mask & np.isfinite(mndwi_t0)]
    if vals_mndwi0.size == 0:
        return False
    mean_mndwi0 = float(np.nanmean(vals_mndwi0))
    if mean_mndwi0 > settings.turbidity_open_water_mndwi:
        return True
    if mean_mndwi0 <= settings.turbidity_wet_bar_mndwi:
        return False

    red_t0 = b04_t0[candidate_mask & np.isfinite(b04_t0)]
    red_t1 = b04_t1[candidate_mask & np.isfinite(b04_t1)]
    if red_t0.size == 0 or red_t1.size == 0:
        return True
    mean_r0 = float(np.nanmean(red_t0))
    mean_r1 = float(np.nanmean(red_t1))
    red_shift = abs(mean_r1 - mean_r0) > settings.turbidity_red_shift_dn
    bright_bar_t0 = mean_r0 > settings.turbidity_bright_bar_b04_dn
    return bool(red_shift or bright_bar_t0)
