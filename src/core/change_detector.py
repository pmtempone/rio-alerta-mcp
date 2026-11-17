"""Motor multitemporal de índices espectrales y detección morfológica de extracción de áridos."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import Polygon, mapping

from src.config import settings
from src.core.crs_manager import reproject_geometry
from src.core.hydrological_control import (
    classify_aoi_geomorphology,
    compute_reach_hydrological_state,
    compute_turbidity_confounder_flag,
    detect_natural_bar_migration_dipoles,
    normalize_seasonal_riparian_indices,
)
from src.core.optical_providers import load_native_s2_scene_bundle
from src.schemas import (
    ChangeAnomalyTypeLiteral,
    ChangeDetectionResult,
    DiscardedAnomalyRecord,
    ExtractionAnomalyFeature,
    GeomorphologicalZoneLiteral,
    NaturalFilterStatusLiteral,
    PolygonSpectralStats,
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
    blue = b02.astype(np.float32)
    green = b03.astype(np.float32)
    red = b04.astype(np.float32)
    nir = b08.astype(np.float32)
    swir1 = b11.astype(np.float32)

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


def compute_regularized_bsi(
    b02: np.ndarray,
    b04: np.ndarray,
    b08: np.ndarray,
    b11: np.ndarray,
    albedo_k: Optional[float] = None,
    valid_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Calcula BSI regularizado por albedo (`k` DN) para evitar singularidad en aguas negras."""
    k = float(albedo_k if albedo_k is not None else settings.bsi_reg_albedo_k)
    blue = b02.astype(np.float32)
    red = b04.astype(np.float32)
    nir = b08.astype(np.float32)
    swir1 = b11.astype(np.float32)
    bsi_reg = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue) + max(1e-6, k))
    if valid_mask is not None:
        bsi_reg = np.where(valid_mask, bsi_reg, np.nan)
    return bsi_reg.astype(np.float32)


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
    bsi_reg_stack = []
    ndvi_stack = []
    b04_stack = []
    b11_stack = []
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
        bsi_reg = compute_regularized_bsi(
            b02=bundle["B02"],
            b04=bundle["B04"],
            b08=bundle["B08"],
            b11=bundle["B11"],
            valid_mask=bundle["valid_mask"],
        )
        mndwi_stack.append(idx["mndwi"])
        bsi_stack.append(idx["bsi"])
        bsi_reg_stack.append(bsi_reg)
        ndvi_stack.append(idx["ndvi"])
        b04_stack.append(bundle["B04"])
        b11_stack.append(bundle["B11"])
        valid_any |= bundle["valid_mask"]
        cloud_any |= bundle["cloud_or_shadow_mask"]

    if len(t0_bundles) == 1:
        return {
            "mndwi": mndwi_stack[0],
            "bsi": bsi_stack[0],
            "bsi_reg": bsi_reg_stack[0],
            "ndvi": ndvi_stack[0],
            "b04": b04_stack[0],
            "b11": b11_stack[0],
            "scl": ref.get("scl"),
            "valid_mask": valid_any,
            "cloud_or_shadow_mask": cloud_any,
            "scenes_count": 1,
        }

    return {
        "mndwi": np.nanmedian(np.stack(mndwi_stack, axis=0), axis=0).astype(np.float32),
        "bsi": np.nanmedian(np.stack(bsi_stack, axis=0), axis=0).astype(np.float32),
        "bsi_reg": np.nanmedian(np.stack(bsi_reg_stack, axis=0), axis=0).astype(np.float32),
        "ndvi": np.nanmedian(np.stack(ndvi_stack, axis=0), axis=0).astype(np.float32),
        "b04": np.nanmedian(np.stack(b04_stack, axis=0), axis=0).astype(np.float32),
        "b11": np.nanmedian(np.stack(b11_stack, axis=0), axis=0).astype(np.float32),
        "scl": ref.get("scl"),
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


def _build_candidate_uid(crs_epsg: int, cx_m: float, cy_m: float, anomaly_type: str) -> str:
    """Construye un identificador determinista estable entre corridas a partir de CRS, grilla 10m y tipo."""
    gx = int(round(cx_m / 10.0)) * 10
    gy = int(round(cy_m / 10.0)) * 10
    digest = hashlib.sha1(f"{crs_epsg}:{gx}:{gy}:{anomaly_type}".encode("utf-8")).hexdigest()[:8].upper()
    return f"RIO-{crs_epsg}-{gx}-{gy}-{digest}"


def _build_coarse_location_uid(crs_epsg: int, cx_m: float, cy_m: float) -> str:
    """Construye un identificador de celda de 100 m para seguimiento longitudinal entre épocas."""
    gx100 = int(round(cx_m / 100.0)) * 100
    gy100 = int(round(cy_m / 100.0)) * 100
    return f"RIO-CELL100-{crs_epsg}-{gx100}-{gy100}"


def _make_discarded_record(
    crs_epsg: int,
    cx_m: float,
    cy_m: float,
    to_wgs: Transformer,
    anomaly_type: ChangeAnomalyTypeLiteral,
    filter_status: NaturalFilterStatusLiteral,
    area_m2: float,
    reason_detail: str,
) -> DiscardedAnomalyRecord:
    """Construye un registro tipado de anomalía descartada para auditoría."""
    lon_d, lat_d = to_wgs.transform(cx_m, cy_m)
    return DiscardedAnomalyRecord(
        candidate_uid=_build_candidate_uid(crs_epsg, cx_m, cy_m, anomaly_type),
        anomaly_type=anomaly_type,
        filter_status=filter_status,
        area_m2=round(area_m2, 2),
        centroid_posgar=(round(cx_m, 2), round(cy_m, 2)),
        centroid_wgs84=(round(float(lon_d), 6), round(float(lat_d), 6)),
        reason_detail=reason_detail,
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
    t0_base: Optional[Dict[str, Any]] = None,
    t1_bundle: Optional[Dict[str, Any]] = None,
    t1_idx: Optional[Dict[str, np.ndarray]] = None,
    geo_zones: Optional[Dict[str, Any]] = None,
    discarded_out: Optional[List[DiscardedAnomalyRecord]] = None,
) -> List[ExtractionAnomalyFeature]:
    """Segmenta contornos OpenCV, mide geometría métrica POSGAR 2007 y construye incertidumbre."""
    # Clausura morfológica 3x3 para consolidar frentes y evitar fragmentación de bordes 20m
    kernel = np.ones((3, 3), dtype=np.uint8)
    cleaned = cv2.morphologyEx(binary_mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    features: List[ExtractionAnomalyFeature] = []
    idx = start_index

    h_img, w_img = binary_mask.shape
    half_px_m = 0.5 * abs(float(transform.a))
    pixel_area_m2 = abs(float(transform.a) * float(transform.e))

    for cnt in contours:
        pts_metric: List[Tuple[float, float]] = [
            (
                float(transform.c + (int(pt[0]) + 0.5) * transform.a),
                float(transform.f + (int(pt[1]) + 0.5) * transform.e),
            )
            for pt in cnt.reshape(-1, 2)
        ]
        if len(cnt) < 3:
            if discarded_out is not None and pts_metric:
                cx_d = float(np.mean([p[0] for p in pts_metric]))
                cy_d = float(np.mean([p[1] for p in pts_metric]))
                est_area = float(len(pts_metric) * pixel_area_m2)
                discarded_out.append(
                    _make_discarded_record(
                        crs_epsg=crs_epsg,
                        cx_m=cx_d,
                        cy_m=cy_d,
                        to_wgs=to_wgs,
                        anomaly_type=anomaly_type,
                        filter_status="DESCARTADO_GEOMETRIA_DEGENERADA",
                        area_m2=est_area,
                        reason_detail=(
                            f"Contorno degenerado con {len(cnt)} vértice(s) (< 3 requeridos para polígono cerrado)."
                        ),
                    )
                )
            continue

        raw_poly = Polygon(pts_metric)
        poly = raw_poly.buffer(half_px_m, cap_style=3, join_style=2)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            if discarded_out is not None:
                if not poly.is_empty:
                    c_pt = poly.centroid
                    cx_d, cy_d = float(c_pt.x), float(c_pt.y)
                else:
                    cx_d = float(np.mean([p[0] for p in pts_metric]))
                    cy_d = float(np.mean([p[1] for p in pts_metric]))
                deg_area = float(poly.area) if not poly.is_empty else 0.0
                discarded_out.append(
                    _make_discarded_record(
                        crs_epsg=crs_epsg,
                        cx_m=cx_d,
                        cy_m=cy_d,
                        to_wgs=to_wgs,
                        anomaly_type=anomaly_type,
                        filter_status="DESCARTADO_GEOMETRIA_DEGENERADA",
                        area_m2=deg_area,
                        reason_detail=f"Geometría degenerada tras poligonización/buffer ({poly.geom_type}).",
                    )
                )
            continue

        # P0.1: Usar exactamente el área vectorial POSGAR 2007 (poly.area) con soporte de medio píxel
        area_m2 = float(poly.area)
        if area_m2 < min_area_m2:
            if discarded_out is not None:
                c_pt = poly.centroid
                discarded_out.append(
                    _make_discarded_record(
                        crs_epsg=crs_epsg,
                        cx_m=float(c_pt.x),
                        cy_m=float(c_pt.y),
                        to_wgs=to_wgs,
                        anomaly_type=anomaly_type,
                        filter_status="DESCARTADO_AREA_MINIMA",
                        area_m2=area_m2,
                        reason_detail=(
                            f"Superficie vectorial ({area_m2:.1f} m²) inferior al piso físico mínimo "
                            f"({min_area_m2:.1f} m²) para {anomaly_type}."
                        ),
                    )
                )
            continue

        x_b, y_b, w_b, h_b = cv2.boundingRect(cnt)
        r0 = max(0, y_b - 1)
        r1 = min(h_img, y_b + h_b + 1)
        c0 = max(0, x_b - 1)
        c1 = min(w_img, x_b + w_b + 1)
        sub_transform = transform @ Affine.translation(c0, r0)
        sub_mask = rasterize(
            [(poly, 1)],
            out_shape=(r1 - r0, c1 - c0),
            transform=sub_transform,
            fill=0,
            dtype="uint8",
        ).astype(bool)
        poly_mask = np.zeros((h_img, w_img), dtype=bool)
        if np.any(sub_mask):
            poly_mask[r0:r1, c0:c1] = sub_mask
        else:
            comp_mask = np.zeros((h_img, w_img), dtype=np.uint8)
            cv2.drawContours(comp_mask, [cnt], -1, 1, thickness=-1)
            poly_mask = comp_mask.astype(bool)

        # Descartar si el candidato está dominado por pulso de turbidez dentro del cauce preexistente
        if anomaly_type == "frente_excavacion_inundado" and compute_turbidity_confounder_flag(
            mndwi_t0=mndwi_t0,
            b04_t0=b04_t0,
            b04_t1=b04_t1,
            candidate_mask=poly_mask,
        ):
            if discarded_out is not None:
                c_pt = poly.centroid
                discarded_out.append(
                    _make_discarded_record(
                        crs_epsg=crs_epsg,
                        cx_m=float(c_pt.x),
                        cy_m=float(c_pt.y),
                        to_wgs=to_wgs,
                        anomaly_type=anomaly_type,
                        filter_status="DESCARTADO_CONFUSOR_TURBIDEZ",
                        area_m2=area_m2,
                        reason_detail=(
                            "Descartado por confusor de turbidez o lámina/barra húmeda preexistente en T0."
                        ),
                    )
                )
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
        if anomaly_type == "desmonte_ribera_acopio" and elongation >= settings.road_min_elongation:
            eff_type = "apertura_camino_acceso"

        dm_vals = delta_mndwi[poly_mask & np.isfinite(delta_mndwi)]
        db_vals = delta_bsi[poly_mask & np.isfinite(delta_bsi)]
        dn_vals = delta_ndvi[poly_mask & np.isfinite(delta_ndvi)]
        dm_mean = float(np.mean(dm_vals)) if dm_vals.size > 0 else 0.0
        db_mean = float(np.mean(db_vals)) if db_vals.size > 0 else 0.0
        dn_mean = float(np.mean(dn_vals)) if dn_vals.size > 0 else 0.0

        valid_frac = float(np.mean(valid_mask[poly_mask])) if np.any(poly_mask) else 0.0
        cloud_adj = bool(np.any(cloud_shadow_dilated[poly_mask]))

        # Estadísticas auditables por polígono (medianas por fecha vs medias de delta)
        px_10m = int(np.sum(poly_mask))
        mv = poly_mask & valid_mask

        def _med(arr: Optional[np.ndarray]) -> Optional[float]:
            if arr is None or not np.any(mv):
                return None
            vals = arr[mv & np.isfinite(arr)]
            return round(float(np.median(vals)), 4) if vals.size > 0 else None

        scl_counts: Dict[str, int] = {}
        if t1_bundle is not None and t1_bundle.get("scl") is not None and np.any(poly_mask):
            s_vals, s_cnts = np.unique(t1_bundle["scl"][poly_mask].astype(int), return_counts=True)
            scl_counts = {str(int(k)): int(v) for k, v in zip(s_vals, s_cnts)}

        spectral_stats = PolygonSpectralStats(
            area_method="shapely_polygon_posgar_2007",
            pixel_count_10m=px_10m,
            effective_swir_pixels_20m=round(px_10m / 4.0, 2),
            t0_b11_median=_med(t0_base.get("b11") if t0_base else None),
            t1_b11_median=_med(t1_bundle.get("B11") if t1_bundle else None),
            t1_b04_median=_med(b04_t1),
            t0_ndvi_median=_med(t0_base.get("ndvi") if t0_base else None),
            t1_ndvi_median=_med(t1_idx.get("ndvi") if t1_idx else None),
            t0_bsi_median=_med(t0_base.get("bsi") if t0_base else None),
            t1_bsi_median=_med(t1_idx.get("bsi") if t1_idx else None),
            t1_mndwi_median=_med(t1_idx.get("mndwi") if t1_idx else None),
            t1_scl_counts=scl_counts,
        )

        # Unidad geomorfológica dominante y distancia al cauce T0
        assigned_zone: GeomorphologicalZoneLiteral = "NO_CLASIFICADA"
        dist_water_m: Optional[float] = None
        if geo_zones is not None and np.any(poly_mask):
            zone_counts: List[Tuple[int, GeomorphologicalZoneLiteral]] = [
                (int(np.sum(geo_zones["cauce_activo"] & poly_mask)), "CAUCE_ACTIVO_PERMANENTE"),
                (int(np.sum(geo_zones["barras_moviles"] & poly_mask)), "BARRAS_Y_BANCOS_MOVILES"),
                (int(np.sum(geo_zones["planicie_islas"] & poly_mask)), "PLANICIE_INUNDABLE_E_ISLAS"),
                (int(np.sum(geo_zones["ribera_firme"] & poly_mask)), "RIBERA_FIRME_Y_TERRAZA"),
            ]
            zone_counts.sort(key=lambda t: t[0], reverse=True)
            if zone_counts[0][0] > 0:
                assigned_zone = zone_counts[0][1]
            if geo_zones.get("dist_cauce_m") is not None:
                dist_water_m = round(float(np.min(geo_zones["dist_cauce_m"][poly_mask])), 1)

        limitations: List[str] = [
            f"Resolución física limitante {effective_res_m:.0f} m (bandas SWIR B11/B12 a 20 m nativos)."
        ]
        if cloud_adj:
            limitations.append(
                f"Contorno adyacente (buffer ≤ {settings.cloud_shadow_buffer_m:.0f} m) a píxeles de nube o "
                "sombra en máscara SCL combinada de T0 y T1."
            )
        if abs(float(hydro_state["water_stage_delta_pct"])) >= settings.water_stage_warning_pct:
            limitations.append(
                f"Variación hidrométrica regional detectada en el tramo ({hydro_state['water_stage_delta_pct']:+.1f}%)."
            )
        if persistence_count == 1:
            limitations.append("Evaluación basada en única escena histórica T0 (sin serie multitemporal extensa).")

        confounder_score = min(
            1.0,
            (0.35 if cloud_adj else 0.0)
            + (0.25 if abs(float(hydro_state["water_stage_delta_pct"])) >= settings.water_stage_warning_pct else 0.0)
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
                candidate_uid=_build_candidate_uid(crs_epsg, cx_m, cy_m, eff_type),
                coarse_location_uid=_build_coarse_location_uid(crs_epsg, cx_m, cy_m),
                anomaly_type=eff_type,
                confidence=confidence,
                signal_score=confidence,
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
                geomorphological_zone=assigned_zone,
                distance_to_water_t0_m=dist_water_m,
                spectral_stats=spectral_stats,
                uncertainty=uncertainty,
            )
        )
        idx += 1

    return features


def _collect_filtered_components(
    raw_mask: np.ndarray,
    final_mask: np.ndarray,
    anomaly_type: ChangeAnomalyTypeLiteral,
    transform: Any,
    crs_epsg: int,
    to_wgs: Transformer,
    pixel_area_m2: float,
    min_area_m2: float,
    bar_migration_mask: np.ndarray,
    uniform_stage_belt_mask: np.ndarray,
    drawdown_belt_mask: np.ndarray,
    flood_zone_excl: np.ndarray,
    scl_mineral_gate: np.ndarray,
    check_mineral_gate: bool = False,
    check_bar_migration: bool = True,
) -> List[DiscardedAnomalyRecord]:
    """Registra con trazabilidad los componentes >= `min_area_m2` suprimidos por controles naturales o SCL."""
    records: List[DiscardedAnomalyRecord] = []
    suppressed = raw_mask & (~final_mask)
    if not np.any(suppressed):
        return records

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        suppressed.astype(np.uint8), connectivity=8
    )
    for lbl in range(1, num_labels):
        area_px = int(stats[lbl, cv2.CC_STAT_AREA])
        area_m2 = float(area_px * pixel_area_m2)
        if area_m2 < min_area_m2:
            continue

        x = int(stats[lbl, cv2.CC_STAT_LEFT])
        y = int(stats[lbl, cv2.CC_STAT_TOP])
        w = int(stats[lbl, cv2.CC_STAT_WIDTH])
        h = int(stats[lbl, cv2.CC_STAT_HEIGHT])
        comp_mask = labels[y : y + h, x : x + w] == lbl

        cx_px = float(centroids[lbl, 0])
        cy_px = float(centroids[lbl, 1])
        cx_m = float(transform.c + (cx_px + 0.5) * transform.a)
        cy_m = float(transform.f + (cy_px + 0.5) * transform.e)

        filter_status: NaturalFilterStatusLiteral
        reason_detail: str
        if check_bar_migration and np.any(bar_migration_mask[y : y + h, x : x + w] & comp_mask):
            filter_status = "DESCARTADO_MIGRACION_BARRA"
            reason_detail = "Par dipolar de erosión/acreción natural de banco de arena."
        elif np.any(
            (
                uniform_stage_belt_mask[y : y + h, x : x + w]
                | drawdown_belt_mask[y : y + h, x : x + w]
                | flood_zone_excl[y : y + h, x : x + w]
            )
            & comp_mask
        ):
            filter_status = "DESCARTADO_PULSO_HIDROMETRICO"
            reason_detail = "Cinturón de crecida/bajante uniforme o inundación en cauce/planicie aluvial."
        elif check_mineral_gate and np.any((~scl_mineral_gate[y : y + h, x : x + w]) & comp_mask):
            filter_status = "DESCARTADO_NUBE_SOMBRA"
            reason_detail = (
                "Excluido por compuerta SCL en T1 (agua SCL=6 o borde mixto SCL=7 sin reflectancia mineral suficiente)."
            )
        else:
            filter_status = "DESCARTADO_COMPUERTA_MINERAL"
            reason_detail = (
                "Excluido por compuerta física de agua oscura / reflectancia mineral insuficiente "
                "(BSI_reg / B11 / B04 / B08)."
            )

        records.append(
            _make_discarded_record(
                crs_epsg=crs_epsg,
                cx_m=cx_m,
                cy_m=cy_m,
                to_wgs=to_wgs,
                anomaly_type=anomaly_type,
                filter_status=filter_status,
                area_m2=area_m2,
                reason_detail=reason_detail,
            )
        )
    return records


def detect_extraction_anomalies(
    t0_vnir_paths: Union[str, Path, Sequence[Union[str, Path]]],
    t1_vnir_path: Union[str, Path],
    t0_swir_scl_paths: Optional[Union[str, Path, Sequence[Union[str, Path]]]] = None,
    t1_swir_scl_path: Optional[Union[str, Path]] = None,
    min_area_m2: Optional[float] = None,
    min_clearing_area_m2: Optional[float] = None,
    work_dir: Optional[Union[str, Path]] = None,
    stage_t0_m: Optional[float] = None,
    stage_t1_m: Optional[float] = None,
    stage_t0_source: Optional[str] = None,
    stage_t1_source: Optional[str] = None,
    max_discarded_records: Optional[int] = None,
) -> ChangeDetectionResult:
    """Detecta anomalías morfológicas candidatas filtrando cambios fluviales naturales y nubes SCL.

    Aplica controles explícitos para:
    - Máscara de nubes/sombras/defectos `SCL` y exclusión de píxeles inválidos.
    - Pulso hidrométrico uniforme (crecida / bajante de nivel del río).
    - Dipolos acoplados de migración natural de bancos de arena.
    - Deriva fenológica/estacional uniforme de la vegetación ribereña.
    - Piso de área física diferenciado por resolución nativa (`>= 1600 m²` SWIR para excavación inundada y
      `>= 800 m²` VNIR para desmonte ribereño o caminos).

    Nunca etiqueta un polígono como ilegal de forma autónoma: las detecciones constituyen
    candidatos morfológicos con registro de incertidumbre para cruce catastral y revisión humana.
    """
    eff_min_area = float(min_area_m2 if min_area_m2 is not None else settings.min_anomaly_area_m2)
    eff_min_clearing_area = float(
        min_clearing_area_m2 if min_clearing_area_m2 is not None else settings.min_clearing_area_m2
    )
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

    # 1. Control hidrométrico global (crecida/bajante uniforme + altura hidrométrica opcional)
    hydro_state = compute_reach_hydrological_state(
        t0_base["mndwi"],
        t1_idx["mndwi"],
        combined_valid,
        stage_t0_m=stage_t0_m,
        stage_t1_m=stage_t1_m,
        stage_rise_alert_m=settings.hydrometric_stage_rise_alert_m,
    )

    # 1b. Clasificación geomorfológica del AOI en T0 (cauce, barras, planicie/islas, ribera firme)
    geo_zones = classify_aoi_geomorphology(
        mndwi_t0=t0_base["mndwi"],
        ndvi_t0=t0_base["ndvi"],
        valid_mask=combined_valid,
        scl_t0=t0_base.get("scl"),
        grid_res_m=grid_res_m,
    )

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

    # 3. Normalización fenológica de vegetación ribereña (ΔNDVI y ΔBSI ajustados) + BSI_reg (M7)
    riparian_norm = normalize_seasonal_riparian_indices(
        ndvi_t0=t0_base["ndvi"],
        ndvi_t1=t1_idx["ndvi"],
        bsi_t0=t0_base["bsi"],
        bsi_t1=t1_idx["bsi"],
        valid_mask=combined_valid,
    )
    delta_bsi_adj = riparian_norm["delta_bsi_adj"]
    delta_ndvi_adj = riparian_norm["delta_ndvi_adj"]

    bsi_reg_t1 = compute_regularized_bsi(
        b02=t1_bundle["B02"],
        b04=t1_bundle["B04"],
        b08=t1_bundle["B08"],
        b11=t1_bundle["B11"],
        valid_mask=t1_bundle["valid_mask"],
    )
    delta_bsi_reg = np.where(combined_valid, bsi_reg_t1 - t0_base["bsi_reg"], np.nan).astype(np.float32)

    # 4. Máscaras candidatas residuales tras aplicar controles naturales, geomorfología y M7
    stage_delta_val = hydro_state.get("stage_delta_m")
    moderate_stage_rise = stage_delta_val is not None and float(stage_delta_val) >= settings.moderate_stage_rise_m
    in_unprotected_flood_zone = geo_zones["cauce_activo"].copy()
    if hydro_state["is_systemic_stage_rise"]:
        in_unprotected_flood_zone = (
            in_unprotected_flood_zone | geo_zones["barras_moviles"] | geo_zones["planicie_islas"]
        )
    elif moderate_stage_rise:
        in_unprotected_flood_zone = in_unprotected_flood_zone | geo_zones["barras_moviles"]

    raw_flooded = (
        combined_valid
        & np.isfinite(delta_mndwi)
        & (delta_mndwi > settings.flooded_min_delta_mndwi)
        & (t1_idx["mndwi"] > settings.flooded_min_t1_mndwi)
    )
    flooded_excavation_mask = (
        raw_flooded
        & (~hydro_state["uniform_stage_belt_mask"])
        & (~bar_migration_mask)
        & (~in_unprotected_flood_zone)
    )

    scl_t1 = t1_bundle.get("scl")
    if scl_t1 is not None:
        scl_mineral_gate = (scl_t1 != 6) & (
            (scl_t1 != 7)
            | (
                (t1_bundle["B11"] >= settings.scl7_min_b11_dn)
                & (t1_bundle["B04"] >= settings.scl7_min_b04_dn)
            )
        )
    else:
        scl_mineral_gate = np.ones_like(combined_valid, dtype=bool)

    dark_water_mineral_gate = (
        (t1_bundle["B11"] >= settings.dark_water_min_b11_dn)
        & (t1_bundle["B04"] >= settings.dark_water_min_b04_dn)
        & (t1_bundle["B08"] >= settings.dark_water_min_b08_dn)
        & (t1_bundle["B11"] > settings.dark_water_min_b11_b03_ratio * t1_bundle["B03"])
        & scl_mineral_gate
    )

    clearing_flood_excl = geo_zones["cauce_activo"].copy()
    if hydro_state["is_systemic_stage_rise"]:
        clearing_flood_excl = clearing_flood_excl | geo_zones["barras_moviles"]

    raw_clearing = (
        combined_valid
        & np.isfinite(delta_bsi_adj)
        & np.isfinite(delta_ndvi_adj)
        & (delta_bsi_adj > settings.clearing_min_delta_bsi_adj)
        & (delta_ndvi_adj < settings.clearing_max_delta_ndvi_adj)
        & (t1_idx["bsi"] > settings.clearing_min_t1_bsi)
        & (t1_idx["mndwi"] < settings.clearing_max_t1_mndwi)
    )
    riparian_clearing_mask = (
        raw_clearing
        & np.isfinite(delta_bsi_reg)
        & (delta_bsi_reg > settings.clearing_min_delta_bsi_reg)
        & (bsi_reg_t1 > settings.clearing_min_t1_bsi_reg)
        & dark_water_mineral_gate
        & (~hydro_state["uniform_stage_belt_mask"])
        & (~hydro_state["drawdown_belt_mask"])
        & (~clearing_flood_excl)
    )

    # Dilatar máscara de nubes/sombras SCL según buffer métrico para registrar adyacencia de incertidumbre
    buf_px = max(1, int(round(settings.cloud_shadow_buffer_m / max(1.0, grid_res_m))))
    k_buf = np.ones((2 * buf_px + 1, 2 * buf_px + 1), dtype=np.uint8)
    cloud_shadow_dilated = cv2.dilate(combined_cloud_shadow.astype(np.uint8), k_buf) > 0

    crs_epsg = int(t1_bundle["crs_epsg"])
    transform = t1_bundle["transform"]
    to_wgs = Transformer.from_crs(CRS.from_epsg(crs_epsg), CRS.from_epsg(4326), always_xy=True)

    discarded_records: List[DiscardedAnomalyRecord] = []

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
        t0_base=t0_base,
        t1_bundle=t1_bundle,
        t1_idx=t1_idx,
        geo_zones=geo_zones,
        start_index=1,
        discarded_out=discarded_records,
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
        min_area_m2=eff_min_clearing_area,
        persistence_count=int(t0_base["scenes_count"]),
        t0_base=t0_base,
        t1_bundle=t1_bundle,
        t1_idx=t1_idx,
        geo_zones=geo_zones,
        start_index=1 + len(excavation_feats),
        discarded_out=discarded_records,
    )

    discarded_records.extend(
        _collect_filtered_components(
            raw_mask=raw_flooded,
            final_mask=flooded_excavation_mask,
            anomaly_type="frente_excavacion_inundado",
            transform=transform,
            crs_epsg=crs_epsg,
            to_wgs=to_wgs,
            pixel_area_m2=pixel_area_m2,
            min_area_m2=eff_min_area,
            bar_migration_mask=bar_migration_mask,
            uniform_stage_belt_mask=hydro_state["uniform_stage_belt_mask"],
            drawdown_belt_mask=hydro_state["drawdown_belt_mask"],
            flood_zone_excl=in_unprotected_flood_zone,
            scl_mineral_gate=scl_mineral_gate,
            check_mineral_gate=False,
            check_bar_migration=True,
        )
    )
    discarded_records.extend(
        _collect_filtered_components(
            raw_mask=raw_clearing,
            final_mask=riparian_clearing_mask,
            anomaly_type="desmonte_ribera_acopio",
            transform=transform,
            crs_epsg=crs_epsg,
            to_wgs=to_wgs,
            pixel_area_m2=pixel_area_m2,
            min_area_m2=eff_min_clearing_area,
            bar_migration_mask=bar_migration_mask,
            uniform_stage_belt_mask=hydro_state["uniform_stage_belt_mask"],
            drawdown_belt_mask=hydro_state["drawdown_belt_mask"],
            flood_zone_excl=clearing_flood_excl,
            scl_mineral_gate=scl_mineral_gate,
            check_mineral_gate=True,
            check_bar_migration=False,
        )
    )
    discarded_records.sort(key=lambda r: r.area_m2, reverse=True)
    t0_stem = Path(t0_list[-1]).stem
    t1_stem = Path(t1_vnir_path).stem
    run_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{t0_stem}_vs_{t1_stem}")[:120]
    discarded_log_path = target_dir / f"discarded_candidates_{run_stem}.json"
    discarded_payload = [d.model_dump(mode="json") for d in discarded_records]
    discarded_log_path.write_text(
        json.dumps(discarded_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    eff_max_disc = max(
        1,
        int(max_discarded_records if max_discarded_records is not None else settings.max_discarded_records),
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

    valid_count = max(1, int(np.sum(combined_valid)))
    geomorphological_zone_fractions = {
        z_name: round(float(np.sum(geo_zones[z_name] & combined_valid) / valid_count), 4)
        for z_name in ("cauce_activo", "barras_moviles", "planicie_islas", "ribera_firme")
    }

    metadata: Dict[str, Any] = {
        "crs_metric_epsg": crs_epsg,
        "grid_resolution_m": grid_res_m,
        "effective_swir_resolution_m": swir_native_res_m,
        "min_anomaly_area_m2": eff_min_area,
        "min_clearing_area_m2": eff_min_clearing_area,
        "baseline_scenes_count": int(t0_base["scenes_count"]),
        "valid_pixel_fraction": round(float(np.mean(combined_valid)), 4) if combined_valid.size > 0 else 0.0,
        "hydrological_controls": {
            "water_stage_delta_pct": hydro_state["water_stage_delta_pct"],
            "is_systemic_stage_rise": hydro_state["is_systemic_stage_rise"],
            "is_systemic_stage_drop": hydro_state["is_systemic_stage_drop"],
            "stage_t0_m": hydro_state.get("stage_t0_m"),
            "stage_t0_source": stage_t0_source or ("PARAMETRO_MANUAL" if stage_t0_m is not None else "NO_DISPONIBLE"),
            "stage_t1_m": hydro_state.get("stage_t1_m"),
            "stage_t1_source": stage_t1_source or ("PARAMETRO_MANUAL" if stage_t1_m is not None else "NO_DISPONIBLE"),
            "stage_delta_m": hydro_state.get("stage_delta_m"),
            "bar_migration_dipoles_filtered": len(dipole_pairs),
            "bar_migration_details": dipole_pairs,
            "riparian_ndvi_seasonal_shift": riparian_norm["riparian_ndvi_seasonal_shift"],
            "riparian_bsi_seasonal_shift": riparian_norm["riparian_bsi_seasonal_shift"],
        },
        "geomorphological_zone_fractions": geomorphological_zone_fractions,
        "calibration_parameters": settings.get_calibration_parameters(),
        "candidate_count": len(geojson_features),
        "discarded_count": len(discarded_records),
        "discarded_truncated": len(discarded_records) > eff_max_disc,
        "discarded_log_path": str(discarded_log_path),
        "discarded_candidates": discarded_payload[:eff_max_disc],
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
