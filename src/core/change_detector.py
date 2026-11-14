"""Motor multitemporal de índices espectrales y detección morfológica de extracción de áridos."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, mapping

from src.config import settings
from src.core.crs_manager import reproject_geometry
from src.core.hydrological_control import (
    compute_reach_hydrological_state,
    compute_turbidity_confounder_flag,
    detect_natural_bar_migration_dipoles,
    normalize_seasonal_riparian_indices,
)
from src.core.optical_providers import load_native_s2_scene_bundle
from src.schemas import (
    ChangeAnomalyTypeLiteral,
    ChangeDetectionResult,
    ExtractionAnomalyFeature,
    SpectralChangeResult,
    UncertaintyMetadata,
)


def compute_spectral_indices(
    b02: np.ndarray,
    b03: np.ndarray,
    b04: np.ndarray,
    b08: np.ndarray,
    b11: np.ndarray,
    valid_mask: Optional[np.ndarray] = None,
) -> Dict[str, np.ndarray]:
    """Calcula MNDWI, BSI y NDVI en Float32 preservando NaN fuera de `valid_mask`."""
    blue = b02.astype(np.float64)
    green = b03.astype(np.float64)
    red = b04.astype(np.float64)
    nir = b08.astype(np.float64)
    swir1 = b11.astype(np.float64)

    mndwi = (green - swir1) / (green + swir1 + 1e-6)
    bsi = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue) + 1e-6)
    ndvi = (nir - red) / (nir + red + 1e-6)

    if valid_mask is not None:
        mndwi = np.where(valid_mask, mndwi, np.nan)
        bsi = np.where(valid_mask, bsi, np.nan)
        ndvi = np.where(valid_mask, ndvi, np.nan)

    return {
        "mndwi": mndwi.astype(np.float32),
        "bsi": bsi.astype(np.float32),
        "ndvi": ndvi.astype(np.float32),
    }


def _write_float32_geotiff(
    arr: np.ndarray,
    out_path: Path,
    crs_epsg: int,
    transform: Any,
    nodata: float = -9999.0,
) -> str:
    """Escribe un GeoTIFF Float32 en POSGAR 2007 convirtiendo NaN a nodata explícito."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = np.where(np.isfinite(arr), arr, nodata).astype(np.float32)
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(crs_epsg),
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return str(out_path)


def _compute_baseline_median_indices(
    t0_bundles: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Construye una línea base T0 robusta por mediana libre de nubes si hay múltiples escenas."""
    if not t0_bundles:
        raise ValueError("Se requiere al menos una escena de línea base T0.")

    ref = t0_bundles[0]
    mndwi_stack = []
    bsi_stack = []
    ndvi_stack = []
    b04_stack = []
    valid_any = np.zeros(ref["shape_hw"], dtype=bool)
    cloud_any = np.zeros(ref["shape_hw"], dtype=bool)

    for bundle in t0_bundles:
        if bundle["shape_hw"] != ref["shape_hw"] or bundle["crs_epsg"] != ref["crs_epsg"]:
            raise ValueError("Las escenas de línea base T0 no comparten la misma grilla POSGAR 2007.")
        idx = compute_spectral_indices(
            b02=bundle["B02"],
            b03=bundle["B03"],
            b04=bundle["B04"],
            b08=bundle["B08"],
            b11=bundle["B11"],
            valid_mask=bundle["valid_mask"],
        )
        mndwi_stack.append(idx["mndwi"])
        bsi_stack.append(idx["bsi"])
        ndvi_stack.append(idx["ndvi"])
        b04_stack.append(bundle["B04"])
        valid_any |= bundle["valid_mask"]
        cloud_any |= bundle["cloud_or_shadow_mask"]

    if len(t0_bundles) == 1:
        return {
            "mndwi": mndwi_stack[0],
            "bsi": bsi_stack[0],
            "ndvi": ndvi_stack[0],
            "b04": b04_stack[0],
            "valid_mask": valid_any,
            "cloud_or_shadow_mask": cloud_any,
            "scenes_count": 1,
        }

    return {
        "mndwi": np.nanmedian(np.stack(mndwi_stack, axis=0), axis=0).astype(np.float32),
        "bsi": np.nanmedian(np.stack(bsi_stack, axis=0), axis=0).astype(np.float32),
        "ndvi": np.nanmedian(np.stack(ndvi_stack, axis=0), axis=0).astype(np.float32),
        "b04": np.nanmedian(np.stack(b04_stack, axis=0), axis=0).astype(np.float32),
        "valid_mask": valid_any,
        "cloud_or_shadow_mask": cloud_any,
        "scenes_count": len(t0_bundles),
    }


def _array_stats(arr: np.ndarray, prefix: str) -> Dict[str, float]:
    vals = arr[np.isfinite(arr)]
    if vals.size == 0:
        return {
            f"{prefix}_min": 0.0,
            f"{prefix}_max": 0.0,
            f"{prefix}_mean": 0.0,
            f"{prefix}_std": 0.0,
        }
    return {
        f"{prefix}_min": round(float(np.min(vals)), 4),
        f"{prefix}_max": round(float(np.max(vals)), 4),
        f"{prefix}_mean": round(float(np.mean(vals)), 4),
        f"{prefix}_std": round(float(np.std(vals)), 4),
    }


def _normalize_scene_paths(
    vnir_paths: Union[str, Path, Sequence[Union[str, Path]]],
    swir_paths: Optional[Union[str, Path, Sequence[Union[str, Path]]]],
) -> Tuple[List[Union[str, Path]], List[Optional[Union[str, Path]]]]:
    """Normaliza rutas individuales o secuencias de escenas T0 a listas emparejadas."""
    vnir_list: List[Union[str, Path]] = (
        [vnir_paths] if isinstance(vnir_paths, (str, Path)) else list(vnir_paths)
    )
    if swir_paths is None:
        swir_list: List[Optional[Union[str, Path]]] = [None] * len(vnir_list)
    elif isinstance(swir_paths, (str, Path)):
        swir_list = [swir_paths]
    else:
        swir_list = list(swir_paths)

    if len(swir_list) != len(vnir_list):
        raise ValueError("La cantidad de rutas SWIR/SCL T0 debe coincidir con la de rutas VNIR T0.")
    return vnir_list, swir_list


def compute_multitemporal_baseline_and_changes(
    t0_vnir_paths: Union[str, Path, Sequence[Union[str, Path]]],
    t1_vnir_path: Union[str, Path],
    out_dir: Optional[Union[str, Path]] = None,
    t0_swir_scl_paths: Optional[Union[str, Path, Sequence[Union[str, Path]]]] = None,
    t1_swir_scl_path: Optional[Union[str, Path]] = None,
) -> SpectralChangeResult:
    """Calcula ΔMNDWI, ΔBSI y ΔNDVI entre línea base T0 (1 o N escenas) y T1 en POSGAR 2007."""
    target_dir = Path(out_dir) if out_dir else settings.processed_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    t0_list, t0_swir_list = _normalize_scene_paths(t0_vnir_paths, t0_swir_scl_paths)

    t0_bundles = [
        load_native_s2_scene_bundle(v_p, s_p, work_dir=target_dir)
        for v_p, s_p in zip(t0_list, t0_swir_list)
    ]
    t1_bundle = load_native_s2_scene_bundle(t1_vnir_path, t1_swir_scl_path, work_dir=target_dir)

    if t0_bundles[0]["shape_hw"] != t1_bundle["shape_hw"] or t0_bundles[0]["crs_epsg"] != t1_bundle["crs_epsg"]:
        raise ValueError("La grilla de T0 y T1 no coincide en dimensiones o código EPSG POSGAR 2007.")

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
    delta_mndwi = np.where(combined_valid, t1_idx["mndwi"] - t0_base["mndwi"], np.nan).astype(np.float32)
    delta_bsi = np.where(combined_valid, t1_idx["bsi"] - t0_base["bsi"], np.nan).astype(np.float32)
    delta_ndvi = np.where(combined_valid, t1_idx["ndvi"] - t0_base["ndvi"], np.nan).astype(np.float32)

    crs_epsg = int(t1_bundle["crs_epsg"])
    transform = t1_bundle["transform"]
    stem = Path(t1_vnir_path).stem

    dm_path = _write_float32_geotiff(delta_mndwi, target_dir / f"{stem}_delta_mndwi.tif", crs_epsg, transform)
    db_path = _write_float32_geotiff(delta_bsi, target_dir / f"{stem}_delta_bsi.tif", crs_epsg, transform)
    dn_path = _write_float32_geotiff(delta_ndvi, target_dir / f"{stem}_delta_ndvi.tif", crs_epsg, transform)

    hydro = compute_reach_hydrological_state(t0_base["mndwi"], t1_idx["mndwi"], combined_valid)
    stats: Dict[str, float] = {
        **_array_stats(delta_mndwi, "delta_mndwi"),
        **_array_stats(delta_bsi, "delta_bsi"),
        **_array_stats(delta_ndvi, "delta_ndvi"),
        "water_stage_delta_pct": float(hydro["water_stage_delta_pct"]),
    }

    return SpectralChangeResult(
        status="success",
        delta_mndwi_path=dm_path,
        delta_bsi_path=db_path,
        delta_ndvi_path=dn_path,
        crs_epsg=crs_epsg,
        resolution_m=float(t1_bundle["grid_resolution_m"]),
        effective_resolution_swir_m=float(t1_bundle["swir_native_resolution_m"]),
        valid_pixel_fraction=round(float(np.mean(combined_valid)), 4) if combined_valid.size > 0 else 0.0,
        bounds_posgar=t1_bundle["bounds_posgar"],
        stats=stats,
    )


def _polygonize_mask_components(
    binary_mask: np.ndarray,
    anomaly_type: ChangeAnomalyTypeLiteral,
    transform: Any,
    crs_epsg: int,
    to_wgs: Transformer,
    delta_mndwi: np.ndarray,
    delta_bsi: np.ndarray,
    delta_ndvi: np.ndarray,
    valid_mask: np.ndarray,
    cloud_shadow_dilated: np.ndarray,
    hydro_state: Dict[str, Any],
    mndwi_t0: np.ndarray,
    b04_t0: np.ndarray,
    b04_t1: np.ndarray,
    effective_res_m: float,
    min_area_m2: float,
    persistence_count: int,
    start_index: int,
) -> List[ExtractionAnomalyFeature]:
    """Segmenta contornos OpenCV, mide geometría métrica POSGAR 2007 y construye incertidumbre."""
    # Clausura morfológica 3x3 para consolidar frentes y evitar fragmentación de bordes 20m
    kernel = np.ones((3, 3), dtype=np.uint8)
    cleaned = cv2.morphologyEx(binary_mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    features: List[ExtractionAnomalyFeature] = []
    idx = start_index

    px_w = float(abs(transform.a))
    px_h = float(abs(transform.e))
    h_img, w_img = binary_mask.shape

    for cnt in contours:
        if len(cnt) < 3:
            continue
        pts_metric: List[Tuple[float, float]] = []
        for pt in cnt.reshape(-1, 2):
            col, row = int(pt[0]), int(pt[1])
            mx = float(transform.c + (col + 0.5) * transform.a)
            my = float(transform.f + (row + 0.5) * transform.e)
            pts_metric.append((mx, my))

        poly = Polygon(pts_metric)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue

        # Compensar la reducción de medio píxel que introduce el contorno por centros de celda
        # usando conteo exacto de píxeles en máscara para área si fuera necesario, o polígono rasterizado
        comp_mask = np.zeros((h_img, w_img), dtype=np.uint8)
        cv2.drawContours(comp_mask, [cnt], -1, 1, thickness=-1)
        comp_bool = comp_mask.astype(bool)
        pixel_area_total = float(np.sum(comp_bool)) * px_w * px_h
        area_m2 = max(float(poly.area), pixel_area_total)
        if area_m2 < min_area_m2:
            continue

        # Descartar si el candidato está dominado por pulso de turbidez dentro del cauce preexistente
        if anomaly_type == "frente_excavacion_inundado" and compute_turbidity_confounder_flag(
            mndwi_t0=mndwi_t0,
            b04_t0=b04_t0,
            b04_t1=b04_t1,
            candidate_mask=comp_bool,
        ):
            continue

        min_rect = poly.minimum_rotated_rectangle
        rect_area = float(min_rect.area) if min_rect is not None and min_rect.area > 0 else area_m2
        rectangularity = float(min(1.0, max(0.0, poly.area / rect_area)))

        coords_rect = list(min_rect.exterior.coords) if min_rect is not None else []
        if len(coords_rect) >= 4:
            e1 = float(np.hypot(coords_rect[1][0] - coords_rect[0][0], coords_rect[1][1] - coords_rect[0][1]))
            e2 = float(np.hypot(coords_rect[2][0] - coords_rect[1][0], coords_rect[2][1] - coords_rect[1][1]))
            short_side = max(1e-3, min(e1, e2))
            long_side = max(short_side, max(e1, e2))
            elongation = max(1.0, long_side / short_side)
        else:
            elongation = 1.0

        eff_type: ChangeAnomalyTypeLiteral = anomaly_type
        if anomaly_type == "desmonte_ribera_acopio" and elongation >= 4.0:
            eff_type = "apertura_camino_acceso"

        dm_vals = delta_mndwi[comp_bool & np.isfinite(delta_mndwi)]
        db_vals = delta_bsi[comp_bool & np.isfinite(delta_bsi)]
        dn_vals = delta_ndvi[comp_bool & np.isfinite(delta_ndvi)]
        dm_mean = float(np.mean(dm_vals)) if dm_vals.size > 0 else 0.0
        db_mean = float(np.mean(db_vals)) if db_vals.size > 0 else 0.0
        dn_mean = float(np.mean(dn_vals)) if dn_vals.size > 0 else 0.0

        valid_frac = float(np.mean(valid_mask[comp_bool])) if np.any(comp_bool) else 0.0
        cloud_adj = bool(np.any(cloud_shadow_dilated[comp_bool]))

        limitations: List[str] = [
            f"Resolución física limitante {effective_res_m:.0f} m (bandas SWIR B11/B12 a 20 m nativos)."
        ]
        if cloud_adj:
            limitations.append("Contorno adyacente a píxeles de nube o sombra en máscara SCL (buffer <= 40 m).")
        if abs(float(hydro_state["water_stage_delta_pct"])) >= 8.0:
            limitations.append(
                f"Variación hidrométrica regional detectada en el tramo ({hydro_state['water_stage_delta_pct']:+.1f}%)."
            )
        if persistence_count == 1:
            limitations.append("Evaluación basada en única escena histórica T0 (sin serie multitemporal extensa).")

        confounder_score = min(
            1.0,
            (0.35 if cloud_adj else 0.0)
            + (0.25 if abs(float(hydro_state["water_stage_delta_pct"])) >= 8.0 else 0.0)
            + max(0.0, 1.0 - valid_frac),
        )
        signal_strength = (
            min(1.0, max(0.0, abs(dm_mean) / 0.8))
            if eff_type == "frente_excavacion_inundado"
            else min(1.0, max(0.0, (abs(db_mean) + abs(dn_mean)) / 1.2))
        )
        confidence = round(
            float(np.clip(0.55 * signal_strength + 0.30 * rectangularity + 0.15 * valid_frac - 0.20 * confounder_score, 0.05, 0.99)),
            3,
        )

        centroid_pt = poly.centroid
        cx_m, cy_m = float(centroid_pt.x), float(centroid_pt.y)
        lon_c, lat_c = to_wgs.transform(cx_m, cy_m)
        minx, miny, maxx, maxy = [float(v) for v in poly.bounds]
        geom_wgs = reproject_geometry(dict(mapping(poly)), f"EPSG:{crs_epsg}", "EPSG:4326")

        uncertainty = UncertaintyMetadata(
            effective_resolution_m=effective_res_m,
            valid_pixel_fraction=round(valid_frac, 3),
            cloud_shadow_adjacent=cloud_adj,
            water_stage_delta_pct=float(hydro_state["water_stage_delta_pct"]),
            natural_dynamics_confounder_score=round(confounder_score, 3),
            persistence_scenes_count=persistence_count,
            limitations=limitations,
        )

        features.append(
            ExtractionAnomalyFeature(
                id=f"RIO-{idx:04d}",
                anomaly_type=eff_type,
                confidence=confidence,
                area_m2=round(area_m2, 2),
                area_ha=round(area_m2 / 10000.0, 4),
                rectangularity=round(rectangularity, 3),
                elongation=round(elongation, 3),
                delta_mndwi_mean=round(dm_mean, 4),
                delta_bsi_mean=round(db_mean, 4),
                delta_ndvi_mean=round(dn_mean, 4),
                bbox_posgar=(minx, miny, maxx, maxy),
                centroid_posgar=(round(cx_m, 2), round(cy_m, 2)),
                centroid_wgs84=(round(float(lon_c), 6), round(float(lat_c), 6)),
                crs_epsg=crs_epsg,
                geometry_geojson=geom_wgs,
                effective_resolution_m=effective_res_m,
                natural_filter_status="CANDIDATO_RESIDUAL",
                uncertainty=uncertainty,
            )
        )
        idx += 1

    return features


def detect_extraction_anomalies(
    t0_vnir_paths: Union[str, Path, Sequence[Union[str, Path]]],
    t1_vnir_path: Union[str, Path],
    t0_swir_scl_paths: Optional[Union[str, Path, Sequence[Union[str, Path]]]] = None,
    t1_swir_scl_path: Optional[Union[str, Path]] = None,
    min_area_m2: Optional[float] = None,
    work_dir: Optional[Union[str, Path]] = None,
) -> ChangeDetectionResult:
    """Detecta anomalías morfológicas candidatas filtrando cambios fluviales naturales y nubes SCL.

    Aplica controles explícitos para:
    - Máscara de nubes/sombras/defectos `SCL` y exclusión de píxeles inválidos.
    - Pulso hidrométrico uniforme (crecida / bajante de nivel del río).
    - Dipolos acoplados de migración natural de bancos de arena.
    - Deriva fenológica/estacional uniforme de la vegetación ribereña.
    - Piso de área física acorde a la resolución nativa SWIR (`>= 1600 m²`).

    Nunca etiqueta un polígono como ilegal de forma autónoma: las detecciones constituyen
    candidatos morfológicos con registro de incertidumbre para cruce catastral y revisión humana.
    """
    eff_min_area = float(min_area_m2 if min_area_m2 is not None else settings.min_anomaly_area_m2)
    target_dir = Path(work_dir) if work_dir else settings.processed_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    t0_list, t0_swir_list = _normalize_scene_paths(t0_vnir_paths, t0_swir_scl_paths)

    t0_bundles = [
        load_native_s2_scene_bundle(v_p, s_p, work_dir=target_dir)
        for v_p, s_p in zip(t0_list, t0_swir_list)
    ]
    t1_bundle = load_native_s2_scene_bundle(t1_vnir_path, t1_swir_scl_path, work_dir=target_dir)

    if t0_bundles[0]["shape_hw"] != t1_bundle["shape_hw"] or t0_bundles[0]["crs_epsg"] != t1_bundle["crs_epsg"]:
        raise ValueError("La grilla espacial entre T0 y T1 no coincide.")

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
    swir_native_res_m = float(t1_bundle["swir_native_resolution_m"])
    pixel_area_m2 = grid_res_m * grid_res_m

    delta_mndwi = np.where(combined_valid, t1_idx["mndwi"] - t0_base["mndwi"], np.nan).astype(np.float32)

    # 1. Control hidrométrico global (crecida/bajante uniforme)
    hydro_state = compute_reach_hydrological_state(t0_base["mndwi"], t1_idx["mndwi"], combined_valid)

    # 2. Control de migración natural de bancos de arena (dipolos erosión/acreción)
    bar_migration_mask, dipole_pairs = detect_natural_bar_migration_dipoles(
        delta_mndwi=delta_mndwi,
        mndwi_t0=t0_base["mndwi"],
        ndvi_t0=t0_base["ndvi"],
        valid_mask=combined_valid,
        pixel_area_m2=pixel_area_m2,
        grid_res_m=grid_res_m,
        min_area_m2=eff_min_area,
    )

    # 3. Normalización fenológica de vegetación ribereña (ΔNDVI y ΔBSI ajustados)
    riparian_norm = normalize_seasonal_riparian_indices(
        ndvi_t0=t0_base["ndvi"],
        ndvi_t1=t1_idx["ndvi"],
        bsi_t0=t0_base["bsi"],
        bsi_t1=t1_idx["bsi"],
        valid_mask=combined_valid,
    )
    delta_bsi_adj = riparian_norm["delta_bsi_adj"]
    delta_ndvi_adj = riparian_norm["delta_ndvi_adj"]

    # 4. Máscaras candidatas residuales tras aplicar controles naturales y SCL
    flooded_excavation_mask = (
        combined_valid
        & np.isfinite(delta_mndwi)
        & (delta_mndwi > 0.40)
        & (t1_idx["mndwi"] > 0.20)
        & (~hydro_state["uniform_stage_belt_mask"])
        & (~bar_migration_mask)
    )

    riparian_clearing_mask = (
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

    # Dilatar máscara de nubes/sombras SCL en 40 m para registrar adyacencia de incertidumbre
    buf_px = max(1, int(round(40.0 / max(1.0, grid_res_m))))
    k_buf = np.ones((2 * buf_px + 1, 2 * buf_px + 1), dtype=np.uint8)
    cloud_shadow_dilated = cv2.dilate(combined_cloud_shadow.astype(np.uint8), k_buf) > 0

    crs_epsg = int(t1_bundle["crs_epsg"])
    transform = t1_bundle["transform"]
    to_wgs = Transformer.from_crs(CRS.from_epsg(crs_epsg), CRS.from_epsg(4326), always_xy=True)

    excavation_feats = _polygonize_mask_components(
        binary_mask=flooded_excavation_mask,
        anomaly_type="frente_excavacion_inundado",
        transform=transform,
        crs_epsg=crs_epsg,
        to_wgs=to_wgs,
        delta_mndwi=delta_mndwi,
        delta_bsi=delta_bsi_adj,
        delta_ndvi=delta_ndvi_adj,
        valid_mask=combined_valid,
        cloud_shadow_dilated=cloud_shadow_dilated,
        hydro_state=hydro_state,
        mndwi_t0=t0_base["mndwi"],
        b04_t0=t0_base["b04"],
        b04_t1=t1_bundle["B04"],
        effective_res_m=swir_native_res_m,
        min_area_m2=eff_min_area,
        persistence_count=int(t0_base["scenes_count"]),
        start_index=1,
    )

    clearing_feats = _polygonize_mask_components(
        binary_mask=riparian_clearing_mask,
        anomaly_type="desmonte_ribera_acopio",
        transform=transform,
        crs_epsg=crs_epsg,
        to_wgs=to_wgs,
        delta_mndwi=delta_mndwi,
        delta_bsi=delta_bsi_adj,
        delta_ndvi=delta_ndvi_adj,
        valid_mask=combined_valid,
        cloud_shadow_dilated=cloud_shadow_dilated,
        hydro_state=hydro_state,
        mndwi_t0=t0_base["mndwi"],
        b04_t0=t0_base["b04"],
        b04_t1=t1_bundle["B04"],
        effective_res_m=swir_native_res_m,
        min_area_m2=eff_min_area,
        persistence_count=int(t0_base["scenes_count"]),
        start_index=1 + len(excavation_feats),
    )

    all_feats = excavation_feats + clearing_feats
    geojson_features: List[Dict[str, Any]] = []
    for feat in all_feats:
        props = feat.model_dump(exclude={"geometry_geojson"})
        geojson_features.append(
            {
                "type": "Feature",
                "id": feat.id,
                "geometry": feat.geometry_geojson,
                "properties": props,
            }
        )

    metadata: Dict[str, Any] = {
        "crs_metric_epsg": crs_epsg,
        "grid_resolution_m": grid_res_m,
        "effective_swir_resolution_m": swir_native_res_m,
        "min_anomaly_area_m2": eff_min_area,
        "baseline_scenes_count": int(t0_base["scenes_count"]),
        "valid_pixel_fraction": round(float(np.mean(combined_valid)), 4) if combined_valid.size > 0 else 0.0,
        "hydrological_controls": {
            "water_stage_delta_pct": hydro_state["water_stage_delta_pct"],
            "is_systemic_stage_rise": hydro_state["is_systemic_stage_rise"],
            "is_systemic_stage_drop": hydro_state["is_systemic_stage_drop"],
            "bar_migration_dipoles_filtered": len(dipole_pairs),
            "bar_migration_details": dipole_pairs,
            "riparian_ndvi_seasonal_shift": riparian_norm["riparian_ndvi_seasonal_shift"],
            "riparian_bsi_seasonal_shift": riparian_norm["riparian_bsi_seasonal_shift"],
        },
        "candidate_count": len(geojson_features),
        "institutional_note": (
            "Las geometrías detectadas son anomalías morfológicas candidatas; "
            "no constituyen por sí solas infracción ni extracción ilegal sin cruce catastral oficial y revisión humana."
        ),
    }

    return ChangeDetectionResult(
        type="FeatureCollection",
        crs={"type": "name", "properties": {"name": "OGC:CRS84"}},
        metadata=metadata,
        features=geojson_features,
    )
