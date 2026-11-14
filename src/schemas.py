"""Modelos Pydantic v2 estrictos de entrada y salida para las herramientas MCP de RíoAlerta."""

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

ChangeAnomalyTypeLiteral = Literal[
    "frente_excavacion_inundado",
    "desmonte_ribera_acopio",
    "apertura_camino_acceso",
    "alteracion_cauce_barra",
    "complejo_extractivo",
]

PermitClassificationLiteral = Literal[
    "EXTRACCION_AUTORIZADA",
    "EXCESO_DE_POLIGONO",
    "PERMISO_VENCIDO",
    "EXTRACCION_SIN_PERMISO",
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
        description="Lista de bandas espectrales leídas (ej. ['B02', 'B03', 'B04', 'B08', 'B11', 'B12']).",
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
    resolution_m: float = Field(..., gt=0.0, description="Resolución espacial en metros.")
    bounds_posgar: Tuple[float, float, float, float] = Field(
        ..., description="Extensión métrica (minx, miny, maxx, maxy)."
    )
    stats: Dict[str, float] = Field(
        default_factory=dict,
        description="Estadísticas descriptivas (min, max, mean, std) por índice diferencial.",
    )


class ExtractionAnomalyFeature(BaseModel):
    """Representación tipada de una anomalía de posible extracción de áridos detectada entre T0 y T1."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Identificador único del polígono de anomalía (ej. 'RIO-0001').")
    anomaly_type: ChangeAnomalyTypeLiteral = Field(
        ..., description="Tipología espectral/morfológica de la alteración fluvial o ribereña."
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Nivel de confianza algorítmica [0.0 - 1.0].")
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


class ChangeDetectionResult(BaseModel):
    """FeatureCollection GeoJSON tipada con las anomalías extractivas detectadas entre T0 y T1."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["FeatureCollection"] = Field(default="FeatureCollection", description="Tipo GeoJSON.")
    crs: Dict[str, Any] = Field(..., description="Definición del sistema de referencia espacial (CRS84).")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metadatos cuantitativos de la detección (escenas T0/T1, umbrales, conteos).",
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
    fecha_desde: str = Field(..., description="Fecha de inicio de vigencia (YYYY-MM-DD).")
    fecha_hasta: str = Field(..., description="Fecha de vencimiento de vigencia (YYYY-MM-DD).")
    vigente_en_t1: bool = Field(..., description="Indica si el permiso estaba vigente en la fecha de T1.")
    overlap_area_m2: float = Field(..., ge=0.0, description="Superficie superpuesta con el polígono de permiso (m²).")
    overlap_pct: float = Field(..., ge=0.0, le=100.0, description="Porcentaje del área anómala dentro del permiso.")
    excess_area_m2: float = Field(..., ge=0.0, description="Superficie de anomalía fuera del polígono autorizado (m²).")
    excess_pct: float = Field(..., ge=0.0, le=100.0, description="Porcentaje del área anómala fuera del permiso.")
    distance_m: float = Field(..., ge=0.0, description="Distancia métrica mínima al polígono del permiso (0 si intersecta).")


class PermitValidationResult(BaseModel):
    """Resultado del cruce espacial y temporal contra el catastro de permisos e hidrografía."""

    model_config = ConfigDict(extra="forbid")

    anomaly_id: str = Field(..., description="Identificador único del polígono de anomalía evaluado.")
    classification: PermitClassificationLiteral = Field(
        ...,
        description=(
            "Estado catastral: EXTRACCION_AUTORIZADA, EXCESO_DE_POLIGONO, "
            "PERMISO_VENCIDO o EXTRACCION_SIN_PERMISO."
        ),
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


class VLMFluvialVerdict(BaseModel):
    """Dictamen pericial multimodal de geomorfología fluvial y fiscalización ambiental."""

    model_config = ConfigDict(extra="forbid")

    verdict: VLMVerdictLiteral = Field(
        ...,
        description=(
            "Dictamen: EXTRACCION_ANTROPICA, DINAMICA_FLUVIAL_NATURAL, "
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
    backend: str = Field(default="unknown", description="Backend que emitió el dictamen pericial.")


class EnforcementReportResult(BaseModel):
    """Resultado de la emisión del acta técnica reproducible de fiscalización ambiental."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="Estado de generación del expediente (ej. 'success').")
    anomaly_id: str = Field(..., description="Identificador único del hallazgo reportado.")
    report_md_path: str = Field(..., description="Ruta al acta técnica en Markdown.")
    report_json_path: str = Field(..., description="Ruta al expediente estructurado en JSON.")
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
