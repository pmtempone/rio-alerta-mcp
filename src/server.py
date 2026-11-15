"""Servidor FastMCP principal para RíoAlerta (`rio-alerta-mcp`)."""

import getpass
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, cast

from fastmcp import FastMCP

from src import __version__
from src.config import POSGAR_2007_EPSG, PROJECT_ROOT, settings
from src.core.case_workflow import CaseWorkflowManager
from src.core.change_detector import (
    compute_multitemporal_baseline_and_changes,
    detect_extraction_anomalies,
)
from src.core.crs_manager import get_posgar_faja
from src.core.optical_providers import search_and_fetch_stac_multitemporal
from src.core.parana_pilot_recon import evaluate_parana_pilot_readiness
from src.core.permits_validator import validate_anomaly_permits
from src.core.report_generator import generate_enforcement_report
from src.core.vlm_judge import (
    DEFAULT_PROMPT_VERSION,
    build_vlm_fluvial_prompt,
    evaluate_candidate_vlm_verdict,
)
from src.schemas import (
    CadastreMetadata,
    CadastreStatusLiteral,
    CaseActionTypeLiteral,
    CaseStatusLiteral,
    ChangeDetectionResult,
    EvaluationModeLiteral,
    InspectionPriorityLiteral,
    MultitemporalScenePair,
    PermitClassificationLiteral,
    SpectralChangeResult,
    VLMVerdictLiteral,
    coerce_anomaly_feature,
)

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


_REMOTE_URI_PREFIXES: Tuple[str, ...] = ("http://", "https://", "stac://", "s3://", "gs://")
_RASTER_SUFFIXES: Tuple[str, ...] = (".tif", ".tiff", ".vrt")
_CADASTRE_SUFFIXES: Tuple[str, ...] = (".gpkg", ".geojson", ".json")
_IMAGE_SUFFIXES: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")


def _pytest_session_temp_root() -> Path | None:
    """Retorna la subcarpeta de usuario para pytest únicamente cuando PYTEST_CURRENT_TEST está activa."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path(tempfile.gettempdir()).resolve() / f"pytest-of-{getpass.getuser()}"


def _configured_temp_root() -> Path | None:
    raw = os.environ.get("RIO_ALERTA_TEMP_DIR", "").strip()
    if not raw:
        return None
    candidate = Path(raw).resolve()
    global_tmp = Path(tempfile.gettempdir()).resolve()
    if candidate in {Path("/"), PROJECT_ROOT.resolve(), global_tmp}:
        return None
    return candidate


def _get_allowed_read_roots() -> List[Path]:
    """Raíces autorizadas para operaciones de lectura desde herramientas MCP."""
    roots = [
        settings.data_dir.resolve(),
        settings.processed_dir.resolve(),
        settings.reports_dir.resolve(),
        (PROJECT_ROOT / "data").resolve(),
    ]
    for extra in (_configured_temp_root(), _pytest_session_temp_root()):
        if extra is not None and extra not in roots:
            roots.append(extra)
    return roots


def _get_allowed_write_roots() -> List[Path]:
    """Raíces autorizadas para operaciones de escritura desde herramientas MCP."""
    roots = [
        settings.processed_dir.resolve(),
        settings.reports_dir.resolve(),
    ]
    for extra in (_configured_temp_root(), _pytest_session_temp_root()):
        if extra is not None and extra not in roots:
            roots.append(extra)
    return roots


def _forbidden_write_roots() -> Tuple[Path, ...]:
    """Subárboles explícitamente vedados a escritura incluso ante variables de entorno mal configuradas."""
    return (
        (PROJECT_ROOT / "src").resolve(),
        (PROJECT_ROOT / "tests").resolve(),
        (PROJECT_ROOT / "scripts").resolve(),
        (PROJECT_ROOT / "docs").resolve(),
        (PROJECT_ROOT / ".git").resolve(),
        (PROJECT_ROOT / "data" / "experiments").resolve(),
        (PROJECT_ROOT / "data" / "raw").resolve(),
    )


def _validate_safe_path(
    raw_path: str,
    *,
    allowed_roots: List[Path],
    must_exist: bool,
    expect_dir: bool,
    allowed_suffixes: Tuple[str, ...] | None = None,
    is_write: bool = False,
    missing_prefix: str = "No existe la ruta requerida",
) -> Path:
    """Valida confinamiento estricto en raíces permitidas, rechaza symlinks y verifica tipo de nodo."""
    cleaned = str(raw_path).strip()
    if not cleaned:
        raise ValueError("La ruta proporcionada no puede estar vacía.")
    if cleaned.lower().startswith(_REMOTE_URI_PREFIXES):
        raise ValueError(f"Se esperaba una ruta local del sistema de archivos, no URI remota: '{raw_path}'.")

    raw_p = Path(cleaned)
    if ".." in raw_p.parts:
        raise PermissionError(f"Navegación relativa '..' rechazada en ruta MCP: '{raw_path}'.")
    if raw_p.parts and raw_p.parts[0].startswith("~"):
        raise PermissionError(f"Expansión de directorio home '~' no permitida en ruta MCP: '{raw_path}'.")

    candidate = raw_p if raw_p.is_absolute() else (PROJECT_ROOT / raw_p)
    resolved = candidate.resolve()

    matched_root: Path | None = None
    for root in allowed_roots:
        if resolved == root or resolved.is_relative_to(root):
            matched_root = root
            break
    if matched_root is None:
        raise PermissionError(
            f"Ruta fuera de las raíces permitidas por el servidor MCP: '{raw_path}'."
        )

    if is_write:
        if resolved == PROJECT_ROOT.resolve():
            raise PermissionError(f"Escritura prohibida en la raíz del proyecto: '{raw_path}'.")
        for forbidden in _forbidden_write_roots():
            if resolved == forbidden or resolved.is_relative_to(forbidden):
                raise PermissionError(
                    f"Escritura prohibida en subárbol protegido '{forbidden}': '{raw_path}'."
                )

    # Rechazar symlinks desde el objetivo hacia arriba hasta alcanzar la raíz autorizada
    curr = candidate
    while True:
        if curr.is_symlink():
            raise PermissionError(f"Symlink no permitido en ruta MCP: '{curr}'.")
        if curr == matched_root:
            break
        try:
            if curr.exists() and curr.resolve() == matched_root:
                break
        except OSError:
            pass
        if curr.parent == curr:
            break
        curr = curr.parent

    if not expect_dir and allowed_suffixes is not None:
        if resolved.suffix.lower() not in allowed_suffixes:
            raise ValueError(
                f"Extensión de archivo no permitida ('{resolved.suffix}') para '{raw_path}'. "
                f"Permitidas: {allowed_suffixes}."
            )

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{missing_prefix}: '{raw_path}'")

    if resolved.exists():
        if expect_dir and not resolved.is_dir():
            raise ValueError(f"Se esperaba un directorio pero la ruta apunta a un archivo: '{raw_path}'.")
        if not expect_dir and not resolved.is_file():
            raise ValueError(f"Se esperaba un archivo regular pero la ruta apunta a un directorio: '{raw_path}'.")

    return resolved


def _validate_raster_paths(*paths: str | None) -> List[str | None]:
    """Valida seguridad, extensión y existencia de rutas raster antes de invocar el motor geoespacial."""
    read_roots = _get_allowed_read_roots()
    validated: List[str | None] = []
    for p in paths:
        if p is None:
            validated.append(None)
            continue
        safe = _validate_safe_path(
            p,
            allowed_roots=read_roots,
            must_exist=True,
            expect_dir=False,
            allowed_suffixes=_RASTER_SUFFIXES,
            is_write=False,
            missing_prefix="No existe el archivo raster requerido",
        )
        validated.append(str(safe))
    return validated


@mcp.tool()
def tool_search_multitemporal_pair(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    t1_end_date: str | None = None,
    lookback_years: int = 3,
) -> MultitemporalScenePair:
    """Busca en STAC y recorta en POSGAR 2007 un par multitemporal Sentinel-2 L2A (10m VNIR + 20m SWIR/SCL).

    Cierra el flujo extremo a extremo desde el servidor MCP: dado un bounding box WGS84 y fecha T1,
    descarga las ventanas COG en `data/raw` listas para `tool_detect_extraction_anomalies`.
    """
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("Bounding box inválido: se requiere min_lon < max_lon y min_lat < max_lat.")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0 and -90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise ValueError("Coordenadas WGS84 fuera de rango [-180..180, -90..90].")
    if lookback_years < 1 or lookback_years > 10:
        raise ValueError("lookback_years debe estar entre 1 y 10.")
    if t1_end_date is not None:
        try:
            datetime.fromisoformat(t1_end_date.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"t1_end_date inválido ('{t1_end_date}'): se requiere formato ISO 8601 (ej. 'YYYY-MM-DD')."
            ) from exc
    settings.ensure_directories()
    return search_and_fetch_stac_multitemporal(
        bbox_wgs84=(float(min_lon), float(min_lat), float(max_lon), float(max_lat)),
        t1_end_date=t1_end_date,
        lookback_years=int(lookback_years),
        out_dir=settings.data_dir,
    )


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
    safe_t0, safe_t1, safe_t0_swir, safe_t1_swir = _validate_raster_paths(
        t0_path, t1_path, t0_swir_20m_path, t1_swir_20m_path
    )
    assert safe_t0 is not None and safe_t1 is not None
    settings.ensure_directories()
    return compute_multitemporal_baseline_and_changes(
        t0_vnir_paths=safe_t0,
        t1_vnir_path=safe_t1,
        out_dir=settings.processed_dir,
        t0_swir_scl_paths=safe_t0_swir,
        t1_swir_scl_path=safe_t1_swir,
    )


@mcp.tool()
def tool_detect_extraction_anomalies(
    t0_path: str,
    t1_path: str,
    t0_swir_20m_path: str | None = None,
    t1_swir_20m_path: str | None = None,
    min_area_m2: float = 1600.0,
    min_clearing_area_m2: float = 800.0,
    stage_t0_m: float | None = None,
    stage_t1_m: float | None = None,
    max_discarded_records: int | None = None,
) -> ChangeDetectionResult:
    """Detecta polígonos de anomalías morfológicas candidatas filtrando cambios fluviales naturales.

    Aplica controles de crecida/bajante uniforme, dipolos de migración natural de bancos de arena,
    fenología ribereña y máscara de nubes/sombras SCL, emitiendo métricas de incertidumbre.
    """
    if min_area_m2 <= 0:
        raise ValueError("min_area_m2 debe ser mayor que 0.")
    if min_clearing_area_m2 <= 0:
        raise ValueError("min_clearing_area_m2 debe ser mayor que 0.")
    if max_discarded_records is not None and max_discarded_records < 1:
        raise ValueError("max_discarded_records debe ser mayor o igual a 1.")
    safe_t0, safe_t1, safe_t0_swir, safe_t1_swir = _validate_raster_paths(
        t0_path, t1_path, t0_swir_20m_path, t1_swir_20m_path
    )
    assert safe_t0 is not None and safe_t1 is not None
    settings.ensure_directories()
    return detect_extraction_anomalies(
        t0_vnir_paths=safe_t0,
        t1_vnir_path=safe_t1,
        t0_swir_scl_paths=safe_t0_swir,
        t1_swir_scl_path=safe_t1_swir,
        min_area_m2=min_area_m2,
        min_clearing_area_m2=min_clearing_area_m2,
        work_dir=settings.processed_dir,
        stage_t0_m=stage_t0_m,
        stage_t1_m=stage_t1_m,
        max_discarded_records=max_discarded_records,
    )


@mcp.tool()
def tool_evaluate_parana_pilot(offline_mode: bool = True) -> Dict[str, Any]:
    """Evalúa factibilidad geodésica, STAC e institucional del tramo piloto Río Paraná Medio."""
    return evaluate_parana_pilot_readiness(offline_mode=offline_mode)


_VALID_CADASTRE_STATUSES = {"OFICIAL_COMPLETO", "PARCIAL_LOCAL", "NO_DISPONIBLE"}
_VALID_EVALUATION_MODES = {"HEURISTIC_SCREENING", "EXTERNAL_VLM", "HUMAN_EXPERT"}
_VALID_VLM_VERDICTS = {
    "EXTRACCION_ANTROPICA",
    "DINAMICA_FLUVIAL_NATURAL",
    "ARTEFACTO_NUBES_SOMBRAS",
    "PENDIENTE_REVISION",
}
_VALID_CASE_ACTIONS = {
    "APERTURA_CASO",
    "NOTA_ANALISTA",
    "ACTUALIZACION_CATASTRAL",
    "DICTAMEN_VLM",
    "SOLICITUD_INSPECCION",
    "RESULTADO_CAMPO",
    "CAMBIO_ESTADO",
}
_VALID_CASE_STATUSES = {
    "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
    "SIN_CATASTRO_OFICIAL_CARGADO",
    "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL",
    "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS",
    "EN_REVISION_GABINETE",
    "INSPECCION_SOLICITADA",
    "INSPECCIONADO_SIN_PERMISO_REGISTRADO",
    "EXTRACCION_AUTORIZADA",
    "EXCESO_DE_POLIGONO_EN_VERIFICACION",
    "DESCARTADO_DINAMICA_NATURAL",
    "ARCHIVADO_FALSO_POSITIVO",
}
_VALID_PRIORITIES = {
    "ALTA_FISCALIZACION_INMEDIATA",
    "MEDIA_VERIFICACION",
    "BAJA_ARCHIVO",
}

_PERMIT_CLASS_TO_CASE_STATUS: Dict[PermitClassificationLiteral, CaseStatusLiteral] = {
    "SIN_CATASTRO_OFICIAL_CARGADO": "SIN_CATASTRO_OFICIAL_CARGADO",
    "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL": "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL",
    "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS": "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS",
    "EXTRACCION_AUTORIZADA": "EXTRACCION_AUTORIZADA",
    "EXCESO_DE_POLIGONO": "EXCESO_DE_POLIGONO_EN_VERIFICACION",
    "PERMISO_VENCIDO": "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
    "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE": "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
    "EXTRACCION_SIN_PERMISO": "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
    "INSPECCIONADO_SIN_PERMISO_REGISTRADO": "INSPECCIONADO_SIN_PERMISO_REGISTRADO",
}


def _validate_iso_date(value: str, field_name: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field_name} inválido ('{value}'): se requiere formato ISO 8601 (ej. 'YYYY-MM-DD')."
        ) from exc


@mcp.tool()
def tool_validate_permits(
    anomaly_dict: Dict[str, Any],
    t1_date: str,
    permits_gpkg_path: str | None = None,
    cadastre_status: str = "NO_DISPONIBLE",
    jurisdiction: str = "No especificada",
    authority_name: str | None = None,
    excess_tolerance_pct: float | None = None,
    query_remote_hydrography: bool = False,
    source_reference: str | None = None,
    last_updated: str | None = None,
) -> Dict[str, Any]:
    """Cruza espacial y temporalmente un candidato contra el catastro de permisos e hidrografía."""
    _validate_iso_date(t1_date, "t1_date")
    if cadastre_status not in _VALID_CADASTRE_STATUSES:
        raise ValueError(
            f"cadastre_status inválido '{cadastre_status}'. Válidos: {sorted(_VALID_CADASTRE_STATUSES)}."
        )
    if excess_tolerance_pct is not None and excess_tolerance_pct < 0.0:
        raise ValueError("excess_tolerance_pct debe ser >= 0.")
    if last_updated is not None:
        _validate_iso_date(last_updated, "last_updated")

    validated_permits: Path | None = None
    if permits_gpkg_path is not None:
        validated_permits = _validate_safe_path(
            permits_gpkg_path,
            allowed_roots=_get_allowed_read_roots(),
            must_exist=False,
            expect_dir=False,
            allowed_suffixes=_CADASTRE_SUFFIXES,
            is_write=False,
        )
    elif settings.permits_gpkg_path.is_symlink():
        raise PermissionError(
            f"Symlink no permitido en ruta de catastro por defecto: '{settings.permits_gpkg_path}'."
        )

    meta = CadastreMetadata(
        cadastre_status=cast(CadastreStatusLiteral, cadastre_status),
        jurisdiction=jurisdiction,
        authority_name=authority_name,
        source_reference=source_reference
        or (str(validated_permits) if validated_permits is not None else str(settings.permits_gpkg_path)),
        last_updated=last_updated,
    )
    result = validate_anomaly_permits(
        anomaly=anomaly_dict,
        t1_date=t1_date,
        permits_path=validated_permits,
        cadastre_metadata=meta,
        excess_tolerance_pct=excess_tolerance_pct,
        query_remote_hydrography=query_remote_hydrography,
    )
    return result.model_dump()


@mcp.tool()
def tool_evaluate_vlm_verdict(
    anomaly_dict: Dict[str, Any],
    verdict: str | None = None,
    certainty_pct: float | None = None,
    extraction_evidence: str | None = None,
    fluvial_dynamics_assessment: str | None = None,
    backend: str | None = None,
    t0_date: str | None = None,
    t1_date: str | None = None,
    evaluation_mode: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    image_refs: List[str] | None = None,
    evaluator_id: str | None = None,
) -> Dict[str, Any]:
    """Emite o registra un dictamen auxiliar multimodal/heurístico/experto con separación de modalidades."""
    typed_mode: EvaluationModeLiteral | None = None
    if evaluation_mode is not None:
        if evaluation_mode not in _VALID_EVALUATION_MODES:
            raise ValueError(
                f"evaluation_mode inválido '{evaluation_mode}'. Válidos: {sorted(_VALID_EVALUATION_MODES)}."
            )
        typed_mode = cast(EvaluationModeLiteral, evaluation_mode)

    typed_verdict: VLMVerdictLiteral | None = None
    if verdict is not None:
        if verdict not in _VALID_VLM_VERDICTS:
            raise ValueError(f"verdict inválido '{verdict}'. Válidos: {sorted(_VALID_VLM_VERDICTS)}.")
        typed_verdict = cast(VLMVerdictLiteral, verdict)
    if certainty_pct is not None and not (0.0 <= certainty_pct <= 100.0):
        raise ValueError("certainty_pct debe estar entre 0 y 100.")

    safe_image_refs: List[str] | None = None
    if image_refs is not None:
        read_roots = _get_allowed_read_roots()
        safe_image_refs = []
        for raw_ref in image_refs:
            ref = str(raw_ref).strip()
            if not ref:
                continue
            if ref.lower().startswith(_REMOTE_URI_PREFIXES):
                safe_image_refs.append(ref)
            else:
                safe_img = _validate_safe_path(
                    ref,
                    allowed_roots=read_roots,
                    must_exist=True,
                    expect_dir=False,
                    allowed_suffixes=_IMAGE_SUFFIXES,
                    is_write=False,
                    missing_prefix="La imagen de evaluación referenciada no existe en disco",
                )
                safe_image_refs.append(str(safe_img))

    feat = coerce_anomaly_feature(anomaly_dict)
    resolved_prompt_ver = prompt_version or DEFAULT_PROMPT_VERSION
    vlm_res = evaluate_candidate_vlm_verdict(
        anomaly=feat,
        verdict=typed_verdict,
        certainty_pct=certainty_pct,
        extraction_evidence=extraction_evidence,
        fluvial_dynamics_assessment=fluvial_dynamics_assessment,
        backend=backend,
        evaluation_mode=typed_mode,
        model_id=model_id,
        model_version=model_version,
        prompt_version=resolved_prompt_ver,
        image_refs=safe_image_refs,
        evaluator_id=evaluator_id,
    )
    suggested_prompt = build_vlm_fluvial_prompt(
        anomaly=feat,
        t0_date=t0_date or "T0",
        t1_date=t1_date or "T1",
        prompt_version=resolved_prompt_ver,
    )
    out = vlm_res.model_dump()
    out["suggested_prompt"] = suggested_prompt
    return out


@mcp.tool()
def tool_generate_enforcement_report(
    anomaly_dict: Dict[str, Any],
    t0_scene_id: str,
    t0_date: str,
    t1_scene_id: str,
    t1_date: str,
    study_area_name: str = "Tramo Fluvial Monitoreado",
    permits_gpkg_path: str | None = None,
    cadastre_status: str = "NO_DISPONIBLE",
    jurisdiction: str = "No especificada",
    multitemporal_series: List[Dict[str, Any]] | None = None,
    output_dir: str | None = None,
    open_case_workflow: bool = True,
    case_storage_dir: str | None = None,
    authority_name: str | None = None,
    source_reference: str | None = None,
    strict_hydrometric_validation: bool = False,
) -> Dict[str, Any]:
    """Genera el dossier Markdown de 9 secciones y el expediente JSON reproducible en generación atómica."""
    _validate_iso_date(t0_date, "t0_date")
    _validate_iso_date(t1_date, "t1_date")
    if cadastre_status not in _VALID_CADASTRE_STATUSES:
        raise ValueError(
            f"cadastre_status inválido '{cadastre_status}'. Válidos: {sorted(_VALID_CADASTRE_STATUSES)}."
        )

    validated_permits: Path | None = None
    if permits_gpkg_path is not None:
        validated_permits = _validate_safe_path(
            permits_gpkg_path,
            allowed_roots=_get_allowed_read_roots(),
            must_exist=False,
            expect_dir=False,
            allowed_suffixes=_CADASTRE_SUFFIXES,
            is_write=False,
        )
    elif settings.permits_gpkg_path.is_symlink():
        raise PermissionError(
            f"Symlink no permitido en ruta de catastro por defecto: '{settings.permits_gpkg_path}'."
        )

    write_roots = _get_allowed_write_roots()
    validated_output_dir: Path | None = (
        _validate_safe_path(
            output_dir,
            allowed_roots=write_roots,
            must_exist=False,
            expect_dir=True,
            allowed_suffixes=None,
            is_write=True,
        )
        if output_dir is not None
        else None
    )
    validated_case_storage_dir: Path | None = (
        _validate_safe_path(
            case_storage_dir,
            allowed_roots=write_roots,
            must_exist=False,
            expect_dir=True,
            allowed_suffixes=None,
            is_write=True,
        )
        if case_storage_dir is not None
        else None
    )

    settings.ensure_directories()
    feat = coerce_anomaly_feature(anomaly_dict)
    meta = CadastreMetadata(
        cadastre_status=cast(CadastreStatusLiteral, cadastre_status),
        jurisdiction=jurisdiction,
        authority_name=authority_name,
        source_reference=source_reference
        or (str(validated_permits) if validated_permits is not None else str(settings.permits_gpkg_path)),
    )
    permit_res = validate_anomaly_permits(
        anomaly=feat,
        t1_date=t1_date,
        permits_path=validated_permits,
        cadastre_metadata=meta,
    )
    vlm_res = evaluate_candidate_vlm_verdict(anomaly=feat, permit_result=permit_res)

    case_rec = None
    if open_case_workflow:
        manager = CaseWorkflowManager(storage_dir=validated_case_storage_dir)
        initial_status = _PERMIT_CLASS_TO_CASE_STATUS[permit_res.classification]
        case_rec = manager.open_or_get_case(
            anomaly=feat,
            actor="tool_generate_enforcement_report",
            summary=f"Expediente generado ({permit_res.classification}; {vlm_res.verdict}).",
            initial_status=initial_status,
            priority=permit_res.priority,
            metadata={
                "t0_scene_id": t0_scene_id,
                "t1_scene_id": t1_scene_id,
                "cadastre_classification": permit_res.classification,
            },
        )

    report = generate_enforcement_report(
        anomaly=feat,
        t0_scene_id=t0_scene_id,
        t0_date=t0_date,
        t1_scene_id=t1_scene_id,
        t1_date=t1_date,
        permit_validation=permit_res,
        vlm_verdict=vlm_res,
        case_record=case_rec,
        multitemporal_series=multitemporal_series,
        study_area_name=study_area_name,
        output_dir=validated_output_dir,
        strict_hydrometric_validation=strict_hydrometric_validation,
        permits_path=validated_permits,
        cadastre_metadata=meta,
    )
    return report.model_dump()


@mcp.tool()
def tool_manage_case_workflow(
    action: str,
    anomaly_dict: Dict[str, Any] | None = None,
    case_id: str | None = None,
    action_type: str | None = None,
    summary: str | None = None,
    actor: str = "operador",
    new_status: str | None = None,
    priority: str | None = None,
    metadata: Dict[str, Any] | None = None,
    status_filter: str | None = None,
    storage_dir: str | None = None,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    """Gestiona expedientes de investigación territorial con SQLite transaccional y bitácora JSONL."""
    validated_storage_dir: Path | None = (
        _validate_safe_path(
            storage_dir,
            allowed_roots=_get_allowed_write_roots(),
            must_exist=False,
            expect_dir=True,
            allowed_suffixes=None,
            is_write=True,
        )
        if storage_dir is not None
        else None
    )
    settings.ensure_directories()
    manager = CaseWorkflowManager(storage_dir=validated_storage_dir)

    if action == "open":
        if anomaly_dict is None:
            raise ValueError("action='open' requiere anomaly_dict.")
        typed_status: CaseStatusLiteral = "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO"
        if new_status is not None:
            if new_status not in _VALID_CASE_STATUSES:
                raise ValueError(f"new_status inválido '{new_status}'.")
            typed_status = cast(CaseStatusLiteral, new_status)
        typed_priority: InspectionPriorityLiteral = "MEDIA_VERIFICACION"
        if priority is not None:
            if priority not in _VALID_PRIORITIES:
                raise ValueError(f"priority inválido '{priority}'.")
            typed_priority = cast(InspectionPriorityLiteral, priority)
        rec = manager.open_or_get_case(
            anomaly=anomaly_dict,
            actor=actor,
            summary=summary or "Apertura inicial de expediente de monitoreo.",
            initial_status=typed_status,
            priority=typed_priority,
            metadata=metadata,
        )
        return rec.model_dump()

    if action == "append_event":
        if not case_id or not action_type or not summary:
            raise ValueError("action='append_event' requiere case_id, action_type y summary.")
        if action_type not in _VALID_CASE_ACTIONS:
            raise ValueError(f"action_type inválido '{action_type}'. Válidos: {sorted(_VALID_CASE_ACTIONS)}.")
        typed_new_status: CaseStatusLiteral | None = None
        if new_status is not None:
            if new_status not in _VALID_CASE_STATUSES:
                raise ValueError(f"new_status inválido '{new_status}'.")
            typed_new_status = cast(CaseStatusLiteral, new_status)
        typed_new_priority: InspectionPriorityLiteral | None = None
        if priority is not None:
            if priority not in _VALID_PRIORITIES:
                raise ValueError(f"priority inválido '{priority}'.")
            typed_new_priority = cast(InspectionPriorityLiteral, priority)
        rec = manager.append_event(
            case_id=case_id,
            action_type=cast(CaseActionTypeLiteral, action_type),
            summary=summary,
            actor=actor,
            new_status=typed_new_status,
            priority=typed_new_priority,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
        return rec.model_dump()

    if action == "get":
        if not case_id:
            raise ValueError("action='get' requiere case_id.")
        rec, events = manager.get_case(case_id)
        return {
            "case": rec.model_dump(),
            "events": [evt.model_dump() for evt in events],
        }

    if action == "list":
        typed_filter: CaseStatusLiteral | None = None
        if status_filter is not None:
            if status_filter not in _VALID_CASE_STATUSES:
                raise ValueError(f"status_filter inválido '{status_filter}'.")
            typed_filter = cast(CaseStatusLiteral, status_filter)
        cases = manager.list_cases(status=typed_filter)
        return {
            "count": len(cases),
            "cases": [c.model_dump() for c in cases],
        }

    raise ValueError("action inválido. Valores permitidos: 'open', 'append_event', 'get', 'list'.")


if __name__ == "__main__":
    mcp.run()
