"""Servidor FastMCP principal para RíoAlerta (`rio-alerta-mcp`)."""

from typing import Any, Dict

from fastmcp import FastMCP

from src import __version__
from src.config import POSGAR_2007_EPSG, settings
from src.core.crs_manager import get_posgar_faja

mcp = FastMCP(
    "RioAlerta-MCP",
    instructions=(
        "Servidor especializado en vigilancia satelital multitemporal de extracción ilegal de áridos "
        "en ríos argentinos para autoridades ambientales, municipios, Prefectura Naval y organismos "
        "de recursos hídricos. Opera sobre escenas Sentinel-2 L2A (T0 vs T1) con índices ΔMNDWI, "
        "ΔBSI y ΔNDVI, cruce catastral de concesiones (data/permits.gpkg), validación hidrográfica "
        "IGN/OSM, inspección multimodal VLM y actas técnicas reproducibles en POSGAR 2007 (EPSG:5343-5349)."
    ),
)


@mcp.tool()
def tool_server_status() -> Dict[str, Any]:
    """Verifica el estado operativo del servidor RíoAlerta-MCP y su configuración geoespacial.

    Asegura que los directorios de trabajo existan y reporta rutas, fajas POSGAR 2007,
    parámetros de red STAC/IGN/OSM y topes de procesamiento configurados.

    Returns:
        Diccionario con el estado del servidor, versión, rutas y configuración activa.
    """
    settings.ensure_directories()
    return {
        "status": "ok",
        "server": "RioAlerta-MCP",
        "version": __version__,
        "posgar_2007_epsg": POSGAR_2007_EPSG,
        "optical_provider": settings.optical_provider,
        "permits_gpkg_path": str(settings.permits_gpkg_path),
        "permits_gpkg_exists": settings.permits_gpkg_path.exists(),
        "directories": {
            "data_dir": str(settings.data_dir),
            "processed_dir": str(settings.processed_dir),
            "reports_dir": str(settings.reports_dir),
        },
        "limits": {
            "max_cloud_cover_pct": settings.max_cloud_cover_pct,
            "max_master_side_px": settings.max_master_side_px,
            "max_optical_chip_px": settings.max_optical_chip_px,
            "optical_buffer_m": settings.optical_buffer_m,
            "min_anomaly_area_m2": settings.min_anomaly_area_m2,
            "excess_tolerance_pct": settings.excess_tolerance_pct,
        },
    }


@mcp.tool()
def tool_resolve_posgar_crs(lon: float, lat: float = -32.9) -> Dict[str, Any]:
    """Resuelve automáticamente la faja métrica oficial POSGAR 2007 para una coordenada en Argentina.

    Args:
        lon: Longitud geográfica en grados decimales WGS84 (negativa al oeste).
        lat: Latitud geográfica de referencia en grados decimales WGS84 (por defecto -32.9).

    Returns:
        Diccionario con el código EPSG POSGAR 2007, número de faja y coordenadas métricas proyectadas.
    """
    from pyproj import CRS, Transformer

    epsg_code = get_posgar_faja(lon)
    faja_num = next(k for k, v in POSGAR_2007_EPSG.items() if v == epsg_code)
    transformer = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg_code), always_xy=True)
    x_m, y_m = transformer.transform(float(lon), float(lat))
    return {
        "lon_wgs84": float(lon),
        "lat_wgs84": float(lat),
        "posgar_faja": faja_num,
        "crs_epsg": epsg_code,
        "crs_name": f"POSGAR 2007 / Argentina {faja_num}",
        "x_posgar_m": round(float(x_m), 2),
        "y_posgar_m": round(float(y_m), 2),
    }


if __name__ == "__main__":
    mcp.run()
