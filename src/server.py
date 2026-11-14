"""Servidor FastMCP principal para RíoAlerta (`rio-alerta-mcp`)."""

from typing import Any, Dict

from fastmcp import FastMCP

from src import __version__
from src.config import POSGAR_2007_EPSG, settings
from src.core.change_detector import (
    compute_multitemporal_baseline_and_changes,
    detect_extraction_anomalies,
)
from src.core.crs_manager import get_posgar_faja
from src.core.parana_pilot_recon import evaluate_parana_pilot_readiness
from src.schemas import ChangeDetectionResult, SpectralChangeResult

mcp = FastMCP(
    "RioAlerta-MCP",
    instructions=(
        "Servidor especializado en vigilancia satelital multitemporal de extracción ilegal de áridos "
        "en ríos argentinos para autoridades ambientales, municipios, Prefectura Naval y organismos "
        "de recursos hídricos. Opera sobre escenas Sentinel-2 L2A (T0 vs T1) respetando resoluciones "
        "nativas (10 m VNIR / 20 m SWIR+SCL), controles de dinámica fluvial natural (crecidas, bajantes, "
        "migración de barras y fenología ribereña), cruce catastral de concesiones (data/permits.gpkg) "
        "y actas técnicas reproducibles en POSGAR 2007 (EPSG:5343-5349)."
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
            "seasonal_window_days": settings.seasonal_window_days,
            "min_valid_pixel_fraction": settings.min_valid_pixel_fraction,
            "baseline_max_scenes": settings.baseline_max_scenes,
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


@mcp.tool()
def tool_compute_spectral_changes(
    t0_path: str,
    t1_path: str,
    t0_swir_20m_path: str | None = None,
    t1_swir_20m_path: str | None = None,
) -> SpectralChangeResult:
    """Calcula diferencias espectrales multitemporales ΔMNDWI, ΔBSI y ΔNDVI entre T0 y T1.

    Respeta resoluciones nativas Sentinel-2 (10 m VNIR y 20 m SWIR/SCL), aplica máscara SCL y
    evalúa la variación hidrométrica global del tramo en coordenadas POSGAR 2007.
    """
    settings.ensure_directories()
    return compute_multitemporal_baseline_and_changes(
        t0_vnir_paths=t0_path,
        t1_vnir_path=t1_path,
        out_dir=settings.processed_dir,
        t0_swir_scl_paths=t0_swir_20m_path,
        t1_swir_scl_path=t1_swir_20m_path,
    )


@mcp.tool()
def tool_detect_extraction_anomalies(
    t0_path: str,
    t1_path: str,
    t0_swir_20m_path: str | None = None,
    t1_swir_20m_path: str | None = None,
    min_area_m2: float = 1600.0,
) -> ChangeDetectionResult:
    """Detecta polígonos de anomalías morfológicas candidatas filtrando cambios fluviales naturales.

    Aplica controles de crecida/bajante uniforme, dipolos de migración natural de bancos de arena,
    fenología ribereña y máscara de nubes/sombras SCL, emitiendo métricas de incertidumbre.
    """
    settings.ensure_directories()
    return detect_extraction_anomalies(
        t0_vnir_paths=t0_path,
        t1_vnir_path=t1_path,
        t0_swir_scl_paths=t0_swir_20m_path,
        t1_swir_scl_path=t1_swir_20m_path,
        min_area_m2=min_area_m2,
        work_dir=settings.processed_dir,
    )


@mcp.tool()
def tool_evaluate_parana_pilot(offline_mode: bool = True) -> Dict[str, Any]:
    """Evalúa factibilidad geodésica, STAC e institucional del tramo piloto Río Paraná Medio."""
    return evaluate_parana_pilot_readiness(offline_mode=offline_mode)


if __name__ == "__main__":
    mcp.run()
