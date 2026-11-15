# RíoAlerta (`rio-alerta-mcp`)

> **Vigilancia Satelital Multitemporal de Extracción de Áridos en Ríos Argentinos mediante Sentinel-2 L2A, Controles de Dinámica Fluvial y Proyección Oficial POSGAR 2007**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastMCP](https://img.shields.io/badge/MCP-FastMCP-emerald.svg)](https://github.com/jlowin/fastmcp)
[![CRS POSGAR 2007](https://img.shields.io/badge/CRS-POSGAR%202007%20(EPSG%3A5343--5349)-orange.svg)](https://www.ign.gob.ar/)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)

---

## Resumen Ejecutivo (Abstract)

**RíoAlerta (`rio-alerta-mcp`)** es un servidor **Model Context Protocol (FastMCP)** y motor geoespacial multitemporal diseñado para asistir a autoridades ambientales provinciales, municipios, Prefectura Naval Argentina (PNA) y organismos de recursos hídricos en la **detección, cuantificación y auditoría reproducible de alteraciones compatibles con extracción de áridos** (cavas inundadas, frentes de dragado, desmontes de ribera, playas de acopio y caminos de acceso) sobre corredores fluviales argentinos.

A diferencia de los detectores de cambio genéricos por diferencia simple de píxeles, RíoAlerta modela explícitamente la **física espectral de sensores ópticos multiespectrales (Sentinel-2 L2A)** y la **geomorfología de ríos aluviales de llanura**:

1. **Preservación de resoluciones nativas (`10 m` VNIR / `20 m` SWIR+SCL)** y separación estricta entre remuestreo radiométrico (`bilinear`) y categórico (`nearest`).
2. **Ingesta eficiente por HTTP Range Requests (`GDAL/VSICURL`)** sobre Cloud-Optimized GeoTIFFs (COG) en STAC (Element 84 Earth Search / Microsoft Planetary Computer), con filtro de cobertura completa de tesela MGRS (`>= 98%`) para evitar truncamientos de borde.
3. **Proyección métrica automática a POSGAR 2007 (`EPSG:5343–5349`, Fajas 1 a 7)** para garantizar áreas en $\text{m}^2/\text{ha}$ y distancias métricas exactas en todo el territorio argentino.
4. **Controles de dinámica fluvial natural** (pulsos de crecida/bajante, dipolos acoplados de migración de bancos de arena, deriva fenológica de la vegetación en galería y confusores de turbidez).
5. **Prudencia institucional y trazabilidad:** las geometrías emitidas constituyen *anomalías morfológicas candidatas* con metadatos explícitos de incertidumbre (`UncertaintyMetadata`), nunca calificaciones jurídicas autónomas sin cruce catastral oficial y verificación humana.

---

## 1. Marco Metodológico y Fundamento Científico (Base tipo Paper)

### 1.1 Problema de Detección en Llanuras Aluviales

Los sistemas fluviales argentinos —en particular el **Río Paraná Medio e Inferior**— presentan una dinámica hidromorfológica intensa caracterizada por variaciones de nivel hidrométrico superiores a $4\text{ m}$, migración continua de barras arenosas, inundación estacional de lagunas isleñas detrás de albardones y presencia simultánea de aguas altamente turbias (carga de sedimentos en suspensión) y aguas negras/húmicas ricas en materia orgánica disuelta (`CDOM`).

Un detector bitemporal ingenuo basado únicamente en índices espectrales normalizados colapsa en este entorno por tres mecanismos físicos documentados en el **Experimento 001 (Paraná Medio)**:
- **Singularidad de cocientes en baja reflectancia:** en aguas negras/húmicas (`B08 -> 0`, `B11 < 200 DN`), el denominador del *Bare Soil Index* (`BSI`) tiende a cero y amplifica ruido atmosférico residual Sen2Cor, generando falsos positivos de suelo desnudo.
- **Inundación no lineal de llanura aluvial:** por encima de la cota de coronamiento de albardones ($\approx 3.0\text{--}3.2\text{ m}$ en Puerto Paraná / Santa Fe), las cubetas isleñas desconectadas del cauce de estiaje se llenan por desborde o napa freática, produciendo incrementos abruptos de `MNDWI` sin conexión superficial directa (`<= 1.5 px`) con la lámina de $T_0$.
- **Confusores de turbidez y sumersión de barras:** la sumersión de un banco somero brillante (`B04_T0 = 988 -> B04_T1 = 641 DN`) invierte el signo de $\Delta B_{04}$, burlando filtros unidireccionales de turbidez.

### 1.2 Formulación Espectral

Dadas las bandas de reflectancia de superficie Sentinel-2 L2A (`BOA_OFFSET_APPLIED = True`, escala `10000` DN):
- **VNIR (`10 m` nativos):** $B_{02}$ (Azul, $490\text{ nm}$), $B_{03}$ (Verde, $560\text{ nm}$), $B_{04}$ (Rojo, $665\text{ nm}$), $B_{08}$ (NIR, $842\text{ nm}$).
- **SWIR + SCL (`20 m` nativos):** $B_{11}$ (SWIR1, $1610\text{ nm}$), $B_{12}$ (SWIR2, $2190\text{ nm}$), `SCL` (*Scene Classification Layer*).

Para cada escena válida (`SCL` $\notin \{0,1,2,3,8,9,10,11\}$), el motor calcula:

1. **Modified Normalized Difference Water Index (MNDWI, Xu 2006):**
   $$\text{MNDWI} = \frac{B_{03} - B_{11}}{B_{03} + B_{11} + \epsilon}$$
   Sensible a lámina de agua abierta, excavaciones inundadas y contenido hídrico superficial.
2. **Bare Soil Index (BSI, Rikimaru et al. 2002):**
   $$\text{BSI} = \frac{(B_{11} + B_{04}) - (B_{08} + B_{02})}{(B_{11} + B_{04}) + (B_{08} + B_{02}) + \epsilon}$$
   *(En Fase 2.1 se incorpora además la variante regularizada por albedo $\text{BSI}_{\text{reg}}(k)$ con $k=800\text{ DN}$ para suprimir singularidades en aguas negras).*
3. **Normalized Difference Vegetation Index (NDVI):**
   $$\text{NDVI} = \frac{B_{08} - B_{04}}{B_{08} + B_{04} + \epsilon}$$

### 1.3 Reglas de Detección y Controles Naturales

Entre la línea base histórica $T_0$ (escena única o mediana libre de nubes de hasta $N$ años en ventana estacional $\pm 45\text{ días}$ del día del año `DOY`) y la escena actual $T_1$:

- **Normalización fenológica ribereña:** sobre píxeles de vegetación densa en $T_0$ ($\text{NDVI}_{T_0} > 0.45$), se estima la deriva mediana estacional $\tilde{\Delta}\text{NDVI}$ y $\tilde{\Delta}\text{BSI}$, descontando únicamente senescencia regional uniforme ($\Delta\text{NDVI}_{\text{adj}}, \Delta\text{BSI}_{\text{adj}}$).
- **Control hidrométrico del tramo (`compute_reach_hydrological_state`):** cuantifica la variación porcentual de lámina de agua (`water_stage_delta_pct`), la fracción de inundación perimetral (`shoreline_flooding_fraction`) y construye `uniform_stage_belt_mask` / `drawdown_belt_mask` mediante transformada de distancia euclidiana (`EDT`).
- **Control de dipolos de migración de bancos (`detect_natural_bar_migration_dipoles`):** empareja componentes de erosión ($\Delta\text{MNDWI} > +0.35$, $\text{NDVI}_{T_0} < 0.35$) con componentes de acreción aguas abajo ($\Delta\text{MNDWI} < -0.35$, $\text{MNDWI}_{T_0} > 0.05$) de área comparable (`ratio` $\in [0.40, 2.50]$) dentro de un radio máximo de $450\text{ m}$.
- **Piso físico de detección:** `min_anomaly_area_m2 = 1600.0 m²` ($\ge 4$ píxeles nativos SWIR de $20\text{ m} \times 20\text{ m}$), con clausura morfológica $3\times 3$ y vectorización `OpenCV` (`RETR_EXTERNAL`) en coordenadas métricas POSGAR 2007.

---

## 2. Arquitectura del Repositorio

```text
rio-alerta-mcp/
├── pyproject.toml                  # Dependencias, ruff, mypy y configuración pytest
├── Dockerfile                      # Contenedor reproducible con GDAL/rasterio
├── LICENSE                         # Licencia MIT
├── .env.example                    # Variables de entorno (STAC, límites de RAM, CRS)
├── src/
│   ├── config.py                   # Configuración CWD-safe (PROJECT_ROOT) y fajas POSGAR 2007
│   ├── schemas.py                  # Modelos Pydantic v2 (SceneMetadata, ExtractionAnomalyFeature, etc.)
│   ├── server.py                   # Servidor FastMCP con herramientas MCP expuestas
│   └── core/
│       ├── crs_manager.py          # Resolución automática de fajas EPSG:5343–5349 y reproyección
│       ├── optical_providers.py    # Cliente STAC, filtro de cobertura MGRS, ingesta COG 10m/20m y SCL
│       ├── change_detector.py      # Índices ΔMNDWI/ΔBSI/ΔNDVI, segmentación y metadatos de incertidumbre
│       ├── hydrological_control.py # Control de crecidas/bajantes, dipolos de barras y fenología
│       ├── dossier_generator.py    # Generador de Dossiers de Investigación Territorial (9 secciones)
│       └── parana_pilot_recon.py   # Diagnóstico geodésico/STAC del tramo piloto Paraná Medio
├── scripts/
│   ├── run_exp_001_parana_medio.py # Pipeline reproducible del Experimento 001 (Paraná Medio)
│   ├── run_exp_002_validation.py   # Validación Fase 2.1 (estiaje Paraná Medio + AOI2 Diamante)
│   └── generate_dossier_rio_0184.py# Generación del Dossier técnico auditable RIO-0184 + figuras
├── data/
│   └── experiments/
│       ├── exp_001_parana_medio/   # Informe técnico, JSON resumen, GeoJSONs y 8 láminas PNG
│       ├── exp_001_recalibrated/   # Recalibración Fase 2.1 sobre Paraná Medio
│       └── exp_002_validation/     # Validación cruzada (Paraná Medio estiaje + Diamante)
├── skills/
│   └── rio-alerta/
│       └── SKILL.md                # Skill portable para Antigravity, OpenCode y Claude
└── tests/                          # Suite unitaria e integración (34 tests)
```

---

## 3. Catálogo de Herramientas MCP y Funciones Principales

### 3.1 Herramientas expuestas por el Servidor FastMCP (`src/server.py`)

| Herramienta MCP | Parámetros Principales | Retorno (`Pydantic` / `Dict`) | Descripción Operativa |
| --- | --- | --- | --- |
| `tool_server_status` | *(ninguno)* | `Dict[str, Any]` | Verifica estado del servidor, versión, rutas (`data/`, `processed/`, `reports/`), fajas POSGAR 2007, existencia de `permits.gpkg` y topes de procesamiento. |
| `tool_resolve_posgar_crs` | `lon: float`, `lat: float = -32.9` | `Dict[str, Any]` | Resuelve automáticamente la faja oficial POSGAR 2007 (`EPSG:5343–5349`) para cualquier coordenada de Argentina y proyecta `(lon, lat)` a `(x_posgar_m, y_posgar_m)`. |
| `tool_search_multitemporal_pair` | `min_lon: float`, `min_lat: float`, `max_lon: float`, `max_lat: float`, `t1_end_date: str \| None`, `lookback_years: int = 3` | `MultitemporalScenePair` | Busca en STAC y recorta por HTTP Range Request un par comparable Sentinel-2 L2A (10m VNIR + 20m SWIR/SCL) en POSGAR 2007 listo para detección. |
| `tool_compute_spectral_changes` | `t0_path: str`, `t1_path: str`, `t0_swir_20m_path: str \| None`, `t1_swir_20m_path: str \| None` | `SpectralChangeResult` | Calcula GeoTIFFs Float32 de $\Delta\text{MNDWI}$, $\Delta\text{BSI}$ y $\Delta\text{NDVI}$ en POSGAR 2007 respetando 10m/20m nativos y reporta `water_stage_delta_pct`. |
| `tool_detect_extraction_anomalies` | `t0_path: str`, `t1_path: str`, `t0_swir_20m_path: str \| None`, `t1_swir_20m_path: str \| None`, `min_area_m2: float = 1600.0` | `ChangeDetectionResult` | Ejecuta el motor completo: controles hidrológicos, dipolos de barras, normalización fenológica, segmentación morfológica y emisión de `FeatureCollection` GeoJSON con `UncertaintyMetadata`. |
| `tool_evaluate_parana_pilot` | `offline_mode: bool = True` | `Dict[str, Any]` | Evalúa factibilidad geodésica (`EPSG:5347`), disponibilidad STAC e institucional del corredor piloto Santa Fe – Paraná. |

### 3.2 Funciones del Núcleo (`src/core/`)

- **Geodesia (`crs_manager.py`):** `get_posgar_faja(lon)`, `bbox_wgs84_to_posgar(bbox)`, `ensure_metric_crs(...)`, `reproject_array_to_grid(...)`, `reproject_geometry(...)`.
- **Ingesta Óptica (`optical_providers.py`):** `decode_scl_masks(scl)`, `load_native_s2_scene_bundle(...)`, `filter_seasonally_comparable_items(...)`, `query_stac_seasonal_pair_candidates(...)`, `search_and_fetch_stac_multitemporal(...)`, `fetch_xyz_rgb_chip(...)`.
- **Detección Multitemporal (`change_detector.py`):** `compute_spectral_indices(...)`, `compute_multitemporal_baseline_and_changes(...)`, `detect_extraction_anomalies(...)`.
- **Dinámica Fluvial (`hydrological_control.py`):** `compute_reach_hydrological_state(...)`, `detect_natural_bar_migration_dipoles(...)`, `normalize_seasonal_riparian_indices(...)`, `compute_turbidity_confounder_flag(...)`.
- **Dossiers de Investigación Territorial (`dossier_generator.py`):** `generate_candidate_dossier(...)`, `generate_negative_or_area_dossier(...)`.

---

## 4. Validación Empírica: Experimento 001 (Paraná Medio)

El script [run_exp_001_parana_medio.py](file:///Users/pablotempone/rio-alerta-mcp/scripts/run_exp_001_parana_medio.py) ejecutó el motor de Fase 2 sobre el corredor **Santa Fe (Canal de Acceso / Colastiné) – Islas – Bajada Grande / Paraná** (`(-60.66, -31.74, -60.53, -31.64)`, `13,817.45 ha`, `EPSG:5347`), comparando `2024-11-06` (estiaje, `0.67 m` PNA Paraná) vs `2026-11-08` (crecida, `4.85 m` PNA Paraná, `+26.29%` de área de agua).

- **Informe completo:** [INFORME_EXPERIMENTO_PARANA_MEDIO.md](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/INFORME_EXPERIMENTO_PARANA_MEDIO.md)
- **Resultados clave:** se identificaron 4 focos costeros reales sobre ribera firme con reflectancia mineral alta (`B11_T1 >= 1573 DN`: `#218 RIO-0184`, `#322 RIO-0225`, `#380 RIO-0199`, `#389 RIO-0365`) y se diagnosticaron con precisión las dos familias de falsos positivos (agua negra/húmica y lagunas isleñas desconectadas) que motivan la calibración científica de **Fase 2.1**.

---

## 5. Instalación y Uso

### 5.1 Requisitos e Instalación con `uv`

```bash
git clone https://github.com/<tu-usuario>/rio-alerta-mcp.git
cd rio-alerta-mcp

# Crear entorno virtual e instalar dependencias (incluyendo dev)
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 5.2 Verificación de Calidad y Tests

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src
.venv/bin/pytest -v
```

### 5.3 Ejecución del Servidor FastMCP

```bash
# Modo stdio (para Antigravity, Claude Desktop, OpenCode, Cursor)
.venv/bin/python -m src.server
```

Ejemplo de configuración en clientes MCP (`mcp_config.json` / `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rio-alerta": {
      "command": "/ruta/absoluta/a/rio-alerta-mcp/.venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "/ruta/absoluta/a/rio-alerta-mcp"
    }
  }
}
```

### 5.4 Ejecución del Experimento Reproducible

```bash
.venv/bin/python scripts/run_exp_001_parana_medio.py
```

---

## 6. Hoja de Ruta

- **Fase 1 (Completada):** Scaffolding FastMCP, esquemas Pydantic v2, `config.py` CWD-safe, fajas POSGAR 2007, Dockerfile y CI.
- **Fase 2 (Completada):** Proveedor STAC Sentinel-2 L2A (10m/20m nativos + SCL), motor multitemporal y controles de dinámica fluvial.
- **Fase 2.1 (En curso — Diseño aprobado en auditoría):** Corrección de agua oscura ($\text{BSI}_{\text{reg}}$ + umbrales minerales), zonificación geomorfológica en 4 zonas, integración de hidrómetros oficiales PNA/INA y validación en estiaje comparable + segundo tramo independiente (`Diamante`).
- **Fase 3 (Planificada):** Catastro de concesiones (`permits_validator.py`), Juez Multimodal VLM (`vlm_judge.py`) y generador de actas técnicas (`report_generator.py`).
