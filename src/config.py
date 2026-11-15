"""Configuración global CWD-safe y constantes geodésicas POSGAR 2007 para RíoAlerta."""

import os
from pathlib import Path
from typing import Dict, Literal, Tuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configuración estricta GDAL/VSICURL para lecturas remotas COG por HTTP Range Request (sin descargas completas)
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff,.jp2,.vrt")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("VSI_CACHE_SIZE", "25000000")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_HTTP_RETRY_MAX", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1.0")

# Diccionario oficial de Fajas POSGAR 2007 (Faja 1 a Faja 7) -> Código EPSG
POSGAR_2007_EPSG: Dict[int, int] = {
    1: 5343,  # Meridiano central -72° (Oeste cordillerano austral / cuyano extremo)
    2: 5344,  # Meridiano central -69° (NOA Oeste, Cuyo, Neuquén/Mendoza/San Juan)
    3: 5345,  # Meridiano central -66° (NOA Centro/Este, Tucumán, Salta, Jujuy, Córdoba Oeste)
    4: 5346,  # Meridiano central -63° (Centro del país, Córdoba, Santa Fe Oeste, La Pampa)
    5: 5347,  # Meridiano central -60° (Litoral, Buenos Aires, Entre Ríos, Santa Fe Este, Paraná)
    6: 5348,  # Meridiano central -57° (Mesopotamia Este, Corrientes, Misiones Oeste, Uruguay)
    7: 5349,  # Meridiano central -54° (Misiones Este)
}

# Límites longitudinales por faja POSGAR 2007: (lon_min, lon_max)
POSGAR_2007_LON_BOUNDS: Dict[int, Tuple[float, float]] = {
    1: (-76.0, -70.5),
    2: (-70.5, -67.5),
    3: (-67.5, -64.5),
    4: (-64.5, -61.5),
    5: (-61.5, -58.5),
    6: (-58.5, -55.5),
    7: (-55.5, -52.0),
}

WGS84_EPSG: int = 4326
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuración del servidor RíoAlerta-MCP validada con Pydantic Settings."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "raw",
        alias="DATA_DIR",
        description="Directorio de entrada para escenas multiespectrales crudas y catastro local.",
    )
    processed_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "processed",
        alias="PROCESSED_DIR",
        description="Directorio de salida para diferencias espectrales, máscaras y láminas comparativas.",
    )
    reports_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "reports",
        alias="REPORTS_DIR",
        description="Directorio de salida para actas de fiscalización y expedientes reproducibles.",
    )
    permits_gpkg_path: Path = Field(
        default=PROJECT_ROOT / "data" / "permits.gpkg",
        alias="PERMITS_GPKG_PATH",
        description="Ruta al GeoPackage/GeoJSON local de concesiones y permisos de extracción de áridos.",
    )
    http_timeout_seconds: float = Field(
        default=25.0,
        gt=0.0,
        alias="HTTP_TIMEOUT_SECONDS",
        description="Timeout máximo en segundos para peticiones HTTP a STAC, IGN WFS y Overpass.",
    )
    ign_wfs_url: str = Field(
        default="https://wms.ign.gob.ar/geoserver/ows",
        alias="IGN_WFS_URL",
        description="Endpoint OWS/WFS oficial del Instituto Geográfico Nacional de Argentina.",
    )
    overpass_url: str = Field(
        default="https://overpass-api.de/api/interpreter",
        alias="OVERPASS_URL",
        description="Endpoint de consulta Overpass API para hidrografía e infraestructura OpenStreetMap.",
    )
    optical_provider: Literal["none", "sentinel2_stac", "planetary_stac", "xyz_tiles"] = Field(
        default="sentinel2_stac",
        alias="OPTICAL_PROVIDER",
        description="Proveedor óptico/multiespectral principal para pares multitemporales T0/T1.",
    )
    earth_search_stac_url: str = Field(
        default="https://earth-search.aws.element84.com/v1",
        alias="EARTH_SEARCH_STAC_URL",
        description="Endpoint STAC público de Element 84 Earth Search (Sentinel-2 L2A).",
    )
    planetary_stac_url: str = Field(
        default="https://planetarycomputer.microsoft.com/api/stac/v1",
        alias="PLANETARY_STAC_URL",
        description="Endpoint STAC de Microsoft Planetary Computer.",
    )
    xyz_tile_url: str = Field(
        default="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        alias="XYZ_TILE_URL",
        description="Plantilla URL de teselas XYZ para chips RGB de alta resolución.",
    )
    max_cloud_cover_pct: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        alias="MAX_CLOUD_COVER_PCT",
        description="Cobertura nubosa máxima admitida (%) para seleccionar escenas Sentinel-2 L2A.",
    )
    seasonal_window_days: int = Field(
        default=45,
        ge=5,
        le=180,
        alias="SEASONAL_WINDOW_DAYS",
        description="Semiancho en días calendario alrededor del día del año de T1 para seleccionar línea base T0 comparable.",
    )
    min_valid_pixel_fraction: float = Field(
        default=0.70,
        ge=0.1,
        le=1.0,
        alias="MIN_VALID_PIXEL_FRACTION",
        description="Fracción mínima de píxeles válidos según máscara SCL para aceptar una escena o parche.",
    )
    baseline_max_scenes: int = Field(
        default=3,
        ge=1,
        le=12,
        alias="BASELINE_MAX_SCENES",
        description="Cantidad máxima de escenas históricas T0 promediadas por mediana para línea base multitemporal.",
    )
    max_master_side_px: int = Field(
        default=4096,
        ge=64,
        alias="MAX_MASTER_SIDE_PX",
        description="Tope duro en píxeles por lado para el recorte maestro reproyectado a POSGAR 2007.",
    )
    max_optical_chip_px: int = Field(
        default=512,
        ge=64,
        alias="MAX_OPTICAL_CHIP_PX",
        description="Tope duro en píxeles por lado para los chips ópticos RGB de láminas comparativas.",
    )
    optical_buffer_m: float = Field(
        default=200.0,
        ge=0.0,
        alias="OPTICAL_BUFFER_M",
        description="Buffer métrico alrededor del bbox POSGAR 2007 del candidato para inspección visual.",
    )
    min_anomaly_area_m2: float = Field(
        default=1600.0,
        gt=0.0,
        alias="MIN_ANOMALY_AREA_M2",
        description="Área mínima en m² (4 píxeles nativos de 20x20 m para bandas SWIR) para retener una anomalía.",
    )
    excess_tolerance_pct: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        alias="EXCESS_TOLERANCE_PCT",
        description="Tolerancia porcentual de superficie fuera del polígono autorizado antes de marcar EXCESO_DE_POLIGONO.",
    )
    # Parámetros calibrables Fase 2.1 (agua oscura y controles hidrológicos/geomorfológicos)
    bsi_reg_albedo_k: float = Field(
        default=800.0,
        ge=0.0,
        alias="BSI_REG_ALBEDO_K",
        description="Constante de regularización por albedo (DN) para BSI_reg contra singularidad en aguas negras.",
    )
    dark_water_min_b11_dn: float = Field(
        default=600.0,
        ge=0.0,
        alias="DARK_WATER_MIN_B11_DN",
        description="Reflectancia mínima SWIR1 (B11 en DN escala 10000) exigida para suelo/árido expuesto.",
    )
    dark_water_min_b04_dn: float = Field(
        default=420.0,
        ge=0.0,
        alias="DARK_WATER_MIN_B04_DN",
        description="Reflectancia mínima Rojo (B04 en DN escala 10000) para separar suelo/arena de vegetación oscura.",
    )
    dark_water_min_b08_dn: float = Field(
        default=350.0,
        ge=0.0,
        alias="DARK_WATER_MIN_B08_DN",
        description="Reflectancia mínima NIR (B08 en DN escala 10000) para excluir agua húmica.",
    )
    scl7_min_b11_dn: float = Field(
        default=850.0,
        ge=0.0,
        alias="SCL7_MIN_B11_DN",
        description="Umbral SWIR1 conservador para aceptar píxeles mixtos SCL=7 (No clasificado) como suelo mineral.",
    )
    scl7_min_b04_dn: float = Field(
        default=500.0,
        ge=0.0,
        alias="SCL7_MIN_B04_DN",
        description="Umbral Rojo conservador para aceptar píxeles mixtos SCL=7 (No clasificado) como suelo mineral.",
    )
    hydrometric_stage_rise_alert_m: float = Field(
        default=0.80,
        ge=0.0,
        alias="HYDROMETRIC_STAGE_RISE_ALERT_M",
        description="Incremento hidrométrico (m) entre T0 y T1 que activa control estricto de llanura aluvial.",
    )
    dark_water_min_b11_b03_ratio: float = Field(
        default=0.85,
        ge=0.0,
        alias="DARK_WATER_MIN_B11_B03_RATIO",
        description="Cociente mínimo B11/B03 para discriminar sedimento/suelo de agua oscura.",
    )
    moderate_stage_rise_m: float = Field(
        default=0.40,
        ge=0.0,
        alias="MODERATE_STAGE_RISE_M",
        description="Umbral de ascenso hidrométrico moderado (m) para excluir barras móviles.",
    )
    water_stage_warning_pct: float = Field(
        default=8.0,
        ge=0.0,
        alias="WATER_STAGE_WARNING_PCT",
        description="Variación porcentual de lámina de agua a partir de la cual se penaliza incertidumbre.",
    )
    flooded_min_delta_mndwi: float = Field(
        default=0.40,
        alias="FLOODED_MIN_DELTA_MNDWI",
        description="Incremento mínimo de MNDWI para candidato de excavación inundada.",
    )
    flooded_min_t1_mndwi: float = Field(
        default=0.20,
        alias="FLOODED_MIN_T1_MNDWI",
        description="Valor mínimo de MNDWI en T1 para candidato de excavación inundada.",
    )
    clearing_min_delta_bsi_adj: float = Field(
        default=0.25,
        alias="CLEARING_MIN_DELTA_BSI_ADJ",
        description="Incremento mínimo de BSI ajustado para desmonte/acopio.",
    )
    clearing_min_delta_bsi_reg: float = Field(
        default=0.18,
        alias="CLEARING_MIN_DELTA_BSI_REG",
        description="Incremento mínimo de BSI regularizado para desmonte/acopio.",
    )
    clearing_min_t1_bsi_reg: float = Field(
        default=0.03,
        alias="CLEARING_MIN_T1_BSI_REG",
        description="Valor mínimo de BSI regularizado en T1 para desmonte/acopio.",
    )
    clearing_max_delta_ndvi_adj: float = Field(
        default=-0.30,
        alias="CLEARING_MAX_DELTA_NDVI_ADJ",
        description="Caída máxima (negativa) de NDVI ajustado para desmonte/acopio.",
    )
    clearing_min_t1_bsi: float = Field(
        default=-0.02,
        alias="CLEARING_MIN_T1_BSI",
        description="Valor mínimo de BSI estándar en T1 para desmonte/acopio.",
    )
    clearing_max_t1_mndwi: float = Field(
        default=0.10,
        alias="CLEARING_MAX_T1_MNDWI",
        description="Valor máximo de MNDWI en T1 para desmonte/acopio en tierra emergida.",
    )
    road_min_elongation: float = Field(
        default=4.0,
        ge=1.0,
        alias="ROAD_MIN_ELONGATION",
        description="Elongación mínima para reclasificar desmonte como apertura de camino de acceso.",
    )
    cloud_shadow_buffer_m: float = Field(
        default=40.0,
        ge=0.0,
        alias="CLOUD_SHADOW_BUFFER_M",
        description="Buffer métrico (m) de dilatación de nubes/sombras SCL para alerta de adyacencia.",
    )
    turbidity_open_water_mndwi: float = Field(
        default=0.20,
        alias="TURBIDITY_OPEN_WATER_MNDWI",
        description="Umbral de MNDWI en T0 por encima del cual el píxel ya era agua abierta.",
    )
    turbidity_wet_bar_mndwi: float = Field(
        default=0.05,
        alias="TURBIDITY_WET_BAR_MNDWI",
        description="Umbral de MNDWI en T0 para zona de transición barra húmeda / agua somera.",
    )
    turbidity_red_shift_dn: float = Field(
        default=150.0,
        ge=0.0,
        alias="TURBIDITY_RED_SHIFT_DN",
        description="Cambio absoluto mínimo en B04 (DN) indicativo de pulso de turbidez o sumersión de barra.",
    )
    turbidity_bright_bar_b04_dn: float = Field(
        default=750.0,
        ge=0.0,
        alias="TURBIDITY_BRIGHT_BAR_B04_DN",
        description="Reflectancia roja B04 (DN) en T0 indicativa de banco de arena somero brillante.",
    )

    def get_calibration_parameters(self) -> Dict[str, float]:
        """Devuelve el diccionario completo de umbrales de detección y calibración para trazabilidad."""
        return {
            "min_anomaly_area_m2": self.min_anomaly_area_m2,
            "bsi_reg_albedo_k": self.bsi_reg_albedo_k,
            "dark_water_min_b11_dn": self.dark_water_min_b11_dn,
            "dark_water_min_b04_dn": self.dark_water_min_b04_dn,
            "dark_water_min_b08_dn": self.dark_water_min_b08_dn,
            "dark_water_min_b11_b03_ratio": self.dark_water_min_b11_b03_ratio,
            "scl7_min_b11_dn": self.scl7_min_b11_dn,
            "scl7_min_b04_dn": self.scl7_min_b04_dn,
            "hydrometric_stage_rise_alert_m": self.hydrometric_stage_rise_alert_m,
            "moderate_stage_rise_m": self.moderate_stage_rise_m,
            "water_stage_warning_pct": self.water_stage_warning_pct,
            "flooded_min_delta_mndwi": self.flooded_min_delta_mndwi,
            "flooded_min_t1_mndwi": self.flooded_min_t1_mndwi,
            "clearing_min_delta_bsi_adj": self.clearing_min_delta_bsi_adj,
            "clearing_min_delta_bsi_reg": self.clearing_min_delta_bsi_reg,
            "clearing_min_t1_bsi_reg": self.clearing_min_t1_bsi_reg,
            "clearing_max_delta_ndvi_adj": self.clearing_max_delta_ndvi_adj,
            "clearing_min_t1_bsi": self.clearing_min_t1_bsi,
            "clearing_max_t1_mndwi": self.clearing_max_t1_mndwi,
            "road_min_elongation": self.road_min_elongation,
            "cloud_shadow_buffer_m": self.cloud_shadow_buffer_m,
            "turbidity_open_water_mndwi": self.turbidity_open_water_mndwi,
            "turbidity_wet_bar_mndwi": self.turbidity_wet_bar_mndwi,
            "turbidity_red_shift_dn": self.turbidity_red_shift_dn,
            "turbidity_bright_bar_b04_dn": self.turbidity_bright_bar_b04_dn,
        }

    def ensure_directories(self) -> None:
        """Crea las carpetas de datos si no existen."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
