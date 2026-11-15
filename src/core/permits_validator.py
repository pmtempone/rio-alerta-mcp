"""Validación espacial y temporal contra catastro de concesiones e hidrografía en POSGAR 2007."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import geopandas as gpd
import httpx
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from src.config import POSGAR_2007_EPSG, settings
from src.core.crs_manager import reproject_geometry
from src.schemas import (
    CadastreMetadata,
    ExtractionAnomalyFeature,
    InspectionPriorityLiteral,
    PermitClassificationLiteral,
    PermitMatchInfo,
    PermitValidationResult,
    coerce_anomaly_feature,
)

_NEARBY_PERMIT_SEARCH_RADIUS_M = 500.0


def validate_posgar_epsg(crs_epsg: int) -> int:
    """Verifica que el código EPSG pertenezca a las fajas métricas POSGAR 2007 (5343-5349)."""
    valid_codes = set(POSGAR_2007_EPSG.values())
    if crs_epsg not in valid_codes:
        raise ValueError(
            f"CRS inválido (EPSG:{crs_epsg}). Se requiere una faja métrica oficial POSGAR 2007 "
            f"{sorted(valid_codes)}."
        )
    return crs_epsg


def _parse_iso_date_prefix(date_str: str, label: str = "fecha") -> str:
    """Valida y normaliza una fecha ISO-8601 al prefijo YYYY-MM-DD."""
    cleaned = date_str.strip()
    if not cleaned:
        raise ValueError(f"{label} vacía; se requiere formato ISO YYYY-MM-DD.")
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} inválida ('{date_str}'): se requiere formato ISO YYYY-MM-DD.") from exc
    return parsed.date().isoformat()


def _repair_geometry(geom: BaseGeometry) -> BaseGeometry:
    """Repara geometrías topológicamente inválidas mediante buffer(0) sin perder área válida."""
    if not geom.is_valid:
        repaired = geom.buffer(0.0)
        if not repaired.is_empty:
            return repaired
    return geom


def _get_col_value(row: Any, candidates: Sequence[str], default: Any = None) -> Any:
    """Obtiene el primer valor no nulo entre posibles nombres de columnas de un padrón catastral."""
    for col in candidates:
        if col in row and row[col] is not None:
            val = row[col]
            if isinstance(val, float) and val != val:  # NaN check
                continue
            text = str(val).strip()
            if text and text.lower() not in {"nan", "none", "nat"}:
                return val
    return default


def _normalize_optional_date(val: Any, fallback: str) -> str:
    """Normaliza una fecha proveniente de tabla GeoPackage/GeoJSON a YYYY-MM-DD o fallback."""
    if val is None:
        return fallback
    text = str(val).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return fallback
    if len(text) >= 10:
        try:
            return _parse_iso_date_prefix(text[:10], label="fecha_permiso")
        except ValueError:
            pass
    return text[:10]


def _is_permit_active_on_date(fecha_desde: str, fecha_hasta: str, target_date_iso: str) -> bool:
    """Evalúa si un permiso se encontraba vigente en la fecha T1."""
    if fecha_hasta and len(fecha_hasta) >= 10 and fecha_hasta != "9999-12-31":
        if fecha_hasta[:10] < target_date_iso[:10]:
            return False
    if fecha_desde and len(fecha_desde) >= 10 and fecha_desde != "1900-01-01":
        if fecha_desde[:10] > target_date_iso[:10]:
            return False
    return True


def _reproject_gdf_to_posgar(gdf: gpd.GeoDataFrame, target_epsg: int) -> gpd.GeoDataFrame:
    """Reproyecta un GeoDataFrame a POSGAR 2007 (`EPSG:{target_epsg}`)."""
    if gdf.empty:
        return gdf
    work = gdf.copy()
    if work.crs is None:
        minx, miny, maxx, maxy = work.total_bounds
        if -180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0 and -90.0 <= miny <= 90.0 and -90.0 <= maxy <= 90.0:
            work = work.set_crs(epsg=4326)
        else:
            work = work.set_crs(epsg=target_epsg)
    if work.crs is not None and work.crs.to_epsg() != target_epsg:
        work = work.to_crs(epsg=target_epsg)
    return work


def _assess_hydrography(
    geom_posgar: BaseGeometry,
    anomaly: ExtractionAnomalyFeature,
    watercourses_gdf: Optional[gpd.GeoDataFrame],
    query_remote_hydrography: bool,
) -> Tuple[Optional[float], Optional[str], List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Calcula distancia a cursos de agua locales y opcionalmente consulta OSM/Overpass."""
    dist_m: Optional[float] = anomaly.distance_to_water_t0_m
    watercourse_name: Optional[str] = None
    osm_matches: List[Dict[str, Any]] = []
    ign_matches: List[Dict[str, Any]] = []
    notes: List[str] = []

    if watercourses_gdf is not None and not watercourses_gdf.empty:
        water_posgar = _reproject_gdf_to_posgar(watercourses_gdf, anomaly.crs_epsg)
        best_dist: Optional[float] = None
        best_name: Optional[str] = None
        for _, wrow in water_posgar.iterrows():
            wgeom = wrow.geometry
            if wgeom is None or wgeom.is_empty:
                continue
            d = float(geom_posgar.distance(wgeom))
            wname = _get_col_value(wrow, ("nombre", "name", "fna", "waterway", "curso"), default=None)
            ign_matches.append(
                {
                    "name": str(wname) if wname is not None else "Curso de agua sin nombre",
                    "distance_m": round(d, 2),
                    "source": "local_hydrography_layer",
                }
            )
            if best_dist is None or d < best_dist:
                best_dist = d
                best_name = str(wname) if wname is not None else None
        if best_dist is not None:
            dist_m = round(best_dist, 2)
            watercourse_name = best_name

    if query_remote_hydrography:
        lon, lat = anomaly.centroid_wgs84
        overpass_query = (
            f"[out:json][timeout:10];"
            f"(way(around:1500,{lat:.6f},{lon:.6f})[waterway];"
            f"way(around:1500,{lat:.6f},{lon:.6f})[natural=water];);"
            f"out tags center 10;"
        )
        try:
            resp = httpx.post(
                settings.overpass_url,
                data={"data": overpass_query},
                timeout=min(10.0, settings.http_timeout_seconds),
            )
            resp.raise_for_status()
            payload = resp.json()
            elements = payload.get("elements", []) if isinstance(payload, dict) else []
            for elem in elements[:10]:
                tags = elem.get("tags", {}) if isinstance(elem, dict) else {}
                osm_name = tags.get("name") or tags.get("waterway") or tags.get("natural") or "OSM water feature"
                osm_matches.append(
                    {
                        "osm_id": elem.get("id"),
                        "type": elem.get("type"),
                        "name": osm_name,
                        "waterway": tags.get("waterway"),
                        "natural": tags.get("natural"),
                    }
                )
                if watercourse_name is None and tags.get("name"):
                    watercourse_name = str(tags["name"])
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Consulta remota Overpass no disponible ({exc}); se conserva hidrografía local/T0.")

    return dist_m, watercourse_name, osm_matches, ign_matches, notes


def validate_anomaly_permits(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
    t1_date: str,
    permits_path: Optional[Union[str, Path]] = None,
    cadastre_metadata: Optional[CadastreMetadata] = None,
    excess_tolerance_pct: Optional[float] = None,
    watercourses_gdf: Optional[gpd.GeoDataFrame] = None,
    query_remote_hydrography: bool = False,
) -> PermitValidationResult:
    """Valida una anomalía candidata contra el catastro local de permisos e hidrografía en POSGAR 2007.

    Mantiene estricta prudencia institucional: nunca imputa ilegalidad automática, respeta el grado
    de completitud del padrón (`CadastreMetadata.cadastre_status`) y exige siempre revisión humana.
    """
    feat = coerce_anomaly_feature(anomaly)
    validate_posgar_epsg(feat.crs_epsg)
    t1_iso = _parse_iso_date_prefix(t1_date, label="t1_date")

    geom_posgar_geojson = reproject_geometry(
        feat.geometry_geojson,
        from_crs="EPSG:4326",
        to_crs=f"EPSG:{feat.crs_epsg}",
    )
    geom_posgar = _repair_geometry(shape(geom_posgar_geojson))
    anomaly_area_m2 = float(geom_posgar.area) if geom_posgar.area > 0.0 else float(feat.area_m2)

    permits_file = Path(permits_path) if permits_path is not None else settings.permits_gpkg_path
    tol_pct = float(excess_tolerance_pct if excess_tolerance_pct is not None else settings.excess_tolerance_pct)
    if tol_pct < 0.0:
        raise ValueError("excess_tolerance_pct debe ser mayor o igual a 0.")

    dist_water_m, watercourse_name, osm_matches, ign_matches, hydro_notes = _assess_hydrography(
        geom_posgar=geom_posgar,
        anomaly=feat,
        watercourses_gdf=watercourses_gdf,
        query_remote_hydrography=query_remote_hydrography,
    )

    if not permits_file.exists() or (
        cadastre_metadata is not None and cadastre_metadata.cadastre_status == "NO_DISPONIBLE"
    ):
        meta = cadastre_metadata or CadastreMetadata(
            cadastre_status="NO_DISPONIBLE",
            jurisdiction="No especificada",
            source_reference=str(permits_file),
        )
        if not permits_file.exists() and meta.cadastre_status != "NO_DISPONIBLE":
            meta = meta.model_copy(update={"cadastre_status": "NO_DISPONIBLE"})

        priority: InspectionPriorityLiteral = (
            "ALTA_FISCALIZACION_INMEDIATA"
            if (feat.confidence >= 0.80 and feat.area_m2 >= 5000.0)
            else "MEDIA_VERIFICACION"
        )
        obs_parts = [
            f"Sin padrón catastral oficial cargado (archivo consultado: '{permits_file}').",
            "La ausencia de coincidencia o de registro digital cargado NO constituye prueba de actividad "
            "no autorizada; requiere consulta documental al organismo provincial/municipal competente y verificación "
            "en terreno.",
        ]
        obs_parts.extend(hydro_notes)
        return PermitValidationResult(
            anomaly_id=feat.id,
            classification="SIN_CATASTRO_OFICIAL_CARGADO",
            matched_permits=[],
            excess_area_m2=round(anomaly_area_m2, 2),
            excess_pct=100.0,
            distance_to_water_m=dist_water_m,
            watercourse_name=watercourse_name,
            osm_matches=osm_matches,
            ign_matches=ign_matches,
            priority=priority,
            observations=" ".join(obs_parts),
            cadastre_metadata=meta,
            human_review_required=True,
        )

    permits_gdf = gpd.read_file(permits_file)
    meta = cadastre_metadata or CadastreMetadata(
        cadastre_status="PARCIAL_LOCAL",
        jurisdiction="No especificada",
        source_reference=str(permits_file),
    )

    if permits_gdf.empty:
        empty_class_map: Dict[str, PermitClassificationLiteral] = {
            "OFICIAL_COMPLETO": "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS",
            "PARCIAL_LOCAL": "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL",
            "NO_DISPONIBLE": "SIN_CATASTRO_OFICIAL_CARGADO",
        }
        obs_parts = [
            f"El catastro '{permits_file}' ({meta.cadastre_status}) no contiene registros de concesiones.",
            "Se requiere verificación administrativa directa ante el organismo competente.",
        ]
        obs_parts.extend(hydro_notes)
        return PermitValidationResult(
            anomaly_id=feat.id,
            classification=empty_class_map[meta.cadastre_status],
            matched_permits=[],
            excess_area_m2=round(anomaly_area_m2, 2),
            excess_pct=100.0,
            distance_to_water_m=dist_water_m,
            watercourse_name=watercourse_name,
            osm_matches=osm_matches,
            ign_matches=ign_matches,
            priority="MEDIA_VERIFICACION",
            observations=" ".join(obs_parts),
            cadastre_metadata=meta,
            human_review_required=True,
        )

    permits_posgar = _reproject_gdf_to_posgar(permits_gdf, feat.crs_epsg)
    overlapping_entries: List[Tuple[PermitMatchInfo, BaseGeometry]] = []
    nearby_entries: List[PermitMatchInfo] = []

    for idx, prow in permits_posgar.iterrows():
        raw_pgeom = prow.geometry
        if raw_pgeom is None or raw_pgeom.is_empty:
            continue
        pgeom = _repair_geometry(raw_pgeom)
        dist = float(geom_posgar.distance(pgeom))
        inter_area = float(geom_posgar.intersection(pgeom).area)
        if inter_area <= 0.0 and dist > _NEARBY_PERMIT_SEARCH_RADIUS_M:
            continue

        permit_id = str(_get_col_value(prow, ("permit_id", "id", "codigo", "nro_permiso"), default=f"PERM-{idx}"))
        titular = str(_get_col_value(prow, ("titular", "empresa", "razon_social", "holder"), default="No informado"))
        expediente = _get_col_value(prow, ("expediente", "nro_expediente", "file_id"), default=None)
        fecha_desde = _normalize_optional_date(
            _get_col_value(prow, ("fecha_desde", "valid_from", "desde", "inicio"), default=None),
            fallback="1900-01-01",
        )
        fecha_hasta = _normalize_optional_date(
            _get_col_value(prow, ("fecha_hasta", "valid_to", "hasta", "vencimiento"), default=None),
            fallback="9999-12-31",
        )
        vigente = _is_permit_active_on_date(fecha_desde, fecha_hasta, t1_iso)
        activity = _get_col_value(prow, ("permitted_activity", "actividad", "activity"), default=None)
        vol_raw = _get_col_value(
            prow,
            ("authorized_volume_limit_m3", "volumen_m3", "volume_limit_m3"),
            default=None,
        )
        vol_limit = float(vol_raw) if vol_raw is not None else None

        overlap_pct = round(min(100.0, (inter_area / anomaly_area_m2) * 100.0), 2)
        excess_area = round(max(0.0, anomaly_area_m2 - inter_area), 2)
        excess_pct = round(max(0.0, ((anomaly_area_m2 - inter_area) / anomaly_area_m2) * 100.0), 2)

        match_info = PermitMatchInfo(
            permit_id=permit_id,
            titular=titular,
            expediente=str(expediente) if expediente is not None else None,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            vigente_en_t1=vigente,
            overlap_area_m2=round(inter_area, 2),
            overlap_pct=overlap_pct,
            excess_area_m2=excess_area,
            excess_pct=excess_pct,
            distance_m=0.0 if inter_area > 0.0 else round(dist, 2),
            permitted_activity=str(activity) if activity is not None else None,
            authorized_volume_limit_m3=vol_limit,
        )
        if inter_area > 0.0:
            overlapping_entries.append((match_info, pgeom))
        else:
            nearby_entries.append(match_info)

    overlapping_entries.sort(key=lambda pair: pair[0].overlap_area_m2, reverse=True)
    nearby_entries.sort(key=lambda info: info.distance_m)
    matched_permits: List[PermitMatchInfo] = [m for m, _ in overlapping_entries] + nearby_entries

    obs_parts = []
    if overlapping_entries:
        active_geoms = [pgeom for m, pgeom in overlapping_entries if m.vigente_en_t1]
        if active_geoms:
            active_union = unary_union(active_geoms)
            covered_area_m2 = float(geom_posgar.intersection(active_union).area)
            total_excess_area_m2 = round(max(0.0, anomaly_area_m2 - covered_area_m2), 2)
            total_excess_pct = round(max(0.0, (total_excess_area_m2 / anomaly_area_m2) * 100.0), 2)
            if total_excess_pct <= tol_pct:
                classification: PermitClassificationLiteral = "EXTRACCION_AUTORIZADA"
                priority = "BAJA_ARCHIVO"
                obs_parts.append(
                    f"La anomalía se encuentra cubierta en un {100.0 - total_excess_pct:.1f}% por concesión vigente "
                    f"en T1 ({t1_iso}), dentro de la tolerancia de borde ({tol_pct:.1f}%)."
                )
            else:
                classification = "EXCESO_DE_POLIGONO"
                priority = (
                    "ALTA_FISCALIZACION_INMEDIATA" if total_excess_area_m2 >= 1600.0 else "MEDIA_VERIFICACION"
                )
                obs_parts.append(
                    f"Se detecta exceso fuera de polígono autorizado vigente equivalente a {total_excess_area_m2:.2f} m² "
                    f"({total_excess_pct:.1f}% > tolerancia {tol_pct:.1f}%). Requiere deslinde topográfico y revisión humana."
                )
        else:
            classification = "PERMISO_VENCIDO"
            total_excess_area_m2 = round(anomaly_area_m2, 2)
            total_excess_pct = 100.0
            priority = "ALTA_FISCALIZACION_INMEDIATA"
            obs_parts.append(
                f"La anomalía intersecta concesión(es) cuyo vencimiento es anterior a T1 ({t1_iso}). "
                "Requiere verificación de renovaciones o prórrogas en trámite con la autoridad minera/ambiental."
            )
    else:
        total_excess_area_m2 = round(anomaly_area_m2, 2)
        total_excess_pct = 100.0
        if meta.cadastre_status == "OFICIAL_COMPLETO":
            classification = "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS"
            priority = "ALTA_FISCALIZACION_INMEDIATA" if feat.confidence >= 0.75 else "MEDIA_VERIFICACION"
        elif meta.cadastre_status == "PARCIAL_LOCAL":
            classification = "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL"
            priority = "MEDIA_VERIFICACION"
        else:
            classification = "SIN_CATASTRO_OFICIAL_CARGADO"
            priority = "MEDIA_VERIFICACION"

        obs_parts.append(
            f"Sin intersección espacial en el catastro consultado ({meta.cadastre_status}, '{permits_file}')."
        )
        if nearby_entries:
            nearest = nearby_entries[0]
            obs_parts.append(
                f"Existe una concesión cercana a {nearest.distance_m:.1f} m (permit_id={nearest.permit_id})."
            )
        obs_parts.append(
            "La ausencia de coincidencia en este archivo requiere verificación administrativa oficial antes de cualquier acción."
        )

    obs_parts.extend(hydro_notes)
    return PermitValidationResult(
        anomaly_id=feat.id,
        classification=classification,
        matched_permits=matched_permits,
        excess_area_m2=total_excess_area_m2,
        excess_pct=total_excess_pct,
        distance_to_water_m=dist_water_m,
        watercourse_name=watercourse_name,
        osm_matches=osm_matches,
        ign_matches=ign_matches,
        priority=priority,
        observations=" ".join(obs_parts),
        cadastre_metadata=meta,
        human_review_required=True,
    )
