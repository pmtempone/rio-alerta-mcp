"""Gestión de proyecciones cartográficas, co-registro y reproyección POSGAR 2007 (Argentina)."""

from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple, Union

import numpy as np
import rasterio
import shapely
from affine import Affine
from pyproj import CRS, Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely.geometry import mapping, shape

from src.config import POSGAR_2007_EPSG, WGS84_EPSG

ResamplingMethodLiteral = Literal["bilinear", "nearest"]

_RESAMPLING_MAP: Dict[str, Resampling] = {
    "bilinear": Resampling.bilinear,
    "nearest": Resampling.nearest,
}


def get_posgar_faja(lon: float) -> int:
    """Determina el código EPSG de la faja POSGAR 2007 según la longitud geográfica en Argentina.

    Las fajas Gauss-Krüger de POSGAR 2007 tienen meridianos centrales cada 3 grados:
    - Faja 1 (-72°): lon < -70.5°          -> EPSG:5343
    - Faja 2 (-69°): -70.5° <= lon < -67.5° -> EPSG:5344
    - Faja 3 (-66°): -67.5° <= lon < -64.5° -> EPSG:5345
    - Faja 4 (-63°): -64.5° <= lon < -61.5° -> EPSG:5346
    - Faja 5 (-60°): -61.5° <= lon < -58.5° -> EPSG:5347
    - Faja 6 (-57°): -58.5° <= lon < -55.5° -> EPSG:5348
    - Faja 7 (-54°): lon >= -55.5°         -> EPSG:5349

    Args:
        lon: Longitud geográfica en grados decimales (WGS84, negativa al oeste).

    Returns:
        Código EPSG entero correspondiente a la faja POSGAR 2007 (5343 a 5349).
    """
    if lon < -70.5:
        faja = 1
    elif lon < -67.5:
        faja = 2
    elif lon < -64.5:
        faja = 3
    elif lon < -61.5:
        faja = 4
    elif lon < -58.5:
        faja = 5
    elif lon < -55.5:
        faja = 6
    else:
        faja = 7
    return POSGAR_2007_EPSG[faja]


def is_metric_crs(crs_input: Union[str, int, CRS, rasterio.crs.CRS]) -> bool:
    """Verifica si un sistema de referencia de coordenadas utiliza unidades lineales métricas.

    Args:
        crs_input: Representación del CRS (código EPSG, cadena WKT/PROJ o instancia CRS).

    Returns:
        True si el CRS es proyectado y sus ejes están en metros; False si es geográfico.
    """
    proj_crs = CRS.from_user_input(crs_input)
    if proj_crs.is_geographic:
        return False
    if proj_crs.is_projected:
        axis_info = proj_crs.axis_info
        if axis_info and (
            "metre" in axis_info[0].unit_name.lower() or "meter" in axis_info[0].unit_name.lower()
        ):
            return True
    return False


def ensure_metric_crs(
    src_raster_path: Union[str, Path],
    out_raster_path: Union[str, Path],
    resampling_method: ResamplingMethodLiteral = "bilinear",
) -> str:
    """Garantiza que un raster se encuentre en una proyección métrica POSGAR 2007.

    Si el raster de entrada ya está en un CRS proyectado en metros (ej. POSGAR 2007 o UTM Sur),
    retorna directamente la ruta original. Si está en coordenadas geográficas (ej. EPSG:4326),
    detecta automáticamente la faja POSGAR 2007 a partir de la longitud de su centroide y lo
    reproyecta preservando valores NoData. Usar `resampling_method='nearest'` para capas
    categóricas como `SCL` (Scene Classification Layer).

    Args:
        src_raster_path: Ruta al archivo GeoTIFF de entrada.
        out_raster_path: Ruta donde se escribirá el GeoTIFF reproyectado si fuera necesario.
        resampling_method: Método de remuestreo ('bilinear' para reflectancias, 'nearest' para máscaras).

    Returns:
        Ruta como string al archivo raster en sistema métrico lista para operar.
    """
    if resampling_method not in _RESAMPLING_MAP:
        raise ValueError(f"Método de remuestreo inválido: {resampling_method}")

    src_path = Path(src_raster_path)
    dst_path = Path(out_raster_path)

    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError(f"El raster '{src_path}' no posee un CRS definido.")

        if is_metric_crs(src.crs):
            return str(src_path)

        bounds = src.bounds
        center_lon = (bounds.left + bounds.right) / 2.0
        center_lat = (bounds.bottom + bounds.top) / 2.0

        src_proj = CRS.from_user_input(src.crs)
        if src_proj.to_epsg() != WGS84_EPSG:
            transformer_to_wgs = Transformer.from_crs(src_proj, CRS.from_epsg(WGS84_EPSG), always_xy=True)
            center_lon, center_lat = transformer_to_wgs.transform(center_lon, center_lat)

        target_epsg = get_posgar_faja(center_lon)
        dst_crs = rasterio.crs.CRS.from_epsg(target_epsg)

        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )

        is_nearest = resampling_method == "nearest"
        out_dtype = src.dtypes[0] if is_nearest else "float32"
        nodata_val: float
        if src.nodata is not None:
            nodata_val = float(src.nodata)
        elif is_nearest and np.issubdtype(np.dtype(out_dtype), np.integer):
            nodata_val = 0.0
        else:
            nodata_val = -9999.0

        kwargs = src.meta.copy()
        kwargs.update(
            {
                "crs": dst_crs,
                "transform": transform,
                "width": width,
                "height": height,
                "dtype": out_dtype,
                "nodata": nodata_val,
            }
        )

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        resamp_enum = _RESAMPLING_MAP[resampling_method]
        with rasterio.open(dst_path, "w", **kwargs) as dst:
            for band_idx in range(1, src.count + 1):
                src_raw = src.read(band_idx)
                src_arr = src_raw if is_nearest else src_raw.astype(np.float32)
                if not is_nearest and src.nodata is not None:
                    src_arr[np.isclose(src_arr, src.nodata)] = nodata_val
                dst_arr = np.full((height, width), nodata_val, dtype=src_arr.dtype)
                reproject(
                    source=src_arr,
                    destination=dst_arr,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata if is_nearest else nodata_val,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    dst_nodata=nodata_val,
                    resampling=resamp_enum,
                )
                dst.write(dst_arr, band_idx)

    return str(dst_path)


def reproject_array_to_grid(
    src_array: np.ndarray,
    src_transform: Affine,
    src_crs: Union[str, int, CRS, rasterio.crs.CRS],
    dst_transform: Affine,
    dst_crs: Union[str, int, CRS, rasterio.crs.CRS],
    dst_shape: Tuple[int, int],
    resampling_method: ResamplingMethodLiteral = "bilinear",
    src_nodata: Optional[float] = None,
    dst_nodata: Optional[float] = np.nan,
) -> np.ndarray:
    """Reproyecta o remuestrea un arreglo 2D a una grilla espacial maestra exacta.

    Permite co-registrar bandas de 20 m (`B11`, `B12` con `bilinear` y `SCL` con `nearest`) o escenas
    de distintas fechas sobre la grilla métrica común en POSGAR 2007 sin desplazamientos de subpíxel.
    """
    if resampling_method not in _RESAMPLING_MAP:
        raise ValueError(f"Método de remuestreo inválido: {resampling_method}")

    height, width = int(dst_shape[0]), int(dst_shape[1])
    resamp_enum = _RESAMPLING_MAP[resampling_method]

    if resampling_method == "nearest" and np.issubdtype(src_array.dtype, np.integer):
        fill_val = 0 if dst_nodata is None or (isinstance(dst_nodata, float) and np.isnan(dst_nodata)) else dst_nodata
        dst_arr = np.full((height, width), fill_val, dtype=src_array.dtype)
        reproject(
            source=src_array,
            destination=dst_arr,
            src_transform=src_transform,
            src_crs=src_crs,
            src_nodata=src_nodata,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=fill_val if src_nodata is not None else None,
            resampling=resamp_enum,
        )
        return dst_arr

    src_f32 = src_array.astype(np.float32, copy=False)
    fill_f32 = float(dst_nodata) if dst_nodata is not None else np.nan
    dst_f32 = np.full((height, width), fill_f32, dtype=np.float32)
    reproject(
        source=src_f32,
        destination=dst_f32,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=dst_nodata,
        resampling=resamp_enum,
    )
    return dst_f32


def align_raster_to_reference(
    src_raster_path: Union[str, Path],
    ref_raster_path: Union[str, Path],
    out_raster_path: Union[str, Path],
    resampling_method: ResamplingMethodLiteral = "bilinear",
) -> str:
    """Co-registra un raster multibanda sobre la grilla exacta (CRS, transform, shape) de un raster maestro."""
    if resampling_method not in _RESAMPLING_MAP:
        raise ValueError(f"Método de remuestreo inválido: {resampling_method}")

    src_path = Path(src_raster_path)
    ref_path = Path(ref_raster_path)
    dst_path = Path(out_raster_path)

    with rasterio.open(ref_path) as ref:
        if ref.crs is None:
            raise ValueError(f"El raster de referencia '{ref_path}' no tiene CRS definido.")
        ref_crs = ref.crs
        ref_transform = ref.transform
        ref_width = ref.width
        ref_height = ref.height

    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError(f"El raster origen '{src_path}' no tiene CRS definido.")

        if (
            src.crs == ref_crs
            and src.transform.almost_equals(ref_transform, precision=1e-6)
            and src.width == ref_width
            and src.height == ref_height
        ):
            return str(src_path)

        is_nearest = resampling_method == "nearest"
        out_dtype = src.dtypes[0] if is_nearest else "float32"
        dst_nodata: Optional[float]
        if src.nodata is not None:
            dst_nodata = float(src.nodata)
        elif is_nearest and np.issubdtype(np.dtype(out_dtype), np.integer):
            dst_nodata = 0.0
        else:
            dst_nodata = -9999.0

        meta = src.meta.copy()
        meta.update(
            {
                "driver": "GTiff",
                "crs": ref_crs,
                "transform": ref_transform,
                "width": ref_width,
                "height": ref_height,
                "dtype": out_dtype,
                "nodata": dst_nodata,
            }
        )

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **meta) as dst:
            for band_idx in range(1, src.count + 1):
                src_band = src.read(band_idx)
                aligned_band = reproject_array_to_grid(
                    src_array=src_band,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    dst_shape=(ref_height, ref_width),
                    resampling_method=resampling_method,
                    src_nodata=src.nodata,
                    dst_nodata=dst_nodata,
                )
                dst.write(aligned_band, band_idx)
            if src.descriptions:
                dst.descriptions = src.descriptions

    return str(dst_path)


def reproject_geometry(geom: Dict[str, Any], from_crs: str, to_crs: str = "EPSG:4326") -> Dict[str, Any]:
    """Reproyecta un diccionario GeoJSON de geometría entre dos sistemas de coordenadas.

    Siempre utiliza el orden de ejes (X/Longitud, Y/Latitud) mediante `always_xy=True`.

    Args:
        geom: Diccionario con estructura GeoJSON Geometry (ej. Polygon, Point, MultiPolygon).
        from_crs: CRS de origen (ej. 'EPSG:5347').
        to_crs: CRS de destino (por defecto 'EPSG:4326' para salidas GeoJSON estándar).

    Returns:
        Diccionario GeoJSON con las coordenadas reproyectadas.
    """
    src_crs_obj = CRS.from_user_input(from_crs)
    dst_crs_obj = CRS.from_user_input(to_crs)

    if src_crs_obj == dst_crs_obj:
        return geom

    transformer = Transformer.from_crs(src_crs_obj, dst_crs_obj, always_xy=True)
    shapely_geom = shape(geom)
    reprojected_shapely = shapely.transform(
        shapely_geom,
        lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1])),
    )
    return dict(mapping(reprojected_shapely))


def bbox_wgs84_to_posgar(
    bbox_wgs84: Tuple[float, float, float, float],
) -> Tuple[Tuple[float, float, float, float], int]:
    """Convierte un bounding box WGS84 `(min_lon, min_lat, max_lon, max_lat)` a POSGAR 2007 métrico.

    Determina automáticamente la faja POSGAR 2007 a partir de la longitud central del bounding box
    y reproyecta las 4 esquinas para obtener una envolvente métrica segura `(minx, miny, maxx, maxy)`.

    Args:
        bbox_wgs84: Tupla `(min_lon, min_lat, max_lon, max_lat)` en grados decimales EPSG:4326.

    Returns:
        Tupla `((minx, miny, maxx, maxy), epsg_code)` en metros POSGAR 2007.
    """
    min_lon, min_lat, max_lon, max_lat = [float(v) for v in bbox_wgs84]
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError(
            f"Bounding box WGS84 inválido: {bbox_wgs84}. Debe cumplir min_lon < max_lon y min_lat < max_lat."
        )

    center_lon = (min_lon + max_lon) / 2.0
    target_epsg = get_posgar_faja(center_lon)
    transformer = Transformer.from_crs(CRS.from_epsg(WGS84_EPSG), CRS.from_epsg(target_epsg), always_xy=True)

    corners = [
        (min_lon, min_lat),
        (min_lon, max_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
    ]
    xs = []
    ys = []
    for lon, lat in corners:
        x, y = transformer.transform(lon, lat)
        xs.append(float(x))
        ys.append(float(y))

    return (min(xs), min(ys), max(xs), max(ys)), target_epsg
