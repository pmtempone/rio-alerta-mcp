"""Modelos Pydantic v2 estrictos de entrada y salida para las herramientas MCP de RíoAlerta."""

from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

ChangeAnomalyTypeLiteral = Literal[
    "frente_excavacion_inundado",
    "desmonte_ribera_acopio",
    "apertura_camino_acceso",
    "alteracion_cauce_barra",
    "complejo_extractivo",
]

NaturalFilterStatusLiteral = Literal[
    "CANDIDATO_RESIDUAL",
    "DESCARTADO_PULSO_HIDROMETRICO",
    "DESCARTADO_MIGRACION_BARRA",
    "DESCARTADO_ESTACIONAL",
    "DESCARTADO_NUBE_SOMBRA",
    "DESCARTADO_CONFUSOR_TURBIDEZ",
    "DESCARTADO_COMPUERTA_MINERAL",
    "DESCARTADO_AREA_MINIMA",
    "DESCARTADO_GEOMETRIA_DEGENERADA",
]

CadastreStatusLiteral = Literal[
    "OFICIAL_COMPLETO",
    "PARCIAL_LOCAL",
    "NO_DISPONIBLE",
]

PermitValidityStatusLiteral = Literal[
    "VIGENTE_CONFIRMADO",
    "VENCIDO",
    "NO_VIGENTE_AUN",
    "VIGENCIA_DESCONOCIDA",
]

PermitActivityStatusLiteral = Literal[
    "COMPATIBLE",
    "INCOMPATIBLE",
    "SIN_ACTIVIDAD_DECLARADA",
]

PermitClassificationLiteral = Literal[
    "EXTRACCION_AUTORIZADA",
    "EXCESO_DE_POLIGONO",
    "PERMISO_VENCIDO",
    "PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE",
    "EXTRACCION_SIN_PERMISO",
    "SIN_CATASTRO_OFICIAL_CARGADO",
    "SIN_COINCIDENCIA_EN_CATASTRO_LOCAL",
    "SIN_COINCIDENCIA_EN_REGISTROS_CONSULTADOS",
    "INSPECCIONADO_SIN_PERMISO_REGISTRADO",
]

EvaluationModeLiteral = Literal[
    "HEURISTIC_SCREENING",
    "EXTERNAL_VLM",
    "HUMAN_EXPERT",
]

GeomorphologicalZoneLiteral = Literal[
    "CAUCE_ACTIVO_PERMANENTE",
    "BARRAS_Y_BANCOS_MOVILES",
    "PLANICIE_INUNDABLE_E_ISLAS",
    "RIBERA_FIRME_Y_TERRAZA",
    "NO_CLASIFICADA",
]

VLMVerdictLiteral = Literal[
    "EXTRACCION_ANTROPICA",
    "DINAMICA_FLUVIAL_NATURAL",
    "ARTEFACTO_NUBES_SOMBRAS",
    "PENDIENTE_REVISION",
]

InspectionPriorityLiteral = Literal[
    "ALTA_FISCALIZACION_INMEDIATA",
    "MEDIA_VERIFICACION",
    "BAJA_ARCHIVO",
]


class UncertaintyMetadata(BaseModel):
    """Registro explícito de incertidumbre física, nubosidad y confusores naturales por anomalía."""

    model_config = ConfigDict(extra="forbid")

    effective_resolution_m: float = Field(
        ..., gt=0.0, description="Resolución física efectiva del índice limitante (ej. 20.0 m para SWIR, 10.0 m VNIR)."
    )
    valid_pixel_fraction: float = Field(
        ..., ge=0.0, le=1.0, description="Fracción de píxeles libres de nubes/sombras (SCL) dentro del polígono."
    )
    cloud_shadow_adjacent: bool = Field(
        default=False, description="Indica presencia de nubes/sombras SCL a <= 40 m del contorno detectado."
    )
    water_stage_delta_pct: float = Field(
        default=0.0, description="Variación porcentual global del área de lámina de agua en el tramo (T0 -> T1)."
    )
    natural_dynamics_confounder_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Puntaje [0-1] de riesgo de confusión por dinámica fluvial natural o estacionalidad.",
    )
    persistence_scenes_count: int = Field(
        default=1, ge=1, description="Cantidad de escenas de línea base histórica evaluadas."
    )
    limitations: List[str] = Field(
        default_factory=list, description="Advertencias metodológicas o limitaciones detectadas en el análisis."
    )


class CadastreMetadata(BaseModel):
    """Metadatos institucionales de fuente y completitud del catastro de permisos consultado."""

    model_config = ConfigDict(extra="forbid")

    cadastre_status: CadastreStatusLiteral = Field(
        ..., description="Grado de completitud del registro catastral: OFICIAL_COMPLETO, PARCIAL_LOCAL o NO_DISPONIBLE."
    )
    jurisdiction: str = Field(default="No especificada", description="Provincia, municipio o cuenca del padrón.")
    authority_name: Optional[str] = Field(
        default=None, description="Organismo emisor del registro oficial de concesiones."
    )
    source_reference: Optional[str] = Field(
        default=None, description="Expediente, URL o referencia oficial del catastro cargado."
    )
    last_updated: Optional[str] = Field(
        default=None, description="Fecha ISO de última actualización del padrón de permisos."
    )


class SceneMetadata(BaseModel):
    """Metadatos trazables de una escena satelital (Sentinel-2 L2A u otro sensor)."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(..., description="Identificador único del producto STAC / escena satelital.")
    provider: str = Field(..., description="Proveedor de la escena (ej. 'sentinel2_stac', 'planetary_stac').")
    acquisition_date: str = Field(..., description="Fecha ISO de adquisición de la escena (YYYY-MM-DD).")
    cloud_cover_pct: float = Field(..., ge=0.0, le=100.0, description="Porcentaje de cobertura nubosa reportado.")
    crs_epsg: int = Field(..., description="Código EPSG métrico de trabajo (POSGAR 2007).")
    resolution_m: float = Field(..., gt=0.0, description="Resolución espacial efectiva en metros.")
    bands_available: List[str] = Field(
        default_factory=list,
        description="Lista de bandas espectrales leídas (ej. ['B02', 'B03', 'B04', 'B08', 'B11', 'B12', 'SCL']).",
    )
    stac_collection: str = Field(
        default="sentinel-2-l2a",
        description="Colección STAC de origen de la escena.",
    )


class MultitemporalScenePair(BaseModel):
    """Par multitemporal co-registrado (línea base histórica T0 vs escena actual T1)."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="Estado de la descarga/recorte (ej. 'success').")
    t0_scene: SceneMetadata = Field(..., description="Metadatos de la escena histórica base (T0).")
    t1_scene: SceneMetadata = Field(..., description="Metadatos de la escena actual evaluada (T1).")
    t0_raster_path: str = Field(..., description="Ruta al GeoTIFF multibanda recortado de T0 en POSGAR 2007.")
    t1_raster_path: str = Field(..., description="Ruta al GeoTIFF multibanda recortado de T1 en POSGAR 2007.")
    t0_swir_raster_path: Optional[str] = Field(
        default=None, description="Ruta al GeoTIFF nativo 20 m (B11, B12, SCL) recortado de T0 en POSGAR 2007."
    )
    t1_swir_raster_path: Optional[str] = Field(
        default=None, description="Ruta al GeoTIFF nativo 20 m (B11, B12, SCL) recortado de T1 en POSGAR 2007."
    )
    crs_epsg: int = Field(..., description="Código EPSG métrico común (POSGAR 2007).")
    bounds_posgar: Tuple[float, float, float, float] = Field(
        ..., description="Bounding box métrico común (minx, miny, maxx, maxy)."
    )
    resolution_m: float = Field(..., gt=0.0, description="Resolución espacial común en metros.")
    rgb_t0_chip_path: Optional[str] = Field(
        default=None, description="Ruta opcional al chip RGB PNG de referencia para T0."
    )
    rgb_t1_chip_path: Optional[str] = Field(
        default=None, description="Ruta opcional al chip RGB PNG de referencia para T1."
    )


class SpectralChangeResult(BaseModel):
    """Resultado del cálculo de diferencias espectrales multitemporales T0 -> T1."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="Estado del procesamiento espectral (ej. 'success').")
    delta_mndwi_path: str = Field(..., description="Ruta al GeoTIFF Float32 de ΔMNDWI (MNDWI_T1 - MNDWI_T0).")
    delta_bsi_path: str = Field(..., description="Ruta al GeoTIFF Float32 de ΔBSI (BSI_T1 - BSI_T0).")
    delta_ndvi_path: str = Field(..., description="Ruta al GeoTIFF Float32 de ΔNDVI (NDVI_T1 - NDVI_T0).")
    crs_epsg: int = Field(..., description="Código EPSG métrico POSGAR 2007.")
    resolution_m: float = Field(..., gt=0.0, description="Resolución espacial de grilla en metros.")
    effective_resolution_swir_m: float = Field(
        default=20.0, gt=0.0, description="Resolución física nativa de las bandas SWIR (B11/B12) en metros."
    )
    valid_pixel_fraction: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Fracción de píxeles válidos tras aplicar máscara SCL en T0 y T1."
    )
    bounds_posgar: Tuple[float, float, float, float] = Field(
        ..., description="Extensión métrica (minx, miny, maxx, maxy)."
    )
    stats: Dict[str, float] = Field(
        default_factory=dict,
        description="Estadísticas descriptivas (min, max, mean, std y métricas hidrológicas) del tramo.",
    )


class PolygonSpectralStats(BaseModel):
    """Estadísticas espectrales y geométricas por polígono diferenciando medianas por fecha y medias de delta."""

    model_config = ConfigDict(extra="forbid")

    area_method: str = Field(
        default="shapely_polygon_posgar_2007",
        description="Método exacto de cálculo de superficie (geometría vectorial POSGAR 2007).",
    )
    pixel_count_10m: int = Field(..., ge=0, description="Cantidad de píxeles en grilla de 10 m dentro del polígono.")
    effective_swir_pixels_20m: float = Field(
        ..., ge=0.0, description="Equivalente aproximado en píxeles nativos SWIR de 20 m (pixel_count_10m / 4)."
    )
    t0_b11_median: Optional[float] = Field(default=None, description="Mediana de B11 (DN) en T0 dentro del polígono.")
    t1_b11_median: Optional[float] = Field(default=None, description="Mediana de B11 (DN) en T1 dentro del polígono.")
    t1_b04_median: Optional[float] = Field(default=None, description="Mediana de B04 (DN) en T1 dentro del polígono.")
    t0_ndvi_median: Optional[float] = Field(default=None, description="Mediana de NDVI en T0 dentro del polígono.")
    t1_ndvi_median: Optional[float] = Field(default=None, description="Mediana de NDVI en T1 dentro del polígono.")
    t0_bsi_median: Optional[float] = Field(default=None, description="Mediana de BSI estándar en T0 dentro del polígono.")
    t1_bsi_median: Optional[float] = Field(default=None, description="Mediana de BSI estándar en T1 dentro del polígono.")
    t1_mndwi_median: Optional[float] = Field(default=None, description="Mediana de MNDWI en T1 dentro del polígono.")
    t1_scl_counts: Dict[str, int] = Field(
        default_factory=dict, description="Distribución de clases SCL en T1 dentro del polígono."
    )


class DiscardedAnomalyRecord(BaseModel):
    """Registro trazable de un candidato preliminar descartado por controles naturales o de máscara."""

    model_config = ConfigDict(extra="forbid")

    candidate_uid: str = Field(..., description="Identificador determinista del componente descartado.")
    anomaly_type: ChangeAnomalyTypeLiteral = Field(..., description="Tipología preliminar evaluada.")
    filter_status: NaturalFilterStatusLiteral = Field(..., description="Motivo tipado del descarte.")
    area_m2: float = Field(..., gt=0.0, description="Superficie métrica aproximada en POSGAR 2007 (m²).")
    centroid_posgar: Tuple[float, float] = Field(..., description="Centroide (X, Y) en POSGAR 2007.")
    centroid_wgs84: Tuple[float, float] = Field(..., description="Centroide (lon, lat) en WGS84.")
    reason_detail: str = Field(..., description="Explicación auditable de la regla que descartó el polígono.")


class ExtractionAnomalyFeature(BaseModel):
    """Representación tipada de una anomalía morfológica candidata detectada entre T0 y T1."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Identificador único del polígono de anomalía (ej. 'RIO-0001').")
    candidate_uid: Optional[str] = Field(
        default=None,
        description="Identificador determinista estable entre corridas derivado de CRS, centroide POSGAR redondeado a 10 m y tipo.",
    )
    coarse_location_uid: Optional[str] = Field(
        default=None,
        description="Identificador espacial en grilla gruesa de 100 m para seguimiento longitudinal entre épocas aunque el frente se expanda.",
    )
    anomaly_type: ChangeAnomalyTypeLiteral = Field(
        ..., description="Tipología espectral/morfológica de la alteración fluvial o ribereña."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Puntaje compuesto de señal morfo-espectral [0.0 - 1.0] (no calibrado como probabilidad estadística; preservado por compatibilidad).",
    )
    signal_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Puntaje compuesto de señal morfo-espectral [0.0 - 1.0] (no calibrado como probabilidad estadística; alias semántico de confidence).",
    )
    area_m2: float = Field(..., gt=0.0, description="Superficie plana en metros cuadrados medida en POSGAR 2007.")
    area_ha: float = Field(..., gt=0.0, description="Superficie plana en hectáreas medida en POSGAR 2007.")
    rectangularity: float = Field(..., ge=0.0, le=1.0, description="Índice de rectangularidad de la geometría.")
    elongation: float = Field(..., ge=1.0, description="Relación de elongación (lado mayor / lado menor).")
    delta_mndwi_mean: float = Field(..., description="Cambio medio de MNDWI dentro del polígono (T1 - T0).")
    delta_bsi_mean: float = Field(..., description="Cambio medio de BSI dentro del polígono (T1 - T0).")
    delta_ndvi_mean: float = Field(..., description="Cambio medio de NDVI dentro del polígono (T1 - T0).")
    bbox_posgar: Tuple[float, float, float, float] = Field(
        ..., description="Bounding box métrico (minx, miny, maxx, maxy) en POSGAR 2007."
    )
    centroid_posgar: Tuple[float, float] = Field(
        ..., description="Coordenadas del centroide (X, Y) en POSGAR 2007."
    )
    centroid_wgs84: Tuple[float, float] = Field(
        ..., description="Coordenadas del centroide (lon, lat) en WGS84."
    )
    crs_epsg: int = Field(..., description="Código EPSG POSGAR 2007 utilizado para el cálculo métrico.")
    geometry_geojson: Dict[str, Any] = Field(
        ..., description="Geometría GeoJSON del polígono reproyectada a EPSG:4326."
    )
    effective_resolution_m: float = Field(
        default=20.0, gt=0.0, description="Resolución física nativa limitante (m) de la detección."
    )
    natural_filter_status: NaturalFilterStatusLiteral = Field(
        default="CANDIDATO_RESIDUAL",
        description="Resultado de los filtros de dinámica fluvial natural y máscara SCL.",
    )
    geomorphological_zone: GeomorphologicalZoneLiteral = Field(
        default="NO_CLASIFICADA",
        description="Unidad geomorfológica asignada en T0 (cauce, barra móvil, planicie/islas o ribera firme).",
    )
    distance_to_water_t0_m: Optional[float] = Field(
        default=None, ge=0.0, description="Distancia métrica mínima del polígono a la lámina de agua T0 (m)."
    )
    spectral_stats: Optional[PolygonSpectralStats] = Field(
        default=None,
        description="Desglose auditable de medianas por fecha, conteo SCL y píxeles efectivos.",
    )
    uncertainty: Optional[UncertaintyMetadata] = Field(
        default=None, description="Métricas cuantitativas de incertidumbre y limitaciones de la anomalía."
    )


class ChangeDetectionResult(BaseModel):
    """FeatureCollection GeoJSON tipada con las anomalías extractivas detectadas entre T0 y T1."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["FeatureCollection"] = Field(default="FeatureCollection", description="Tipo GeoJSON.")
    crs: Dict[str, Any] = Field(..., description="Definición del sistema de referencia espacial (CRS84).")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metadatos cuantitativos de la detección (escenas T0/T1, controles hidrológicos, conteos).",
    )
    features: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Lista de features GeoJSON de anomalías detectadas.",
    )


class PermitMatchInfo(BaseModel):
    """Detalle de una concesión o permiso de extracción de áridos intersectado o cercano."""

    model_config = ConfigDict(extra="forbid")

    permit_id: str = Field(..., description="Identificador catastral del permiso o concesión.")
    titular: str = Field(..., description="Titular o razón social del permiso de extracción.")
    expediente: Optional[str] = Field(default=None, description="Número de expediente administrativo.")
    fecha_desde: Optional[str] = Field(default=None, description="Fecha de inicio de vigencia (YYYY-MM-DD).")
    fecha_hasta: Optional[str] = Field(default=None, description="Fecha de vencimiento de vigencia (YYYY-MM-DD).")
    vigente_en_t1: bool = Field(
        default=False,
        description="Indica si el permiso tenía vigencia confirmada e inequívoca en la fecha T1.",
    )
    validity_status: PermitValidityStatusLiteral = Field(
        default="VIGENCIA_DESCONOCIDA",
        description="Estado de vigencia temporal: VIGENTE_CONFIRMADO, VENCIDO, NO_VIGENTE_AUN o VIGENCIA_DESCONOCIDA.",
    )
    activity_status: PermitActivityStatusLiteral = Field(
        default="SIN_ACTIVIDAD_DECLARADA",
        description="Estado de compatibilidad de actividad: COMPATIBLE, INCOMPATIBLE o SIN_ACTIVIDAD_DECLARADA.",
    )
    overlap_area_m2: float = Field(..., ge=0.0, description="Superficie superpuesta con el polígono de permiso (m²).")
    overlap_pct: float = Field(..., ge=0.0, le=100.0, description="Porcentaje del área anómala dentro del permiso.")
    excess_area_m2: float = Field(..., ge=0.0, description="Superficie de anomalía fuera del polígono autorizado (m²).")
    excess_pct: float = Field(..., ge=0.0, le=100.0, description="Porcentaje del área anómala fuera del permiso.")
    distance_m: float = Field(..., ge=0.0, description="Distancia métrica mínima al polígono del permiso (0 si intersecta).")
    permitted_activity: Optional[str] = Field(
        default=None, description="Actividad específica habilitada (ej. 'dragado de arena', 'cantera aluvial')."
    )
    authorized_volume_limit_m3: Optional[float] = Field(
        default=None, ge=0.0, description="Volumen o cupo máximo autorizado en m³ si figura en el padrón."
    )


class PermitValidationResult(BaseModel):
    """Resultado del cruce espacial y temporal contra el catastro de permisos e hidrografía."""

    model_config = ConfigDict(extra="forbid")

    anomaly_id: str = Field(..., description="Identificador único del polígono de anomalía evaluado.")
    classification: PermitClassificationLiteral = Field(
        ...,
        description="Estado del cruce catastral respetando el grado de completitud del padrón cargado.",
    )
    matched_permits: List[PermitMatchInfo] = Field(
        default_factory=list,
        description="Lista de concesiones/permisos intersectados o próximos ordenados por superposición.",
    )
    excess_area_m2: float = Field(..., ge=0.0, description="Área total excedida o sin cobertura de permiso (m²).")
    excess_pct: float = Field(..., ge=0.0, le=100.0, description="Porcentaje de área fuera de permiso vigente.")
    distance_to_water_m: Optional[float] = Field(
        default=None,
        description="Distancia métrica al curso o cuerpo de agua más cercano según IGN/OSM (None si sin datos).",
    )
    watercourse_name: Optional[str] = Field(
        default=None, description="Nombre del río o arroyo más cercano identificado en IGN/OSM."
    )
    osm_matches: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Elementos hidrográficos o extractivos cercanos recuperados de OpenStreetMap.",
    )
    ign_matches: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Entidades hidrográficas o de infraestructura recuperadas del WFS del IGN.",
    )
    priority: InspectionPriorityLiteral = Field(
        ..., description="Prioridad operativa de fiscalización para autoridades ambientales y Prefectura."
    )
    observations: str = Field(
        default="", description="Resumen técnico del cruce catastral y temporal."
    )
    cadastre_metadata: Optional[CadastreMetadata] = Field(
        default=None, description="Metadatos de completitud y fuente oficial del catastro evaluado."
    )
    human_review_required: bool = Field(
        default=True, description="Requerimiento obligatorio de verificación documental/humana antes de sanción."
    )


class VLMFluvialVerdict(BaseModel):
    """Dictamen pericial multimodal auxiliar de geomorfología fluvial y fiscalización ambiental."""

    model_config = ConfigDict(extra="forbid")

    verdict: VLMVerdictLiteral = Field(
        ...,
        description=(
            "Dictamen auxiliar: EXTRACCION_ANTROPICA, DINAMICA_FLUVIAL_NATURAL, "
            "ARTEFACTO_NUBES_SOMBRAS o PENDIENTE_REVISION."
        ),
    )
    certainty_pct: int = Field(..., ge=0, le=100, description="Nivel de certeza del dictamen (0 a 100%).")
    extraction_evidence: str = Field(
        ...,
        description="Descripción de indicadores antrópicos (frentes geométricos, piletas, caminos, dragas/barcazas).",
    )
    fluvial_dynamics_assessment: str = Field(
        ...,
        description="Evaluación comparativa frente a dinámica fluvial natural (estiaje, crecida, meandro).",
    )
    field_inspection_recommended: bool = Field(
        ..., description="Indica si se recomienda inspección en campo / patrullaje fluvial / dron."
    )
    backend: str = Field(default="unknown", description="Backend que emitió la evaluación.")
    evaluation_mode: EvaluationModeLiteral = Field(
        default="HEURISTIC_SCREENING",
        description="Modalidad explícita: HEURISTIC_SCREENING, EXTERNAL_VLM o HUMAN_EXPERT.",
    )
    model_id: Optional[str] = Field(
        default=None, description="Identificador del modelo multimodal (requerido en EXTERNAL_VLM)."
    )
    model_version: Optional[str] = Field(
        default=None, description="Versión o snapshot del modelo multimodal."
    )
    prompt_version: Optional[str] = Field(
        default="rio-alerta-fluvial-v1.0", description="Versión del prompt estructurado utilizado."
    )
    image_refs: List[str] = Field(
        default_factory=list, description="Rutas o URIs de las imágenes efectivamente evaluadas."
    )
    image_hashes: Dict[str, str] = Field(
        default_factory=dict, description="Hashes SHA-256 de las imágenes locales evaluadas."
    )
    evaluated_at_utc: Optional[str] = Field(
        default=None, description="Marca temporal ISO-8601 UTC de ejecución de la evaluación."
    )
    evaluator_id: Optional[str] = Field(
        default=None, description="Identificador o matrícula del especialista humano (HUMAN_EXPERT)."
    )
    is_statistical_probability: bool = Field(
        default=False,
        description="Indica si certainty_pct constituye probabilidad estadística calibrada (False en cribado heurístico/VLM).",
    )
    is_forensic_certification: bool = Field(
        default=False,
        description="Ninguna evaluación automatizada (HEURISTIC_SCREENING o EXTERNAL_VLM) equivale a constatación pericial.",
    )
    human_review_required: bool = Field(
        default=True,
        description="El resultado es auxiliar y requiere validación por un inspector humano habilitado.",
    )


class EnforcementReportResult(BaseModel):
    """Resultado de la emisión del acta técnica reproducible de fiscalización ambiental."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="Estado de generación del expediente (ej. 'success').")
    anomaly_id: str = Field(..., description="Identificador único del hallazgo reportado.")
    report_md_path: str = Field(..., description="Ruta al acta técnica en Markdown.")
    report_json_path: str = Field(..., description="Ruta al expediente estructurado en JSON.")
    report_generation_id: Optional[str] = Field(
        default=None, description="Identificador único e inmutable de la generación del expediente."
    )
    generation_dir_path: Optional[str] = Field(
        default=None, description="Ruta al directorio inmutable de la generación publicada."
    )
    manifest_json_path: Optional[str] = Field(
        default=None, description="Ruta al manifiesto criptográfico manifest.json de la generación."
    )
    markdown_sha256: Optional[str] = Field(
        default=None, description="Digest SHA-256 del artefacto report.md."
    )
    report_json_sha256: Optional[str] = Field(
        default=None, description="Digest SHA-256 del artefacto report.json."
    )
    software_commit_sha: Optional[str] = Field(
        default=None, description="SHA del commit git de RíoAlerta con el cual se emitió el informe."
    )
    classification: PermitClassificationLiteral = Field(..., description="Clasificación catastral definitiva.")
    priority: InspectionPriorityLiteral = Field(..., description="Prioridad de fiscalización asignada.")
    area_m2: float = Field(..., gt=0.0, description="Superficie afectada en metros cuadrados.")
    area_ha: float = Field(..., gt=0.0, description="Superficie afectada en hectáreas.")
    crs_epsg: int = Field(..., description="Código EPSG POSGAR 2007 de cálculo métrico.")
    centroid_posgar: Tuple[float, float] = Field(..., description="Coordenadas del centroide (X, Y) POSGAR 2007.")
    centroid_wgs84: Tuple[float, float] = Field(..., description="Coordenadas del centroide (lon, lat) WGS84.")
    t0_scene_id: str = Field(..., description="ID STAC de la escena histórica T0.")
    t0_date: str = Field(..., description="Fecha ISO de adquisición de la escena T0.")
    t1_scene_id: str = Field(..., description="ID STAC de la escena actual T1.")
    t1_date: str = Field(..., description="Fecha ISO de adquisición de la escena T1.")


CaseStatusLiteral = Literal[
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
]

CaseActionTypeLiteral = Literal[
    "APERTURA_CASO",
    "NOTA_ANALISTA",
    "ACTUALIZACION_CATASTRAL",
    "DICTAMEN_VLM",
    "SOLICITUD_INSPECCION",
    "RESULTADO_CAMPO",
    "CAMBIO_ESTADO",
]


class CaseEventRecord(BaseModel):
    """Evento auditable append-only del historial de investigación de un candidato."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., description="Identificador único del evento en la bitácora.")
    case_id: str = Field(..., description="Identificador estable del expediente o caso.")
    candidate_uid: str = Field(..., description="Identificador determinista del candidato satelital.")
    anomaly_id: str = Field(..., description="Código de anomalía en la corrida asociada (ej. RIO-0184).")
    timestamp_utc: str = Field(..., description="Marca temporal ISO-8601 UTC de registro del evento.")
    action_type: CaseActionTypeLiteral = Field(..., description="Tipo de acción registrada en el expediente.")
    actor: str = Field(..., description="Usuario, operador o servicio que registró el evento.")
    previous_status: Optional[CaseStatusLiteral] = Field(
        default=None, description="Estado previo del caso antes de este evento."
    )
    new_status: CaseStatusLiteral = Field(..., description="Estado vigente del caso luego de este evento.")
    summary: str = Field(..., description="Descripción técnica o nota institucional auditable del evento.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Metadatos estructurados adicionales del evento."
    )
    idempotency_key: Optional[str] = Field(
        default=None, description="Clave opcional de idempotencia para evitar duplicación de eventos."
    )
    prev_event_hash: Optional[str] = Field(
        default=None, description="Hash SHA-256 del evento inmediato anterior en la bitácora."
    )
    event_hash: Optional[str] = Field(
        default=None, description="Hash SHA-256 encadenado del registro canónico del evento."
    )
    human_review_required: bool = Field(
        default=True,
        description="Indica que toda decisión administrativa/judicial requiere verificación humana competente.",
    )


class CaseRecord(BaseModel):
    """Estado consolidado actual de un expediente de investigación territorial en RíoAlerta."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., description="Identificador único estable del expediente.")
    candidate_uid: str = Field(..., description="Identificador determinista estable del candidato.")
    anomaly_id: str = Field(..., description="Último código de anomalía asociado al candidato.")
    status: CaseStatusLiteral = Field(..., description="Estado administrativo/técnico actual del expediente.")
    priority: InspectionPriorityLiteral = Field(..., description="Prioridad operativa vigente.")
    crs_epsg: int = Field(..., description="Código EPSG POSGAR 2007 de referencia métrica.")
    centroid_wgs84: Tuple[float, float] = Field(..., description="Centroide (lon, lat) WGS84.")
    area_m2: float = Field(..., gt=0.0, description="Superficie métrica estimada en m² (POSGAR 2007).")
    created_at_utc: str = Field(..., description="Fecha y hora UTC de apertura inicial del caso.")
    updated_at_utc: str = Field(..., description="Fecha y hora UTC de última actualización.")
    events_count: int = Field(..., ge=1, description="Cantidad total de eventos registrados en el historial.")
    latest_summary: str = Field(..., description="Resumen del último evento registrado.")
    human_review_required: bool = Field(
        default=True,
        description="Indica que ningún resultado satelital automatizado constituye imputación legal directa.",
    )


def coerce_anomaly_feature(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
) -> ExtractionAnomalyFeature:
    """Convierte un `ExtractionAnomalyFeature`, dict plano o Feature GeoJSON al modelo estricto.

    Args:
        anomaly: Instancia `ExtractionAnomalyFeature`, diccionario con campos del modelo o
            Feature GeoJSON emitido por `detect_extraction_anomalies`.

    Returns:
        Instancia validada de `ExtractionAnomalyFeature`.
    """
    if isinstance(anomaly, ExtractionAnomalyFeature):
        return anomaly
    if not isinstance(anomaly, dict):
        raise TypeError("Se esperaba ExtractionAnomalyFeature o dict.")
    if anomaly.get("type") == "Feature" and "properties" in anomaly and "geometry" in anomaly:
        props = dict(anomaly["properties"])
        props.setdefault("id", anomaly.get("id", "RIO-0000"))
        props.setdefault("geometry_geojson", anomaly["geometry"])
        return ExtractionAnomalyFeature(**props)
    return ExtractionAnomalyFeature(**anomaly)

