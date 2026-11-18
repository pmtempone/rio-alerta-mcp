# RíoAlerta (`rio-alerta-mcp`)

> **Vigilancia Satelital Multitemporal de Extracción de Áridos en Ríos Argentinos mediante Sentinel-2 L2A, Controles de Dinámica Fluvial y Proyección Oficial POSGAR 2007**
> Servidor **Model Context Protocol (FastMCP)** y motor geoespacial para autoridades ambientales
> provinciales, municipios, Prefectura Naval Argentina (PNA) y organismos de recursos hídricos.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastMCP](https://img.shields.io/badge/MCP-FastMCP-emerald.svg)](https://github.com/jlowin/fastmcp)
[![CRS POSGAR 2007](https://img.shields.io/badge/CRS-POSGAR%202007%20(EPSG%3A5343--5349)-orange.svg)](https://www.ign.gob.ar/)
[![Tests](https://img.shields.io/badge/tests-86%20passed-brightgreen.svg)](#7-verificación-de-calidad)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)

> **⚡ ¿Querés verlo funcionando en 2 minutos?** Ir al [**Manual de Uso Rápido**](docs/MANUAL_DE_USO.md)
> con ejemplos reales de *qué preguntarle por chat → qué herramienta se invoca → qué resultado devuelve*.

---

## Principio rector

**Ninguna detección satelital ni cribado automático constituye imputación jurídica ni prueba
definitiva.** Todo hallazgo se emite como **candidato** con metadatos explícitos de incertidumbre,
sujeto a cruce documental catastral, evaluación pericial humana y constatación presencial
(`human_review_required=true` en toda la plataforma). `INDETERMINADO` es un resultado
científicamente legítimo y de primera clase.

---

## 1. Qué puede hacer el sistema hoy

| Capacidad | Herramienta MCP |
| --- | --- |
| Diagnóstico del servidor y configuración activa | `tool_server_status` |
| Resolución de faja métrica POSGAR 2007 para cualquier coordenada argentina | `tool_resolve_posgar_crs` |
| Búsqueda STAC y recorte COG (HTTP Range Request) de un par multitemporal comparable | `tool_search_multitemporal_pair` |
| Cambios espectrales ΔMNDWI / ΔBSI / ΔNDVI en grillas nativas 10 m / 20 m | `tool_compute_spectral_changes` |
| Detección de anomalías con controles de dinámica fluvial y log de descartes | `tool_detect_extraction_anomalies` |
| Validación catastral semántica contra padrón de permisos + hidrografía | `tool_validate_permits` |
| Cribado heurístico / evaluación VLM externa / dictamen de perito (modalidades separadas) | `tool_evaluate_vlm_verdict` |
| Expediente técnico reproducible de 9 secciones con figuras de análisis incluidas | `tool_generate_enforcement_report` |
| Expediente administrativo transaccional (SQLite append-only + JSONL encadenado) | `tool_manage_case_workflow` |
| Ficha de caso del protocolo de validación (Apéndice B) | `tool_generate_case_file` |
| Cola de revisión **en ciego** con muestreo estratificado para peritos | `tool_export_blind_review_queue` |
| Diagnóstico de factibilidad del corredor piloto Paraná Medio | `tool_evaluate_parana_pilot` |

**Manual de uso con ejemplos chat → tool → resultado:** [`docs/MANUAL_DE_USO.md`](docs/MANUAL_DE_USO.md).

---

## 2. Marco Metodológico y Fundamento Científico

### 2.1 Problema de Detección en Llanuras Aluviales

Los sistemas fluviales argentinos —en particular el **Río Paraná Medio e Inferior**— presentan una
dinámica hidromorfológica intensa: variaciones de nivel hidrométrico superiores a $4\text{ m}$,
migración continua de barras arenosas, inundación estacional de lagunas isleñas y presencia
simultánea de aguas turbias y aguas negras/húmicas (`CDOM`). Un detector bitemporal ingenuo colapsa
por tres mecanismos físicos documentados en el **Experimento 001**:

- **Singularidad de cocientes en baja reflectancia:** en aguas negras (`B11 < 200 DN`), el
  denominador del BSI tiende a cero y amplifica ruido atmosférico residual.
- **Inundación no lineal de llanura:** por encima de la cota de coronamiento, las cubetas isleñas
  desconectadas del cauce se llenan por desborde o napa, generando ΔMNDWI positivos sin conexión
  superficial con la lámina de T0.
- **Confusores de turbidez y sumersión de barras:** la sumersión de un banco somero brillante
  invierte el signo de ΔB04, burlando filtros unidireccionales.

### 2.2 Formulación Espectral (Sentinel-2 L2A, escala 10000 DN)

$$\text{MNDWI} = \frac{B_{03} - B_{11}}{B_{03} + B_{11} + \epsilon} \qquad
\text{BSI} = \frac{(B_{11}+B_{04})-(B_{08}+B_{02})}{(B_{11}+B_{04})+(B_{08}+B_{02})+\epsilon} \qquad
\text{NDVI} = \frac{B_{08}-B_{04}}{B_{08}+B_{04}+\epsilon}$$

más la variante regularizada por albedo $\text{BSI}_{\text{reg}}(k=800\text{ DN})$ para suprimir
singularidades en aguas negras, y compuertas minerales estrictas (`B11`, `B04`, `B08`, ratio
`B11/B03`) sobre píxeles `SCL` válidos (excluye 0,1,2,3,8,9,10,11).

### 2.3 Reglas de Detección y Controles Naturales

Entre la línea base $T_0$ (escena única o mediana de hasta N escenas en ventana estacional
$\pm 45$ días del DOY) y $T_1$:

1. **Control hidrométrico del tramo:** variación de lámina de agua (`water_stage_delta_pct`),
   anillos costeros de crecida/bajante vía EDT y lecturas oficiales del INA (API A5, con procedencia).
2. **Dipolos de migración natural de barras:** empareja erosión (`ΔMNDWI > +0.35`) con acreción
   compensatoria (`ΔMNDWI < -0.35`) de área comparable a ≤ 450 m.
3. **Normalización fenológica ribereña:** descuenta la deriva mediana estacional de NDVI/BSI sobre
   vegetación densa de T0.
4. **Zonificación geomorfológica en 4 unidades:** `cauce_activo` (≥ 5 ha conexos), `barras_moviles`
   (≤ 300 m del cauce), `planicie_islas` (lagunas y sectores entre brazos) y `ribera_firme`.
5. **Confusor de turbidez/sumersión:** descarta candidatos sobre agua abierta preexistente o con
   pulso rojo (|ΔB04| > 150 DN) o barra somera brillante en T0.
6. **Piso de área diferenciado por resolución nativa:** `≥ 1600 m²` (excavación inundada, SWIR 20 m)
   y `≥ 800 m²` (desmonte/camino, VNIR 10 m), con poligonización por soporte de medio píxel
   (buffer cuadrado de 5 m) y clausura morfológica 3×3.
7. **Auditoría de descartes:** cada componente suprimido queda registrado con motivo tipado
   (`DESCARTADO_*`), ordenado por área, con log completo persistido por par de escenas.

---

## 3. Arquitectura del Repositorio

```text
rio-alerta-mcp/
├── pyproject.toml                        # Dependencias, ruff, mypy, pytest
├── Dockerfile                            # Contenedor reproducible con GDAL/rasterio
├── LICENSE                               # MIT
├── .github/workflows/ci.yml              # CI: ruff + mypy + pytest + docker smoke
├── docs/
│   ├── MANUAL_DE_USO.md                  # ⭐ Manual de uso rápido con ejemplos reales
│   └── validation/
│       ├── PROTOCOL_INDEPENDENT_VALIDATION.md  # Protocolo de validación independiente (Fase 3.2)
│       ├── META_AUDIT_RIO_0001.md        # Meta-auditoría formal P0 (completada)
│       └── meta_audit_rio_0001_results.json
├── src/
│   ├── config.py                         # Settings CWD-safe + 42 parámetros congelados + fajas POSGAR
│   ├── schemas.py                        # Modelos Pydantic v2 estrictos (contratos públicos)
│   ├── server.py                         # Servidor FastMCP: 12 herramientas con confinamiento de rutas
│   └── core/
│       ├── crs_manager.py                # Fajas POSGAR 2007 (EPSG:5343-5349), reproyección, co-registro
│       ├── optical_providers.py          # Cliente STAC (Earth Search/Planetary), COG 10m/20m + SCL
│       ├── hydrological_control.py       # Crecidas/bajantes, dipolos de barras, fenología, INA A5
│       ├── change_detector.py            # Motor multitemporal completo + bitácora de descartes
│       ├── permits_validator.py          # Cruce catastral semántico (vigencia/actividad/unión)
│       ├── vlm_judge.py                  # Modalidades HEURISTIC / EXTERNAL_VLM / HUMAN_EXPERT
│       ├── case_workflow.py              # SQLite WAL append-only + JSONL con cadena SHA-256
│       ├── dossier_generator.py          # Dossier de 9 secciones con dominios declarados
│       ├── figure_generator.py           # Mapas de contexto + chips RGB + comparación T0/T1
│       ├── report_generator.py           # Expediente atómico por generación + manifest SHA-256
│       ├── case_file.py                  # Ficha de caso versionada (protocolo Apéndice B)
│       ├── blind_review_queue.py         # Cola de revisión ciega con muestreo estratificado
│       └── parana_pilot_recon.py         # Diagnóstico de factibilidad del tramo piloto
├── scripts/
│   ├── run_exp_001_parana_medio.py       # Experimento 001 reproducible (Paraná Medio)
│   ├── run_exp_002_validation.py         # Validación cruzada congelada (Exp 001 + Diamante)
│   ├── generate_dossier_rio_0184.py      # Dossier auditable del foco RIO-0184
│   └── verify_audit_inventory.py         # Meta-auditoría P0: verificación M1/M2/R5
├── reports/audits/                       # Evidencia de auditoría independiente (RIO-0001)
├── skills/rio-alerta/SKILL.md            # Skill portable para agentes
└── tests/                                # 13 módulos, 86 tests (sin red, rasters sintéticos)
```

---

## 4. Catálogo completo de Herramientas MCP

### 4.1 Herramientas del Servidor FastMCP (`src/server.py`)

| Herramienta | Parámetros principales | Retorno | Descripción operativa |
| --- | --- | --- | --- |
| `tool_server_status` | — | Dict | Estado, versión, rutas de datos, fajas POSGAR, topes de procesamiento, existencia de `permits.gpkg`. |
| `tool_resolve_posgar_crs` | `lon`, `lat` | Dict | Faja POSGAR 2007 (EPSG:5343–5349) + coordenadas métricas. |
| `tool_search_multitemporal_pair` | `min_lon, min_lat, max_lon, max_lat`, `t1_end_date?`, `lookback_years=3` | `MultitemporalScenePair` | Búsqueda STAC + recorte COG (Range Request) del par T0/T1 estacionalmente comparable, con fallback de ventana y filtro de cobertura MGRS ≥ 98%. |
| `tool_compute_spectral_changes` | `t0_path, t1_path`, `t0/t1_swir_20m_path?` | `SpectralChangeResult` | GeoTIFFs Float32 de ΔMNDWI/ΔBSI/ΔNDVI en POSGAR 2007 + variación hidrométrica del tramo. |
| `tool_detect_extraction_anomalies` | ídem + `min_area_m2=1600`, `min_clearing_area_m2=800`, `stage_t0_m?/t1_m?`, `max_discarded_records=200` | `ChangeDetectionResult` | Motor completo: máscaras SCL, cinturones de crecida, dipolos, fenología, compuertas minerales, segmentación con buffer de medio píxel, log de descartes con motivo, `candidate_uid` determinista y `coarse_location_uid`. |
| `tool_evaluate_parana_pilot` | `offline_mode=True` | Dict | Factibilidad geodésica/STAC/institucional del corredor piloto. |
| `tool_validate_permits` | `anomaly_dict, t1_date`, `permits_gpkg_path?`, `cadastre_status`, `authority_name?`, `excess_tolerance_pct?`, `query_remote_hydrography?` | `PermitValidationResult` | Cruce espacial-temporal semántico: vigencia estricta (sin fechas sintéticas), actividad extractiva compatible por tokens estrictos, unión geométrica sin doble conteo, degradación de procedencia no verificable. |
| `tool_evaluate_vlm_verdict` | `anomaly_dict`, `evaluation_mode`, `verdict?`, `model_id?`, `evaluator_id?`, `image_refs?`, ... | `VLMFluvialVerdict` | Tres modalidades separadas con requisitos estrictos y hashes SHA-256 de imágenes; `certainty_pct` declarado como no-probabilidad. |
| `tool_generate_enforcement_report` | `anomaly_dict, t0/t1_scene_id, t0/t1_date`, `t0/t1_vnir_10m_path?`, `multitemporal_series?`, `output_dir?`, `open_case_workflow=True`, ... | `EnforcementReportResult` | Expediente atómico: `report.md` (9 secciones + **figuras de análisis auto-generadas**), `report.json` con provenance, `manifest.json` con SHA-256 y `current_generation.json`. |
| `tool_manage_case_workflow` | `action: open/append_event/get/list`, `case_id?`, `action_type?`, `idempotency_key?`, ... | Dict | Expediente administrativo SQLite append-only con cadena de hashes, idempotencia por clave y estados tipados (incluye cierre `CAMBIO_REAL_INDETERMINADO`). |
| `tool_generate_case_file` | `anomaly_dict`, `case_id?`, `output_dir?`, `scenes?`, `hypotheses?`, `review?`, `verification?` | Dict | Ficha de caso versionada (`phase3.2.v1`) del protocolo de validación; los campos humanos quedan pendientes hasta que la institución los complete. |
| `tool_export_blind_review_queue` | `source_geojson_path` o `anomaly_dicts`, `min_per_stratum=20`, `seed=2026`, `discarded_log_path?`, `chips_by_candidate?` | Dict | Cola de revisión en ciego: worksheet sin campos interpretativos, IDs ciegos `REV-<hash>`, manifiesto privado bajo custodia del coordinador y plantilla de acta. |

### 4.2 Funciones del Núcleo (`src/core/`)

- **Geodesia:** `get_posgar_faja`, `bbox_wgs84_to_posgar`, `ensure_metric_crs`, `reproject_array_to_grid`, `reproject_geometry`, `align_raster_to_reference`.
- **Ingesta óptica:** `load_native_s2_scene_bundle`, `filter_seasonally_comparable_items`, `query_stac_seasonal_pair_candidates`, `search_and_fetch_stac_multitemporal`, `decode_scl_masks`.
- **Detección:** `compute_spectral_indices`, `compute_regularized_bsi`, `compute_multitemporal_baseline_and_changes`, `detect_extraction_anomalies`.
- **Dinámica fluvial:** `compute_reach_hydrological_state`, `classify_aoi_geomorphology`, `detect_natural_bar_migration_dipoles`, `normalize_seasonal_riparian_indices`, `compute_turbidity_confounder_flag`, `fetch_ina_stage_height_with_provenance`.
- **Catastro:** `validate_anomaly_permits`, `validate_posgar_epsg`.
- **Evaluación:** `evaluate_candidate_vlm_verdict`, `build_vlm_fluvial_prompt`.
- **Expedientes:** `generate_candidate_dossier`, `generate_negative_or_area_dossier`, `generate_enforcement_report`, `verify_report_generation_integrity`, `CaseWorkflowManager` (open/append/get/list/verify/rebuild).
- **Fase 3.2:** `build_case_file`, `write_case_file`, `sample_stratified`, `build_blind_queue`, `write_blind_queue`, `generate_report_figures`, `verify_inventory` (meta-auditoría).

---

## 5. Configuración y Parámetros Congelados

Todos los umbrales operativos viven en `src/config.py` (`Settings`, env `.env`) y se exportan
íntegros como **provenance** en cada corrida y expediente (`metadata.calibration_parameters`,
42 parámetros). Los grupos principales:

| Grupo | Ejemplos |
| --- | --- |
| Detección | `min_anomaly_area_m2=1600`, `min_clearing_area_m2=800`, `flooded_min_delta_mndwi=0.40`, `clearing_min_delta_bsi_adj=0.25` |
| Agua oscura / mineral | `dark_water_min_b11_dn=600`, `bsi_reg_albedo_k=800`, `scl7_min_b11_dn=850` |
| Hidrología | `hydrometric_stage_rise_alert_m=0.80`, `water_stage_warning_pct=8.0`, `shoreline_change_min_fraction=0.25` |
| Geomorfología / dipolos | `geomorph_bar_max_dist_m=300`, `geomorph_lagoon_buffer_m=450`, `dipole_min_abs_delta_mndwi=0.35`, `dipole_max_area_ratio=2.50` |
| Límites de procesamiento | `max_master_side_px=4096`, `max_cloud_cover_pct=20`, `seasonal_window_days=45`, `cloud_shadow_buffer_m=40` |

Ver `.env.example` para las variables de entorno (STAC, directorios, límites). **En validación con
parámetros congelados no se ajusta contra el AOI independiente de Diamante** (ver protocolo).

---

## 6. Integridad, Trazabilidad y Auditoría

- **Expedientes:** publicación atómica por generación (`staging` + `fsync` + `os.replace`);
  `manifest.json` con SHA-256 de `report.md`/`report.json` y de las figuras; verificación de
  integridad con `verify_report_generation_integrity`.
- **Workflow de casos:** SQLite (`WAL`, `synchronous=FULL`) con transacciones `BEGIN IMMEDIATE`,
  bloqueo `flock`, triggers **append-only** (UPDATE/DELETE abortados), exportación atómica a JSONL
  con cadena SHA-256 encadenada, `idempotency_key` con índice único parcial, verificación de
  integridad y reconstrucción exacta desde JSONL.
- **Bitácora de descartes:** cada candidato suprimido por controles naturales/SCL/área queda
  registrado con motivo tipado; log completo persistido por par de escenas.
- **Seguridad MCP:** confinamiento de rutas (sin `..`, sin `~`, sin symlinks, raíces de
  lectura/escritura diferenciadas, extensiones permitidas), validación ISO de fechas y rangos.
- **Meta-auditoría (P0):** `scripts/verify_audit_inventory.py` verifica hashes (M1), escenas contra
  STAC (M2) y el esquema de provenance metodológica R5. Informe formal:
  [`docs/validation/META_AUDIT_RIO_0001.md`](docs/validation/META_AUDIT_RIO_0001.md).

---

## 7. Verificación de Calidad

```bash
uv venv .venv --python 3.11
uv pip install -e ".[dev]"

.venv/bin/ruff check src tests scripts   # lint
.venv/bin/mypy src                        # tipos (18 archivos)
.venv/bin/pytest -v                       # 86 tests
```

Los tests usan **rasters Sentinel-2 sintéticos** en `tmp_path`; no requieren red ni servicios
STAC/INA. CI (GitHub Actions): micromamba con stack geoespacial → ruff → mypy → pytest →
docker smoke. Triggers: `main`, `feat/**`, `fix/**`, `chore/**`, `docs/**` y PRs a `main`.

---

## 8. Ejecución

### 8.1 Servidor MCP (stdio)

```bash
.venv/bin/python -m src.server
```

Configuración del cliente (Claude Desktop / OpenCode / Antigravity / Cursor):

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

### 8.2 Experimentos reproducibles (requieren red STAC)

```bash
.venv/bin/python scripts/run_exp_001_parana_medio.py    # Paraná Medio, par 2024→2026
.venv/bin/python scripts/run_exp_002_validation.py      # Validación cruzada congelada + Diamante
```

### 8.3 Meta-auditoría de una auditoría

```bash
.venv/bin/python scripts/verify_audit_inventory.py \
  --audit-dir reports/audits/RIO-0001 --stac --strict
```

---

## 9. Validación Empírica

- **Exp 001 (Paraná Medio, 13.817 ha, EPSG:5347):** par `2024-11-06` (estiaje, 0.67 m INA) →
  `2026-11-08` (crecida, 4.85 m INA; +26,29% lámina). La Fase 2.1 recalibrada **recupera 4/4 los
  focos costeros de referencia** (`RIO-0184`, `RIO-0225`, `RIO-0199`, `RIO-0365`) suprimiendo las
  dos familias de falsos positivos (agua húmica y lagunas isleñas desconectadas).
- **Exp 002 (validación congelada):** regresión recalibrada (13→34 candidatos), estiajes
  comparables AOI 1 (2024→2025, 2023→2026) y AOI 2 **independiente** Diamante (2023→2025).
  Resumen en `data/experiments/exp_002_validation/exp_002_summary.json` con los 42 parámetros
  congelados.
- **Auditoría independiente RIO-0001:** dictamen `INDETERMINADO` (cambio real y persistente
  2023–2026 de 5.065 m² sobre planicie inundable; H1 extracción y H3 dinámica fluvial ambas
  plausibles a 10/20 m). Verificación en [`reports/audits/RIO-0001/`](reports/audits/RIO-0001/AUDIT_RIO_0001.md).

---

## 10. Protocolo de Validación Independiente (Fase 3.2)

Estado del plan **P0–P5** (detalles en
[`docs/validation/PROTOCOL_INDEPENDENT_VALIDATION.md`](docs/validation/PROTOCOL_INDEPENDENT_VALIDATION.md)):

| Fase | Contenido | Estado |
| --- | --- | --- |
| P0 | Meta-auditoría formal (M1–M6, script automatizado) | ✅ Completada |
| P1 | Correcciones del dossier C1–C5 + tests de regresión R1–R5 | ✅ Completada |
| P2 | Estado `CAMBIO_REAL_INDETERMINADO`, ficha de caso versionada, dominio SCL | ✅ Completada |
| P3 | Cola de revisión en ciego (muestreo estratificado + acta) | ✅ Completada |
| P4 | Muestreo piloto con revisores humanos independientes | ⏳ Requiere institución |
| P5 | Métricas de rendimiento sobre referencia sancionada | ⏳ Requiere catastro/VHR/campo |

Regla estructural del conjunto de referencia: **etiqueta física e hipótesis, no legalidad**.
Métricas de rendimiento no se computan hasta que exista referencia sancionada (guard §5.7 del protocolo).

---

## 11. Hoja de Ruta (Histórico de Fases)

- **Fase 1 ✅:** Scaffolding FastMCP, schemas Pydantic v2, config CWD-safe, fajas POSGAR 2007, Dockerfile y CI.
- **Fase 2 ✅:** Proveedor STAC Sentinel-2 L2A (10 m/20 m nativos + SCL), motor multitemporal y controles de dinámica fluvial.
- **Fase 2.1 ✅:** Corrección de agua oscura (`BSI_reg` + compuertas minerales), zonificación geomorfológica, hidrómetros INA, piso diferenciado 1600/800 m², buffer de medio píxel y **recuperación 4/4** de focos de referencia.
- **Fase 3 ✅:** Validación catastral, dictamen VLM/perito, workflow JSONL+SQLite, expedientes reproducibles y 10 herramientas MCP.
- **Fase 3.1 ✅:** Seguridad semántica catastral, integridad transaccional SQLite+JSONL, separación de modalidades, publicación atómica, confinamiento de rutas MCP, endurecimiento de tokens de actividad y `expert_declaration_recorded`.
- **Fase 3.2 ✅ (P0–P3):** Protocolo de validación independiente, meta-auditoría, guardas de regresión del dossier, ficha de caso y cola de revisión en ciego, figuras automáticas en expedientes.
- **Pendiente institucional:** P4–P5 (revisores, catastros oficiales, evidencia VHR/campo), catastro de concesiones en producción y panel de priorización operativa.

---

## 12. Advertencia Institucional

Las geometrías y dictámenes producidos son **anomalías morfológicas candidatas** y apoyos de
investigación. No constituyen por sí solos infracción, extracción ilegal ni prueba judicial sin:
(a) cruce contra catastro oficial de concesiones, (b) evaluación de especialista humano acreditado,
y (c) constatación presencial por autoridad competente. Todo artefacto de salida declara esta
limitación (`institutional_note`, disclaimers y `human_review_required=true`).

## Licencia

MIT — ver [LICENSE](LICENSE).
