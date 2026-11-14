"""Proveedor óptico multitemporal Sentinel-2 L2A (STAC + resoluciones nativas 10m/20m + máscara SCL)."""

import io
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import httpx
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import from_origin
from rasterio.windows import from_bounds

from src.config import settings
from src.core.crs_manager import (
    bbox_wgs84_to_posgar,
    ensure_metric_crs,
    reproject_array_to_grid,
)
from src.schemas import MultitemporalScenePair, SceneMetadata

logger = logging.getLogger(__name__)

S2_VNIR_10M_BANDS: Tuple[str, ...] = ("B02", "B03", "B04", "B08")
S2_SWIR_20M_BANDS: Tuple[str, ...] = ("B11", "B12")
S2_ALL_SPECTRAL_BANDS: Tuple[str, ...] = ("B02", "B03", "B04", "B08", "B11", "B12")

# Clases Sentinel-2 Scene Classification Layer (SCL):
# 0=NoData, 1=Saturado/Defectuoso, 2=Sombras oscuras/topográficas, 3=Sombra de nube,
# 4=Vegetación, 5=No vegetado/Suelo desnudo, 6=Agua, 7=No clasificado,
# 8=Nube prob. media, 9=Nube prob. alta, 10=Cirros finos, 11=Nieve/Hielo
S2_INVALID_SCL_CLASSES: Tuple[int, ...] = (0, 1, 2, 3, 8, 9, 10, 11)
S2_CLOUD_SHADOW_SCL_CLASSES: Tuple[int, ...] = (2, 3, 8, 9, 10)

_STAC_BAND_ASSET_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "B02": ("blue", "B02"),
    "B03": ("green", "B03"),
    "B04": ("red", "B04"),
    "B08": ("nir", "B08"),
    "B11": ("swir16", "B11"),
    "B12": ("swir22", "B12"),
    "SCL": ("scl", "SCL"),
}


def decode_scl_masks(scl_array: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Decodifica la banda SCL de Sentinel-2 en máscara válida y máscara de nubes/sombras.

    Args:
        scl_array: Arreglo 2D con códigos enteros SCL (0..11).

    Returns:
        Tupla `(valid_mask, cloud_or_shadow_mask)` de arreglos booleanos 2D.
    """
    finite = np.isfinite(scl_array)
    scl_int = np.rint(np.where(finite, scl_array, 0.0)).astype(np.int32)
    invalid = (~finite) | np.isin(scl_int, S2_INVALID_SCL_CLASSES)
    cloud_or_shadow = np.isin(scl_int, S2_CLOUD_SHADOW_SCL_CLASSES)
    return (~invalid), cloud_or_shadow


def _resolve_band_indices(
    descriptions: Optional[Sequence[Optional[str]]],
    count: int,
    expected: Sequence[str],
) -> Dict[str, int]:
    """Resuelve índices 1-based de bandas por descripción o posición estándar."""
    mapping: Dict[str, int] = {}
    if descriptions:
        norm_desc = [str(d).strip().upper() if d else "" for d in descriptions]
        for band in expected:
            if band.upper() in norm_desc:
                mapping[band] = norm_desc.index(band.upper()) + 1

    if len(mapping) >= min(len(expected), count):
        return mapping

    for idx, band in enumerate(expected, start=1):
        if idx <= count and band not in mapping:
            mapping[band] = idx
    return mapping


def _clean_reflectance_band(
    arr: np.ndarray,
    nodata_val: Optional[float],
    valid_mask: np.ndarray,
) -> np.ndarray:
    """Convierte NoData a NaN y aplica máscara booleana de validez física."""
    out = arr.astype(np.float32, copy=True)
    invalid = ~np.isfinite(out) | (out < 0.0) | (~valid_mask)
    if nodata_val is not None and np.isfinite(nodata_val):
        invalid |= np.isclose(out, float(nodata_val), rtol=1e-5, atol=1e-5)
    out[invalid] = np.nan
    return out


def load_native_s2_scene_bundle(
    vnir_10m_path: Union[str, Path],
    swir_scl_20m_path: Optional[Union[str, Path]] = None,
    work_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Carga una escena Sentinel-2 L2A respetando resoluciones nativas (10 m VNIR y 20 m SWIR/SCL).

    Soporta dos disposiciones:
    1. Par nativo separado: `vnir_10m_path` (`B02, B03, B04, B08` a 10 m) y `swir_scl_20m_path`
       (`B11, B12, SCL` a 20 m). Co-registra `B11`/`B12` (`bilinear`) y `SCL` (`nearest`) a la
       grilla métrica POSGAR 2007 de 10 m preservando `swir_native_resolution_m` real.
    2. Stack único multibanda (`B02, B03, B04, B08, B11, B12[, SCL]`).

    Returns:
        Diccionario con bandas en `float32` (`np.nan` fuera de píxeles válidos), `valid_mask`,
        `cloud_or_shadow_mask`, metadatos espaciales POSGAR 2007 y resoluciones efectivas.
    """
    vnir_src_path = Path(vnir_10m_path)
    tmp_base = work_dir or vnir_src_path.parent
    metric_vnir_path = Path(
        ensure_metric_crs(
            vnir_src_path,
            tmp_base / f"{vnir_src_path.stem}_posgar.tif",
            resampling_method="bilinear",
        )
    )

    with rasterio.open(metric_vnir_path) as src:
        if src.crs is None:
            raise ValueError(f"El raster VNIR '{metric_vnir_path}' no tiene CRS definido.")
        ref_crs = src.crs
        crs_epsg = int(ref_crs.to_epsg() or 0)
        ref_transform = src.transform
        ref_shape = (src.height, src.width)
        grid_res_m = float(abs(ref_transform.a))
        bounds_posgar = (
            float(src.bounds.left),
            float(src.bounds.bottom),
            float(src.bounds.right),
            float(src.bounds.top),
        )
        vnir_nodata = src.nodata

        if swir_scl_20m_path is not None:
            vnir_map = _resolve_band_indices(src.descriptions, src.count, S2_VNIR_10M_BANDS)
            raw_bands: Dict[str, np.ndarray] = {}
            for band in S2_VNIR_10M_BANDS:
                raw_bands[band] = src.read(vnir_map[band]).astype(np.float32)

            swir_path = Path(swir_scl_20m_path)
            with rasterio.open(swir_path) as swir_src:
                if swir_src.crs is None:
                    raise ValueError(f"El raster SWIR/SCL '{swir_path}' no tiene CRS definido.")
                swir_native_res_m = float(abs(swir_src.transform.a))
                swir_map = _resolve_band_indices(
                    swir_src.descriptions,
                    swir_src.count,
                    ("B11", "B12", "SCL"),
                )
                for swir_band in S2_SWIR_20M_BANDS:
                    arr_20 = swir_src.read(swir_map[swir_band]).astype(np.float32)
                    raw_bands[swir_band] = reproject_array_to_grid(
                        src_array=arr_20,
                        src_transform=swir_src.transform,
                        src_crs=swir_src.crs,
                        dst_transform=ref_transform,
                        dst_crs=ref_crs,
                        dst_shape=ref_shape,
                        resampling_method="bilinear",
                        src_nodata=swir_src.nodata,
                        dst_nodata=np.nan,
                    )

                if "SCL" in swir_map:
                    scl_20 = swir_src.read(swir_map["SCL"])
                    scl_aligned = reproject_array_to_grid(
                        src_array=scl_20,
                        src_transform=swir_src.transform,
                        src_crs=swir_src.crs,
                        dst_transform=ref_transform,
                        dst_crs=ref_crs,
                        dst_shape=ref_shape,
                        resampling_method="nearest",
                        src_nodata=None,
                        dst_nodata=0,
                    )
                else:
                    scl_aligned = None
        else:
            expected = ("B02", "B03", "B04", "B08", "B11", "B12", "SCL")
            band_map = _resolve_band_indices(src.descriptions, src.count, expected)
            raw_bands = {}
            for band in S2_ALL_SPECTRAL_BANDS:
                if band not in band_map:
                    raise ValueError(f"Falta la banda requerida '{band}' en '{metric_vnir_path}'.")
                raw_bands[band] = src.read(band_map[band]).astype(np.float32)

            if "SCL" in band_map:
                scl_aligned = src.read(band_map["SCL"])
            else:
                scl_aligned = None
            swir_native_res_m = float(max(grid_res_m, 20.0))

    if scl_aligned is not None:
        valid_mask, cloud_or_shadow_mask = decode_scl_masks(scl_aligned)
    else:
        valid_mask = np.ones(ref_shape, dtype=bool)
        for band in S2_ALL_SPECTRAL_BANDS:
            arr = raw_bands[band]
            valid_mask &= np.isfinite(arr) & (arr >= 0.0)
            if vnir_nodata is not None and np.isfinite(vnir_nodata):
                valid_mask &= ~np.isclose(arr, float(vnir_nodata), rtol=1e-5, atol=1e-5)
        cloud_or_shadow_mask = np.zeros(ref_shape, dtype=bool)

    for band in S2_ALL_SPECTRAL_BANDS:
        raw_bands[band] = _clean_reflectance_band(raw_bands[band], vnir_nodata, valid_mask)

    # Asegurar consistencia entre NaN de bandas SWIR remuestreadas y valid_mask
    swir_finite = np.isfinite(raw_bands["B11"]) & np.isfinite(raw_bands["B12"])
    valid_mask = valid_mask & swir_finite
    for band in S2_ALL_SPECTRAL_BANDS:
        raw_bands[band][~valid_mask] = np.nan

    return {
        **raw_bands,
        "scl": scl_aligned,
        "valid_mask": valid_mask,
        "cloud_or_shadow_mask": cloud_or_shadow_mask,
        "crs_epsg": crs_epsg,
        "transform": ref_transform,
        "bounds_posgar": bounds_posgar,
        "shape_hw": ref_shape,
        "grid_resolution_m": grid_res_m,
        "swir_native_resolution_m": swir_native_res_m,
        "valid_pixel_fraction": float(np.mean(valid_mask)) if valid_mask.size > 0 else 0.0,
    }


def _extract_item_datetime(item: Any) -> Optional[datetime]:
    """Extrae fecha UTC de un pystac.Item o diccionario compatible."""
    dt_val = getattr(item, "datetime", None)
    if isinstance(dt_val, datetime):
        return dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)

    props = getattr(item, "properties", None)
    if props is None and isinstance(item, dict):
        props = item.get("properties", item)
    if isinstance(props, dict):
        raw = props.get("datetime") or props.get("acquisition_date")
        if isinstance(raw, str) and raw:
            norm = raw.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(norm)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _extract_item_cloud_cover(item: Any) -> float:
    """Extrae porcentaje de cobertura nubosa `eo:cloud_cover` de un item STAC o dict."""
    props = getattr(item, "properties", None)
    if props is None and isinstance(item, dict):
        props = item.get("properties", item)
    if isinstance(props, dict):
        val = props.get("eo:cloud_cover", props.get("cloud_cover_pct", 100.0))
        if val is None:
            return 100.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 100.0
    return 100.0


def filter_seasonally_comparable_items(
    items: Sequence[Any],
    reference_date_iso: str,
    window_days: Optional[int] = None,
    max_cloud_cover_pct: Optional[float] = None,
    historical_only: bool = True,
    min_days_before: int = 60,
) -> List[Any]:
    """Filtra escenas históricas T0 dentro de una ventana estacional comparable respecto a T1.

    Compara el día del año (DOY circular en 365 días) para evitar confrontar crecientes estacionales
    de verano contra bajantes de invierno. Ordena las escenas resultantes por cercanía fenológica
    y menor cobertura nubosa.
    """
    eff_window = int(window_days if window_days is not None else settings.seasonal_window_days)
    eff_cloud = float(max_cloud_cover_pct if max_cloud_cover_pct is not None else settings.max_cloud_cover_pct)

    ref_norm = reference_date_iso.replace("Z", "+00:00")
    ref_dt = datetime.fromisoformat(ref_norm)
    if ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
    ref_doy = ref_dt.timetuple().tm_yday

    scored: List[Tuple[int, float, Any]] = []
    for item in items:
        it_dt = _extract_item_datetime(item)
        if it_dt is None:
            continue
        if historical_only and (ref_dt - it_dt).days < min_days_before:
            continue
        cloud_pct = _extract_item_cloud_cover(item)
        if cloud_pct > eff_cloud:
            continue
        it_doy = it_dt.timetuple().tm_yday
        diff = abs(ref_doy - it_doy)
        circular_diff = min(diff, 365 - diff)
        if circular_diff <= eff_window:
            scored.append((circular_diff, cloud_pct, item))

    scored.sort(key=lambda tup: (tup[0], tup[1]))
    return [entry[2] for entry in scored]


def _read_stac_asset_window_posgar(
    href: str,
    bounds_posgar: Tuple[float, float, float, float],
    dst_crs_epsg: int,
    dst_res_m: float,
    resampling_method: str,
) -> Tuple[np.ndarray, Any, Tuple[int, int]]:
    """Lee por HTTP Range Request únicamente el recorte necesario y lo reproyecta a POSGAR 2007."""
    minx, miny, maxx, maxy = bounds_posgar
    width = max(1, min(settings.max_master_side_px, int(round((maxx - minx) / dst_res_m))))
    height = max(1, min(settings.max_master_side_px, int(round((maxy - miny) / dst_res_m))))
    dst_transform = from_origin(minx, maxy, (maxx - minx) / width, (maxy - miny) / height)
    dst_crs = rasterio.crs.CRS.from_epsg(dst_crs_epsg)

    with rasterio.open(href) as src:
        to_src = Transformer.from_crs(CRS.from_epsg(dst_crs_epsg), src.crs, always_xy=True)
        xs, ys = [], []
        for cx, cy in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)):
            sx, sy = to_src.transform(cx, cy)
            xs.append(sx)
            ys.append(sy)
        # Margen de 2 píxeles fuente para evitar artefactos de borde en el remuestreo
        pad_x = abs(src.transform.a) * 2.0
        pad_y = abs(src.transform.e) * 2.0
        win = from_bounds(
            min(xs) - pad_x,
            min(ys) - pad_y,
            max(xs) + pad_x,
            max(ys) + pad_y,
            transform=src.transform,
        )
        win = win.round_offsets().round_lengths()
        arr = src.read(1, window=win, boundless=True, fill_value=src.nodata or 0)
        win_transform = src.window_transform(win)

        resamp: Literal["bilinear", "nearest"] = "nearest" if resampling_method == "nearest" else "bilinear"
        out = reproject_array_to_grid(
            src_array=arr,
            src_transform=win_transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_shape=(height, width),
            resampling_method=resamp,
            src_nodata=src.nodata,
            dst_nodata=0 if resamp == "nearest" else -9999.0,
        )

    return out, dst_transform, (height, width)


def _resolve_asset_href(item: Any, band_name: str, sign_planetary: bool = False) -> str:
    """Obtiene la URL firmada o directa de una banda Sentinel-2 desde un ítem STAC."""
    if sign_planetary:
        import planetary_computer

        item = planetary_computer.sign(item)
    assets = item.assets
    for key in _STAC_BAND_ASSET_CANDIDATES.get(band_name, (band_name,)):
        if key in assets:
            return str(assets[key].href)
    raise KeyError(f"El ítem STAC '{item.id}' no posee asset para la banda '{band_name}'.")


def _export_stac_item_to_posgar_geotiffs(
    item: Any,
    bounds_posgar: Tuple[float, float, float, float],
    crs_epsg: int,
    out_dir: Path,
    prefix: str,
    sign_planetary: bool = False,
) -> Tuple[Path, Path, SceneMetadata]:
    """Descarga ventanas COG (VNIR 10 m y SWIR+SCL 20 m) en POSGAR 2007."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vnir_arrays: List[np.ndarray] = []
    vnir_transform = None
    vnir_shape: Tuple[int, int] = (0, 0)
    for band in S2_VNIR_10M_BANDS:
        href = _resolve_asset_href(item, band, sign_planetary=sign_planetary)
        arr, tr, shp = _read_stac_asset_window_posgar(href, bounds_posgar, crs_epsg, 10.0, "bilinear")
        vnir_arrays.append(arr.astype(np.float32))
        vnir_transform = tr
        vnir_shape = shp

    vnir_path = out_dir / f"{prefix}_{item.id}_vnir10m.tif"
    with rasterio.open(
        vnir_path,
        "w",
        driver="GTiff",
        height=vnir_shape[0],
        width=vnir_shape[1],
        count=len(S2_VNIR_10M_BANDS),
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(crs_epsg),
        transform=vnir_transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(np.stack(vnir_arrays, axis=0))
        dst.descriptions = S2_VNIR_10M_BANDS

    swir_arrays: List[np.ndarray] = []
    swir_transform = None
    swir_shape: Tuple[int, int] = (0, 0)
    for band in S2_SWIR_20M_BANDS:
        href = _resolve_asset_href(item, band, sign_planetary=sign_planetary)
        arr, tr, shp = _read_stac_asset_window_posgar(href, bounds_posgar, crs_epsg, 20.0, "bilinear")
        swir_arrays.append(arr.astype(np.float32))
        swir_transform = tr
        swir_shape = shp

    scl_href = _resolve_asset_href(item, "SCL", sign_planetary=sign_planetary)
    scl_arr, _, _ = _read_stac_asset_window_posgar(scl_href, bounds_posgar, crs_epsg, 20.0, "nearest")
    swir_arrays.append(scl_arr.astype(np.float32))

    swir_path = out_dir / f"{prefix}_{item.id}_swir_scl20m.tif"
    with rasterio.open(
        swir_path,
        "w",
        driver="GTiff",
        height=swir_shape[0],
        width=swir_shape[1],
        count=3,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(crs_epsg),
        transform=swir_transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(np.stack(swir_arrays, axis=0))
        dst.descriptions = ("B11", "B12", "SCL")

    it_dt = _extract_item_datetime(item)
    acq_date = it_dt.strftime("%Y-%m-%d") if it_dt else "unknown"
    meta = SceneMetadata(
        scene_id=str(item.id),
        provider=settings.optical_provider,
        acquisition_date=acq_date,
        cloud_cover_pct=round(_extract_item_cloud_cover(item), 2),
        crs_epsg=crs_epsg,
        resolution_m=10.0,
        bands_available=["B02", "B03", "B04", "B08", "B11", "B12", "SCL"],
    )
    return vnir_path, swir_path, meta


def _item_bbox_coverage_fraction(
    item: Any,
    bbox_wgs84: Tuple[float, float, float, float],
) -> float:
    """Calcula la fracción del AOI (`bbox_wgs84`) cubierta por el `bbox` del ítem STAC."""
    it_bbox = getattr(item, "bbox", None)
    if it_bbox is None and isinstance(item, dict):
        it_bbox = item.get("bbox")
    if not it_bbox or len(it_bbox) < 4:
        return 1.0
    ix0, iy0, ix1, iy1 = float(it_bbox[0]), float(it_bbox[1]), float(it_bbox[2]), float(it_bbox[3])
    ax0, ay0, ax1, ay1 = bbox_wgs84
    req_area = max(1e-12, (ax1 - ax0) * (ay1 - ay0))
    inter_w = max(0.0, min(ix1, ax1) - max(ix0, ax0))
    inter_h = max(0.0, min(iy1, ay1) - max(iy0, ay0))
    return float((inter_w * inter_h) / req_area)


def _item_covers_bbox(
    item: Any,
    bbox_wgs84: Tuple[float, float, float, float],
    min_coverage: float = 0.98,
) -> bool:
    """Indica si el ítem STAC cubre prácticamente todo el AOI solicitado (evita bordes MGRS)."""
    return _item_bbox_coverage_fraction(item, bbox_wgs84) >= min_coverage


def query_stac_seasonal_pair_candidates(
    client: Any,
    bbox_wgs84: Tuple[float, float, float, float],
    end_dt: Optional[datetime] = None,
    lookback_years: int = 3,
    window_days: Optional[int] = None,
    max_cloud_cover_pct: Optional[float] = None,
) -> Tuple[Optional[Any], List[Any], int]:
    """Consulta STAC por ventanas anuales equivalentes para evitar truncamiento por paginación.

    Primero ubica la escena limpia más reciente (`T1`) priorizando cobertura completa del AOI
    (evitando recortes truncados de borde MGRS) y luego consulta explícitamente cada ventana
    estacional histórica (`±window_days` en años anteriores) para construir la línea base `T0`.
    """
    eff_end = end_dt or datetime.now(timezone.utc)
    if eff_end.tzinfo is None:
        eff_end = eff_end.replace(tzinfo=timezone.utc)
    eff_window = int(window_days if window_days is not None else settings.seasonal_window_days)
    eff_cloud = float(max_cloud_cover_pct if max_cloud_cover_pct is not None else settings.max_cloud_cover_pct)

    recent_start = (eff_end - timedelta(days=180)).strftime("%Y-%m-%d")
    recent_end = eff_end.strftime("%Y-%m-%d")
    search_t1 = client.search(
        collections=["sentinel-2-l2a"],
        bbox=list(bbox_wgs84),
        datetime=f"{recent_start}/{recent_end}",
        query={"eo:cloud_cover": {"lt": eff_cloud}},
        max_items=20,
    )
    recent_items = list(search_t1.items())
    if not recent_items:
        fallback_start = (eff_end - timedelta(days=365)).strftime("%Y-%m-%d")
        recent_items = list(
            client.search(
                collections=["sentinel-2-l2a"],
                bbox=list(bbox_wgs84),
                datetime=f"{fallback_start}/{recent_end}",
                query={"eo:cloud_cover": {"lt": eff_cloud}},
                max_items=20,
            ).items()
        )
    if not recent_items:
        return None, [], 0

    recent_sorted = sorted(
        recent_items,
        key=lambda it: (
            1 if _item_covers_bbox(it, bbox_wgs84) else 0,
            _item_bbox_coverage_fraction(it, bbox_wgs84),
            (_extract_item_datetime(it) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            -_extract_item_cloud_cover(it),
        ),
        reverse=True,
    )
    t1_item = recent_sorted[0]
    t1_dt = _extract_item_datetime(t1_item)
    if t1_dt is None:
        return None, [], len(recent_items)

    seen_ids = {str(getattr(t1_item, "id", ""))}
    hist_pool: List[Any] = []
    for yr in range(1, max(1, lookback_years) + 1):
        center = t1_dt - timedelta(days=365 * yr)
        win_start = (center - timedelta(days=eff_window)).strftime("%Y-%m-%d")
        win_end = (center + timedelta(days=eff_window)).strftime("%Y-%m-%d")
        yr_items = list(
            client.search(
                collections=["sentinel-2-l2a"],
                bbox=list(bbox_wgs84),
                datetime=f"{win_start}/{win_end}",
                query={"eo:cloud_cover": {"lt": eff_cloud}},
                max_items=15,
            ).items()
        )
        for it in yr_items:
            it_id = str(getattr(it, "id", ""))
            if it_id not in seen_ids:
                seen_ids.add(it_id)
                hist_pool.append(it)

    full_cov_hist = [it for it in hist_pool if _item_covers_bbox(it, bbox_wgs84)]
    effective_hist = full_cov_hist if full_cov_hist else hist_pool

    comparable_t0 = filter_seasonally_comparable_items(
        items=effective_hist,
        reference_date_iso=t1_dt.isoformat(),
        window_days=eff_window,
        max_cloud_cover_pct=eff_cloud,
        historical_only=True,
    )
    return t1_item, comparable_t0, len(recent_items)


def search_and_fetch_stac_multitemporal(
    bbox_wgs84: Tuple[float, float, float, float],
    t1_end_date: Optional[str] = None,
    lookback_years: int = 3,
    out_dir: Optional[Path] = None,
    stac_client: Optional[Any] = None,
) -> MultitemporalScenePair:
    """Busca y recorta un par multitemporal Sentinel-2 L2A estacionalmente comparable en POSGAR 2007."""
    from pystac_client import Client

    end_dt = (
        datetime.fromisoformat(t1_end_date.replace("Z", "+00:00"))
        if t1_end_date
        else datetime.now(timezone.utc)
    )
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    use_planetary = settings.optical_provider == "planetary_stac"
    stac_url = settings.planetary_stac_url if use_planetary else settings.earth_search_stac_url
    client = stac_client or Client.open(stac_url)

    t1_item, comparable_t0, _ = query_stac_seasonal_pair_candidates(
        client=client,
        bbox_wgs84=bbox_wgs84,
        end_dt=end_dt,
        lookback_years=lookback_years,
    )
    if t1_item is None:
        raise RuntimeError(
            f"No se hallaron escenas recientes Sentinel-2 L2A limpias (<{settings.max_cloud_cover_pct}% nubes)."
        )
    t1_dt = _extract_item_datetime(t1_item)
    if not comparable_t0:
        ref_str = t1_dt.strftime("%Y-%m-%d") if t1_dt else "desconocida"
        raise RuntimeError(
            f"No se encontraron escenas históricas T0 dentro de ±{settings.seasonal_window_days} días alrededor de {ref_str}."
        )

    t0_item = comparable_t0[0]
    bounds_posgar, crs_epsg = bbox_wgs84_to_posgar(bbox_wgs84)
    target_dir = out_dir or settings.data_dir

    t0_vnir_path, t0_swir_path, t0_meta = _export_stac_item_to_posgar_geotiffs(
        item=t0_item,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
        out_dir=target_dir,
        prefix="t0",
        sign_planetary=use_planetary,
    )
    t1_vnir_path, t1_swir_path, t1_meta = _export_stac_item_to_posgar_geotiffs(
        item=t1_item,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
        out_dir=target_dir,
        prefix="t1",
        sign_planetary=use_planetary,
    )

    return MultitemporalScenePair(
        status="success",
        t0_scene=t0_meta,
        t1_scene=t1_meta,
        t0_raster_path=str(t0_vnir_path),
        t1_raster_path=str(t1_vnir_path),
        t0_swir_raster_path=str(t0_swir_path),
        t1_swir_raster_path=str(t1_swir_path),
        crs_epsg=crs_epsg,
        bounds_posgar=bounds_posgar,
        resolution_m=10.0,
    )


def cap_rgb_chip_size(rgb: np.ndarray, max_side_px: Optional[int] = None) -> np.ndarray:
    """Garantiza que el chip RGB no supere `max_optical_chip_px` (512x512 px por defecto)."""
    cap = int(max_side_px or settings.max_optical_chip_px)
    h, w = rgb.shape[:2]
    if max(h, w) <= cap:
        return rgb
    scale = cap / float(max(h, w))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _deg2tile_float(lon_deg: float, lat_deg: float, zoom: int) -> Tuple[float, float]:
    lat_rad = math.radians(lat_deg)
    n = 2.0**zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def fetch_xyz_rgb_chip(
    patch_bbox_wgs84: Tuple[float, float, float, float],
    zoom: int = 16,
) -> Optional[np.ndarray]:
    """Descarga entre 1 y 9 teselas XYZ (256x256) y recorta al parche respetando `max_optical_chip_px`."""
    from PIL import Image

    min_lon, min_lat, max_lon, max_lat = patch_bbox_wgs84
    x0_f, y1_f = _deg2tile_float(min_lon, min_lat, zoom)
    x1_f, y0_f = _deg2tile_float(max_lon, max_lat, zoom)

    x_min, x_max = int(math.floor(x0_f)), int(math.floor(x1_f))
    y_min, y_max = int(math.floor(y0_f)), int(math.floor(y1_f))

    if (x_max - x_min + 1) * (y_max - y_min + 1) > 9:
        if zoom > 13:
            return fetch_xyz_rgb_chip(patch_bbox_wgs84, zoom=zoom - 1)
        return None

    tile_size = 256
    mosaic = np.zeros(((y_max - y_min + 1) * tile_size, (x_max - x_min + 1) * tile_size, 3), dtype=np.uint8)

    with httpx.Client(timeout=min(10.0, settings.http_timeout_seconds)) as client:
        for ty in range(y_min, y_max + 1):
            for tx in range(x_min, x_max + 1):
                url = settings.xyz_tile_url.format(z=zoom, x=tx, y=ty)
                resp = client.get(url)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                ox = (tx - x_min) * tile_size
                oy = (ty - y_min) * tile_size
                mosaic[oy : oy + tile_size, ox : ox + tile_size] = np.asarray(img, dtype=np.uint8)

    px0 = max(0, int(round((x0_f - x_min) * tile_size)))
    px1 = min(mosaic.shape[1], int(round((x1_f - x_min) * tile_size)))
    py0 = max(0, int(round((y0_f - y_min) * tile_size)))
    py1 = min(mosaic.shape[0], int(round((y1_f - y_min) * tile_size)))
    if px1 <= px0 + 4 or py1 <= py0 + 4:
        return cap_rgb_chip_size(mosaic)

    crop = mosaic[py0:py1, px0:px1]
    return cap_rgb_chip_size(crop)
