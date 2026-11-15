"""Validación espacial y temporal contra catastro de concesiones e hidrografía en POSGAR 2007."""

from __future__ import annotations

import math
import unicodedata
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
    PermitActivityStatusLiteral,
    PermitClassificationLiteral,
    PermitMatchInfo,
    PermitValidationResult,
    PermitValidityStatusLiteral,
    coerce_anomaly_feature,
)

_NEARBY_PERMIT_SEARCH_RADIUS_M = 500.0

_COMPATIBLE_ACTIVITY_TOKENS = (
    "arena",
    "arido",
    "cantera",
    "dragad",
    "extraccion",
    "acopio",
    "refulado",
    "mineria",
    "minero",
    "grava",
    "ripio",
    "suelo",
    "sedimento",
    "hidraulic",
    "portuari",
    "vial",
    "terraplen",
)

_INCOMPATIBLE_ACTIVITY_TOKENS = (
    "prohib",
    "veda",
    "sin extraccion",
    "prohibicion",
    "no extractiv",
)


def validate_posgar_epsg(crs_epsg: int) -> int:
    """Verifica que el código EPSG pertenezca a las fajas métricas POSGAR 2007 (5343-5349)."""
    valid_codes = set(POSGAR_2007_EPSG.values())
    if crs_epsg not in valid_codes:
        raise ValueError(
            f"CRS inválido (EPSG:{crs_epsg}). Se requiere una faja métrica oficial POSGAR 2007 "
            f"{sorted(valid_codes)}."
        )
    return crs_epsg


def _normalize_text(text: str) -> str:
    """Normaliza tildes y capitalización para comparación semántica de actividades."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


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


def _parse_strict_permit_date(val: Any) -> Tuple[Optional[str], Optional[str]]:
    """Parsea estrictamente una fecha catastral; devuelve `(iso_yyyy_mm_dd, error_msg)`."""
    if val is None:
        return None, "fecha ausente"
    text = str(val).strip()
    if not text or text.lower() in {"nan", "none", "nat", "null", "n/d", "s/d"}:
        return None, "fecha ausente"
    candidate = text[:10]
    try:
        parsed = datetime.strptime(candidate, "%Y-%m-%d").date()
        return parsed.isoformat(), None
    except ValueError:
        return None, f"fecha inválida '{text}'"


def _evaluate_permit_validity(
    raw_desde: Any,
    raw_hasta: Any,
    t1_iso: str,
) -> Tuple[bool, PermitValidityStatusLiteral, Optional[str], Optional[str], Optional[str]]:
    """Diferencia vigencia confirmada, vencida, no vigente aún o vigencia desconocida.

    Nunca asigna fechas sintéticas ('1900-01-01' / '9999-12-31') ni presume vigencia ante
    fechas faltantes o malformadas.
    """
    desde_iso, err_d = _parse_strict_permit_date(raw_desde)
    hasta_iso, err_h = _parse_strict_permit_date(raw_hasta)

    raw_d_str = None if raw_desde is None or str(raw_desde).strip().lower() in {"", "nan", "none", "nat"} else str(raw_desde).strip()
    raw_h_str = None if raw_hasta is None or str(raw_hasta).strip().lower() in {"", "nan", "none", "nat"} else str(raw_hasta).strip()

    if err_d or err_h or desde_iso is None or hasta_iso is None:
        reasons = [r for r in (err_d, err_h) if r]
        return (
            False,
            "VIGENCIA_DESCONOCIDA",
            desde_iso or raw_d_str,
            hasta_iso or raw_h_str,
            f"Vigencia no verificable ({'; '.join(reasons)}).",
        )

    if desde_iso > hasta_iso:
        return (
            False,
            "VIGENCIA_DESCONOCIDA",
            desde_iso,
            hasta_iso,
            f"Rango de fechas inconsistente (fecha_desde={desde_iso} > fecha_hasta={hasta_iso}).",
        )

    if hasta_iso < t1_iso:
        return False, "VENCIDO", desde_iso, hasta_iso, None
    if desde_iso > t1_iso:
        return (
            False,
            "NO_VIGENTE_AUN",
            desde_iso,
            hasta_iso,
            f"Inicio de vigencia ({desde_iso}) posterior a T1 ({t1_iso}).",
        )

    return True, "VIGENTE_CONFIRMADO", desde_iso, hasta_iso, None


def _evaluate_permit_activity(raw_activity: Any) -> Tuple[PermitActivityStatusLiteral, Optional[str]]:
    """Clasifica si la actividad declarada en el permiso habilita extracción/movimiento de áridos."""
    if raw_activity is None:
        return "SIN_ACTIVIDAD_DECLARADA", None
    text = str(raw_activity).strip()
    if not text or text.lower() in {"nan", "none", "nat", "null", "n/d", "s/d"}:
        return "SIN_ACTIVIDAD_DECLARADA", None

    norm = _normalize_text(text)
    if any(tok in norm for tok in _INCOMPATIBLE_ACTIVITY_TOKENS):
        return "INCOMPATIBLE", text
    if any(tok in norm for tok in _COMPATIBLE_ACTIVITY_TOKENS):
        return "COMPATIBLE", text
    return "INCOMPATIBLE", text


def _repair_geometry(geom: BaseGeometry) -> BaseGeometry:
    """Repara geometrías topológicamente inválidas mediante buffer(0) sin perder área válida."""
    if not geom.is_valid:
        repaired = geom.buffer(0.0)
        if not repaired.is_empty:
            return repaired
    return geom


def _validate_and_build_anomaly_geometry(feat: ExtractionAnomalyFeature) -> Tuple[BaseGeometry, float]:
    """Construye y valida estrictamente la geometría poligonal de la anomalía en POSGAR 2007."""
    geojson = feat.geometry_geojson
    if not isinstance(geojson, dict) or not geojson.get("type") or not geojson.get("coordinates"):
        raise ValueError(f"La anomalía '{feat.id}' carece de coordenadas GeoJSON válidas.")

    geom_posgar_geojson = reproject_geometry(
        geojson,
        from_crs="EPSG:4326",
        to_crs=f"EPSG:{feat.crs_epsg}",
    )
    geom_raw = shape(geom_posgar_geojson)
    if geom_raw.is_empty:
        raise ValueError(f"La geometría reproyectada de la anomalía '{feat.id}' está vacía.")

    geom_posgar = _repair_geometry(geom_raw)
    if geom_posgar.is_empty or not geom_posgar.is_valid:
        raise ValueError(f"La geometría de la anomalía '{feat.id}' es topológicamente inválida e irrecuperable.")
    if geom_posgar.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(
            f"La anomalía '{feat.id}' debe ser Polygon o MultiPolygon (recibido: {geom_posgar.geom_type})."
        )
    area_m2 = float(geom_posgar.area)
    if not math.isfinite(area_m2) or area_m2 <= 0.0:
        raise ValueError(f"La anomalía '{feat.id}' posee área degenerada o nula en POSGAR 2007 ({area_m2}).")
    return geom_posgar, area_m2


def _get_col_value(row: Any, candidates: Sequence[str], default: Any = None) -> Any:
    """Obtiene el primer valor no nulo entre posibles nombres de columnas de un padrón catastral."""
    for col in candidates:
        if col in row and row[col] is not None:
            val = row[col]
            if isinstance(val, float) and math.isnan(val):
                continue
            text = str(val).strip()
            if text and text.lower() not in {"nan", "none", "nat"}:
                return val
    return default


def _reproject_gdf_to_posgar(
    gdf: gpd.GeoDataFrame,
    target_epsg: int,
    layer_label: str = "catastro",
) -> Tuple[gpd.GeoDataFrame, List[str]]:
    """Valida CRS/bounds y reproyecta un GeoDataFrame a POSGAR 2007 (`EPSG:{target_epsg}`)."""
    warnings: List[str] = []
    if gdf.empty:
        return gdf, warnings
    work = gdf.copy()
    if work.crs is None:
        minx, miny, maxx, maxy = [float(v) for v in work.total_bounds]
        if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
            raise ValueError(f"La capa de {layer_label} sin CRS contiene coordenadas no finitas.")
        if -180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0 and -90.0 <= miny <= 90.0 and -90.0 <= maxy <= 90.0:
            work = work.set_crs(epsg=4326)
            warnings.append(
                f"La capa de {layer_label} carecía de CRS declarado; se infirió EPSG:4326 por rango de coordenadas."
            )
        elif 1_000_000.0 <= minx <= 8_500_000.0 and 1_000_000.0 <= maxx <= 8_500_000.0 and 3_500_000.0 <= miny <= 8_000_000.0 and 3_500_000.0 <= maxy <= 8_000_000.0:
            work = work.set_crs(epsg=target_epsg)
            warnings.append(
                f"La capa de {layer_label} carecía de CRS declarado; se asumió EPSG:{target_epsg} por rango métrico POSGAR."
            )
        else:
            raise ValueError(
                f"La capa de {layer_label} carece de CRS y sus coordenadas ({minx:.2f}, {miny:.2f}) "
                f"no pertenecen ni a WGS84 ni al dominio métrico POSGAR 2007."
            )
    if work.crs is not None and work.crs.to_epsg() != target_epsg:
        work = work.to_crs(epsg=target_epsg)
    return work, warnings


def _verify_cadastre_metadata_provenance(
    meta: CadastreMetadata,
) -> Tuple[CadastreMetadata, Optional[str]]:
    """Impide presentar una fuente como `OFICIAL_COMPLETO` sin respaldo mínimo de procedencia."""
    if meta.cadastre_status != "OFICIAL_COMPLETO":
        return meta, None

    has_authority = bool(meta.authority_name and meta.authority_name.strip())
    has_source_ref = bool(meta.source_reference and meta.source_reference.strip())
    has_specific_jurisdiction = bool(meta.jurisdiction and meta.jurisdiction.strip() and meta.jurisdiction.strip() != "No especificada")

    # Si no se informa organismo emisor oficial:
    # - cuando tampoco hay jurisdicción específica, o cuando solo figura una ruta local como source_reference,
    #   degradar a PARCIAL_LOCAL para no presentar un archivo local sin procedencia como padrón oficial completo.
    if not has_authority and (not has_specific_jurisdiction or has_source_ref):
        degraded = meta.model_copy(update={"cadastre_status": "PARCIAL_LOCAL"})
        return (
            degraded,
            "El parámetro cadastre_status='OFICIAL_COMPLETO' fue degradado automáticamente a 'PARCIAL_LOCAL' "
            "por falta de evidencia verificable de procedencia oficial (authority_name y respaldo documental).",
        )
    return meta, None


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
        water_posgar, crs_notes = _reproject_gdf_to_posgar(
            watercourses_gdf, anomaly.crs_epsg, layer_label="hidrografía"
        )
        notes.extend(crs_notes)
        best_dist: Optional[float] = None
        best_name: Optional[str] = None
        for _, wrow in water_posgar.iterrows():
            wgeom = wrow.geometry
            if wgeom is None or wgeom.is_empty:
                continue
            wgeom = _repair_geometry(wgeom)
            if wgeom.is_empty or not wgeom.is_valid:
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

    Mantiene estricta prudencia institucional y seguridad semántica:
    - Exige vigencia confirmada y actividad compatible para clasificar `EXTRACCION_AUTORIZADA`.
    - Diferencia vigencia desconocida/vencida y actividad ausente/incompatible mediante
      `PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE` y `PERMISO_VENCIDO`.
    - Evalúa unión geométrica (`unary_union`) ante múltiples concesiones para evitar doble conteo.
    - Degrada `OFICIAL_COMPLETO` a `PARCIAL_LOCAL` si falta respaldo mínimo de procedencia.
    - Exige siempre revisión humana (`human_review_required=True`) y nunca imputa ilegalidad automática.
    """
    feat = coerce_anomaly_feature(anomaly)
    validate_posgar_epsg(feat.crs_epsg)
    t1_iso = _parse_iso_date_prefix(t1_date, label="t1_date")

    geom_posgar, anomaly_area_m2 = _validate_and_build_anomaly_geometry(feat)

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
            if (feat.confidence >= 0.80 and anomaly_area_m2 >= 5000.0)
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
    raw_meta = cadastre_metadata or CadastreMetadata(
        cadastre_status="PARCIAL_LOCAL",
        jurisdiction="No especificada",
        source_reference=str(permits_file),
    )
    meta, provenance_warning = _verify_cadastre_metadata_provenance(raw_meta)

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
        if provenance_warning:
            obs_parts.insert(0, provenance_warning)
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

    permits_posgar, crs_notes = _reproject_gdf_to_posgar(
        permits_gdf, feat.crs_epsg, layer_label="permisos"
    )
    overlapping_entries: List[Tuple[PermitMatchInfo, BaseGeometry, List[str]]] = []
    nearby_entries: List[PermitMatchInfo] = []
    invalid_permit_geom_count = 0

    for idx, prow in permits_posgar.iterrows():
        raw_pgeom = prow.geometry
        if raw_pgeom is None or raw_pgeom.is_empty:
            invalid_permit_geom_count += 1
            continue
        pgeom = _repair_geometry(raw_pgeom)
        if pgeom.is_empty or not pgeom.is_valid:
            invalid_permit_geom_count += 1
            continue

        dist = float(geom_posgar.distance(pgeom))
        inter_area = float(geom_posgar.intersection(pgeom).area)
        if inter_area <= 0.0 and dist > _NEARBY_PERMIT_SEARCH_RADIUS_M:
            continue

        permit_id = str(_get_col_value(prow, ("permit_id", "id", "codigo", "nro_permiso"), default=f"PERM-{idx}"))
        titular = str(_get_col_value(prow, ("titular", "empresa", "razon_social", "holder"), default="No informado"))
        expediente = _get_col_value(prow, ("expediente", "nro_expediente", "file_id"), default=None)
        raw_desde = _get_col_value(prow, ("fecha_desde", "valid_from", "desde", "inicio"), default=None)
        raw_hasta = _get_col_value(prow, ("fecha_hasta", "valid_to", "hasta", "vencimiento"), default=None)
        vigente, validity_status, fecha_desde, fecha_hasta, validity_msg = _evaluate_permit_validity(
            raw_desde, raw_hasta, t1_iso
        )
        raw_activity = _get_col_value(prow, ("permitted_activity", "actividad", "activity"), default=None)
        activity_status, cleaned_activity = _evaluate_permit_activity(raw_activity)

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
            validity_status=validity_status,
            activity_status=activity_status,
            overlap_area_m2=round(inter_area, 2),
            overlap_pct=overlap_pct,
            excess_area_m2=excess_area,
            excess_pct=excess_pct,
            distance_m=0.0 if inter_area > 0.0 else round(dist, 2),
            permitted_activity=cleaned_activity,
            authorized_volume_limit_m3=vol_limit,
        )
        item_notes: List[str] = []
        if validity_msg:
            item_notes.append(f"[{permit_id}] {validity_msg}")
        if activity_status == "SIN_ACTIVIDAD_DECLARADA":
            item_notes.append(f"[{permit_id}] Actividad autorizada no declarada en el padrón.")
        elif activity_status == "INCOMPATIBLE":
            item_notes.append(
                f"[{permit_id}] Actividad declarada ('{cleaned_activity}') incompatible con extracción de áridos."
            )

        if inter_area > 0.0:
            overlapping_entries.append((match_info, pgeom, item_notes))
        else:
            nearby_entries.append(match_info)

    overlapping_entries.sort(key=lambda pair: pair[0].overlap_area_m2, reverse=True)
    nearby_entries.sort(key=lambda info: info.distance_m)
    matched_permits: List[PermitMatchInfo] = [m for m, _, _ in overlapping_entries] + nearby_entries

    obs_parts = []
    if provenance_warning:
        obs_parts.append(provenance_warning)
    obs_parts.extend(crs_notes)
    if invalid_permit_geom_count > 0:
        obs_parts.append(
            f"Se descartaron {invalid_permit_geom_count} geometría(s) de concesión vacías o topológicamente inválidas."
        )

    if overlapping_entries:
        verified_active_compatible = [
            (m, pgeom)
            for m, pgeom, _ in overlapping_entries
            if m.validity_status == "VIGENTE_CONFIRMADO" and m.activity_status == "COMPATIBLE"
        ]
        unverifiable_or_incompatible = [
            (m, notes)
            for m, _, notes in overlapping_entries
            if (m.validity_status != "VENCIDO" and (m.validity_status != "VIGENTE_CONFIRMADO" or m.activity_status != "COMPATIBLE"))
        ]
        expired_only = [m for m, _, _ in overlapping_entries if m.validity_status == "VENCIDO"]

        if verified_active_compatible:
            active_union = unary_union([pgeom for _, pgeom in verified_active_compatible])
            covered_area_m2 = float(geom_posgar.intersection(active_union).area)
            total_excess_area_m2 = round(max(0.0, anomaly_area_m2 - covered_area_m2), 2)
            total_excess_pct = round(max(0.0, (total_excess_area_m2 / anomaly_area_m2) * 100.0), 2)
            if total_excess_pct <= tol_pct:
                classification: PermitClassificationLiteral = "EXTRACCION_AUTORIZADA"
                priority = "BAJA_ARCHIVO"
                obs_parts.append(
                    f"La anomalía se encuentra cubierta en un {100.0 - total_excess_pct:.1f}% mediante unión "
                    f"geométrica de {len(verified_active_compatible)} concesión(es) vigente(s) confirmada(s) y "
                    f"con actividad compatible en T1 ({t1_iso}), dentro de la tolerancia ({tol_pct:.1f}%)."
                )
            else:
                classification = "EXCESO_DE_POLIGONO"
                priority = (
                    "ALTA_FISCALIZACION_INMEDIATA" if total_excess_area_m2 >= 1600.0 else "MEDIA_VERIFICACION"
                )
                obs_parts.append(
                    f"Se detecta superficie fuera de la cobertura autorizada vigente y compatible equivalente a "
                    f"{total_excess_area_m2:.2f} m² ({total_excess_pct:.1f}% > tolerancia {tol_pct:.1f}%). "
                    "Requiere deslinde topográfico y revisión humana."
                )
        elif unverifiable_or_incompatible:
            classification = "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE"
            total_excess_area_m2 = round(anomaly_area_m2, 2)
            total_excess_pct = 100.0
            has_incompatible = any(m.activity_status == "INCOMPATIBLE" for m, _ in unverifiable_or_incompatible)
            priority = "ALTA_FISCALIZACION_INMEDIATA" if has_incompatible else "MEDIA_VERIFICACION"
            detail_notes = [n for _, notes in unverifiable_or_incompatible for n in notes]
            obs_parts.append(
                "Se encontraron concesiones superpuestas pero NO puede confirmarse autorización válida de extracción "
                f"({' '.join(detail_notes)}). No se clasifica como autorizada ni como exceso de polígono; requiere "
                "verificación documental y en terreno."
            )
        else:
            assert expired_only
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
            "La ausencia de coincidencia en este archivo no implica ilegalidad y requiere verificación "
            "administrativa oficial antes de cualquier acción."
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
