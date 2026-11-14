"""Evaluación de factibilidad del primer tramo piloto sobre el Río Paraná Medio (Santa Fe - Paraná)."""

from typing import Any, Dict, List, Optional, Tuple

from src.config import settings
from src.core.crs_manager import bbox_wgs84_to_posgar
from src.core.optical_providers import (
    _extract_item_datetime,
    query_stac_seasonal_pair_candidates,
)

PARANA_MID_PILOT_BBOX_WGS84: Tuple[float, float, float, float] = (-60.72, -31.78, -60.52, -31.60)


def evaluate_parana_pilot_readiness(
    offline_mode: bool = False,
    stac_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Releva disponibilidad técnica y brechas institucionales para el tramo piloto Paraná Medio.

    Evalúa:
    1. Proyección métrica POSGAR 2007 Faja 5 (`EPSG:5347`) y área del tramo.
    2. Cobertura histórica Sentinel-2 L2A en STAC con ventana estacional comparable (si `offline_mode=False`).
    3. Estado del catastro local de permisos (`settings.permits_gpkg_path`) sin inventar concesiones.
    """
    bbox_posgar, crs_epsg = bbox_wgs84_to_posgar(PARANA_MID_PILOT_BBOX_WGS84)
    width_km = (bbox_posgar[2] - bbox_posgar[0]) / 1000.0
    height_km = (bbox_posgar[3] - bbox_posgar[1]) / 1000.0

    stac_summary: Dict[str, Any] = {
        "queried": False,
        "provider": settings.optical_provider,
        "endpoint": settings.earth_search_stac_url,
        "recent_clean_scenes": None,
        "latest_t1_date": None,
        "comparable_t0_count": None,
        "comparable_t0_dates": [],
        "status": "offline_skipped",
    }

    if not offline_mode or stac_client is not None:
        try:
            from pystac_client import Client

            client = stac_client or Client.open(settings.earth_search_stac_url)
            t1_item, comparable_t0, recent_count = query_stac_seasonal_pair_candidates(
                client=client,
                bbox_wgs84=PARANA_MID_PILOT_BBOX_WGS84,
                lookback_years=3,
            )
            stac_summary["queried"] = True
            stac_summary["recent_clean_scenes"] = recent_count
            if t1_item is not None:
                t1_dt = _extract_item_datetime(t1_item)
                stac_summary["latest_t1_date"] = t1_dt.strftime("%Y-%m-%d") if t1_dt else None
                stac_summary["comparable_t0_count"] = len(comparable_t0)
                t0_dates = []
                for it in comparable_t0[:5]:
                    it_dt = _extract_item_datetime(it)
                    if it_dt is not None:
                        t0_dates.append(it_dt.strftime("%Y-%m-%d"))
                stac_summary["comparable_t0_dates"] = t0_dates
                stac_summary["status"] = "ready" if comparable_t0 else "insufficient_seasonal_baseline"
            else:
                stac_summary["status"] = "no_clean_scenes_found"
        except Exception as exc:  # noqa: BLE001
            stac_summary["queried"] = True
            stac_summary["status"] = f"network_error: {exc}"

    permits_exists = settings.permits_gpkg_path.exists()
    public_datasets_available: List[str] = [
        "Sentinel-2 L2A (COG 10m VNIR / 20m SWIR+SCL vía Element 84 STAC / Planetary Computer)",
        "Hidrografía vectorial pública IGN Argentina WFS (capa ign:linea_de_agua)",
        "Red hidrográfica e infraestructura ribereña OpenStreetMap (Overpass API)",
    ]
    institutional_gaps: List[str] = []
    if not permits_exists:
        institutional_gaps.append(
            f"Falta archivo local de catastro de permisos ({settings.permits_gpkg_path}). "
            "Se requiere padrón oficial de concesiones mineras/areneras (Santa Fe / Entre Ríos / SSRH) "
            "con geometría poligonal, titular, vigencia (fecha_desde/fecha_hasta) y actividad autorizada."
        )

    return {
        "pilot_corridor": "Río Paraná Medio — Tramo Santa Fe / Paraná – Bajada Grande",
        "bbox_wgs84": PARANA_MID_PILOT_BBOX_WGS84,
        "crs_epsg": crs_epsg,
        "bbox_posgar": tuple(round(v, 2) for v in bbox_posgar),
        "reach_dimensions_km": (round(width_km, 2), round(height_km, 2)),
        "stac_readiness": stac_summary,
        "permits_cadastre_loaded": permits_exists,
        "public_datasets_available": public_datasets_available,
        "institutional_gaps": institutional_gaps,
    }
