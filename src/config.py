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

    def ensure_directories(self) -> None:
        """Crea las carpetas de datos si no existen."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
