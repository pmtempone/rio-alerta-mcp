# Experimento 001 — Validación Real de RíoAlerta (Fase 2) en el Río Paraná Medio

**Fecha de ejecución:** 2026-11-14  
**Estado del proyecto:** Validación intermedia con imágenes Sentinel-2 L2A reales (previa a Fase 3)  
**Motor evaluado:** `src/core/change_detector.py` y `src/core/hydrological_control.py` (sin modificaciones algorítmicas respecto al commit `f2e4972`)  
**Script reproducible:** [run_exp_001_parana_medio.py](file:///Users/pablotempone/rio-alerta-mcp/scripts/run_exp_001_parana_medio.py)  
**Metadatos completos:** [experiment_summary.json](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/experiment_summary.json)

---

## 1. Síntesis Ejecutiva

El primer experimento sobre imágenes Sentinel-2 L2A reales del tramo **Santa Fe (Canal de Acceso / Colastiné) – Islas del Paraná – Bajada Grande / Paraná** confirma dos hechos fundamentales para la madurez técnica de RíoAlerta:

1. **Capacitación operativa real de ingesta y trazabilidad:** El proveedor STAC, la lectura por ventanas COG en `EPSG:5347` (POSGAR 2007 Faja 5), la preservación de resoluciones nativas (`10 m` VNIR / `20 m` SWIR+SCL) y la vectorización métrica funcionan de extremo a extremo sobre escenas reales. Durante la etapa de selección se detectó y corrigió una falla de ingesta por **borde de celda MGRS** (`MGRS-20JQL` cubría solo el `65.8%` del AOI y dejaba el `34.2%` sur con `NoData`, frente al `100%` cubierto por `MGRS-20HQK`), sin alterar el algoritmo de detección.
2. **Brecha crítica entre pruebas sintéticas y dinámica real de llanura aluvial:** Con los parámetros actuales de Fase 2 sin calibrar frente a una crecida real del Paraná (**+26.29% de lámina de agua abierta** entre noviembre 2024 y noviembre 2026), el detector generó **452 polígonos candidatos (542.58 ha)** en modo bitemporal y **476 polígonos (752.41 ha)** en modo mediana multianual de 3 años. La auditoría visual y espectral demuestra que **más del 95% de esas detecciones corresponden a falsos positivos hidrológicos y espectrales**, concentrados en dos mecanismos físicos concretos no capturados por los escenarios sintéticos:
   - **Inundación de lagunas isleñas internas y bancos anchos** fuera del alcance geométrico de `uniform_stage_belt_mask`.
   - **Singularidad del índice BSI en aguas negras/húmicas de muy baja reflectancia** (`B08 ≈ 0` y `B11_T1 < 700 DN`, reflectancia `< 1.5%`), que hace que `264 de 275` polígonos clasificados como `desmonte_ribera_acopio` / `apertura_camino_acceso` sean en realidad **agua oscura o vegetación palustre inundada** (`SCL = 6` o `7`).
3. **Señal positiva rescatable para inspección humana:** Al aislar los **11 polígonos con reflectancia SWIR real de suelo seco brillante (`B11_T1 >= 700 DN`)**, se identificaron cambios reales en bordes antrópicos y ribera firme (p. ej., `#218 RIO-0184` junto a playón/cava costera en Bajada Grande, `#322 RIO-0225` sobre ribera norte de Paraná y `#389 RIO-0365` junto al corredor vial de Ruta Nacional 168).

> **Conclusión operativa:** No corresponde avanzar todavía a Fase 3 (cruce catastral automático, Juez VLM y actas legales) sin calibrar primero los controles físicos identificados en este informe.

---

## 2. Etapa 1 — Área de Estudio (AOI) y Selección de Escenas

### 2.1 Ubicación Geográfica y Dimensiones

- **Tramo seleccionado:** Corredor fluvial del Río Paraná Medio entre el borde oriental de Alto Verde / Canal de Acceso Santa Fe / Río Colastiné, el sistema de islas y madrejones del cauce principal, y la ribera de Bajada Grande y frente costero noroeste de la ciudad de Paraná.
- **Bounding Box WGS84 (`EPSG:4326`):** `(-60.66, -31.74, -60.53, -31.64)`
- **Bounding Box métrico efectivo POSGAR 2007 / Argentina 5 (`EPSG:5347`):**
  - `minx = 5437391.58 m`, `miny = 6488754.39 m`, `maxx = 5449777.63 m`, `maxy = 6499910.04 m`
- **Dimensiones de grilla:** `12.39 km × 11.16 km` (`1,239 × 1,116 píxeles` a `10 m`; `620 × 558 píxeles` nativos SWIR/SCL a `20 m`).
- **Superficie total del AOI:** **`138.17 km²` (`13,817.45 ha`)**.

### 2.2 Escenas Sentinel-2 L2A Seleccionadas y Calidad Verificada

- **Catálogo STAC:** Element 84 Earth Search v1 (`https://earth-search.aws.element84.com/v1`), colección `sentinel-2-l2a`.
- **Celda MGRS única con 100% de cobertura sobre el AOI:** `MGRS-20HQK` (`boa_offset_applied = True`, reflectancia de superficie `uint16` escalada por `0.0001` con offset ya descontado).

| Rol | ID Producto STAC Sentinel-2 L2A | Fecha UTC | Diferencia Estacional ($\Delta$ DOY vs $T_1$) | Nubosidad Escena (`eo:cloud_cover`) | Píxeles Válidos SCL en AOI | Agua `SCL=6` en AOI | Suelo Desnudo `SCL=5` | Vegetación `SCL=4` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **$T_1$ (Actual)** | `S2A_20HQK_20261108_1_L2A` | `2026-11-08 14:11:38Z` | Referencia | `7.78%` | `98.81%` (`98.94%` 20m nat.) | `32.7%` | `8.3%` | `44.2%` |
| **$T_0$ Principal** | `S2A_20HQK_20241106_0_L2A` | `2024-11-06 14:11:28Z` | `2 días` | `1.70%` | `99.94%` (`99.95%` 20m nat.) | `23.1%` | `7.8%` | `68.1%` |
| **$T_0$ Base Año -3** | `S2B_20HQK_20231107_0_L2A` | `2023-11-07 14:11:35Z` | `1 día` | `0.11%` | `99.97%` | `23.8%` | `9.1%` | `57.2%` |
| **$T_0$ Base Año -1** | `S2A_20HQK_20251113_1_L2A` | `2025-11-13 14:11:47Z` | `5 días` | `11.33%` | `99.29%` | `23.3%` | `7.4%` | `67.9%` |

**Criterio de comparabilidad:** Todas las escenas corresponden estrictamente a la primera quincena de noviembre ($\Delta\text{DOY} \le 5$ días), eliminando sesgos por ángulo solar o estacionalidad astronómica, y presentan `>98.8%` de píxeles libres de nubes y sombras dentro del AOI. Las pequeñas nubes presentes en $T_1$ (~`1.2%` del AOI en entrada noroeste de la RN 168 y borde urbano sur de Paraná) quedaron correctamente enmascaradas por `SCL`.

---

## 3. Etapa 2 — Corrección Documentada de Ingesta y Parámetros de Ejecución

### 3.1 Corrección mínima aplicada durante la ingestión (sin alterar el detector)

Durante la consulta inicial en tiempo real, el corredor `(-60.66, -31.74, -60.53, -31.64)` intersectaba tanto la celda norte `MGRS-20JQL` (límite sur `-31.7058°`) como la celda `MGRS-20HQK` (límite norte `-31.5933°`). Sin filtro de cobertura completa, `query_stac_seasonal_pair_candidates` elegía `S2B_20JQL_20261111_0_L2A` porque su fecha era 3 días más reciente, truncando con `NoData` el `34.2%` inferior del AOI (franja de Paraná y Bajada Grande).

- **Solución implementada:** Se agregaron `_item_bbox_coverage_fraction()` y `_item_covers_bbox(min_coverage=0.98)` en [optical_providers.py](file:///Users/pablotempone/rio-alerta-mcp/src/core/optical_providers.py) para priorizar ítems que cubran íntegramente el AOI, y los campos `t0_swir_raster_path` / `t1_swir_raster_path` en [schemas.py](file:///Users/pablotempone/rio-alerta-mcp/src/schemas.py).
- **Integridad algorítmica:** No se modificó ninguna línea de cálculo de índices, umbrales ni filtros hidrológicos en `src/core/change_detector.py` ni en `src/core/hydrological_control.py`.

### 3.2 Parámetros del Motor Ejecutado

- **Grilla de salida:** `10.0 m` (`9.997 m` en `EPSG:5347`), bandas SWIR (`B11, B12`) remuestreadas con `bilinear` desde `20.0 m` nativos; `SCL` remuestreado con `nearest` desde `20.0 m` nativos.
- **Umbrales de cambio:**
  - `frente_excavacion_inundado`: `ΔMNDWI > +0.40` y `MNDWI_T1 > +0.20` (excluyendo `uniform_stage_belt_mask`, `bar_migration_mask` y confusor de turbidez `ΔB04 > +150` en agua previa).
  - `desmonte_ribera_acopio` / `apertura_camino_acceso` (`elongación >= 4.0`): `ΔBSI_adj > +0.25`, `ΔNDVI_adj < -0.30`, `BSI_T1 > -0.02`, `MNDWI_T1 < +0.10` (excluyendo cinturones de crecida/bajante).
- **Piso mínimo de polígono:** `min_anomaly_area_m2 = 1600.0 m²` (`4` píxeles SWIR nativos de `20 m`).

### 3.3 Resultados Cuantitativos Comparados (Corrida Bitemporal vs Mediana 3 Años)

| Métrica del Tramo | Corrida A: Bitemporal (`2024-11-06 -> 2026-11-08`) | Corrida B: Mediana 3 años (`2023+2024+2025 -> 2026-11-08`) |
| --- | --- | --- |
| **Fracción válida combinada (`SCL`)** | `98.77%` | `98.81%` |
| **Lámina de agua `MNDWI > 0.15` ($T_0 \to T_1$)** | `22.9%` $\to$ `28.9%` (`+26.29%`) | `22.9%` $\to$ `28.7%` (`+25.15%`) |
| **`shoreline_flooding_fraction` detectada** | `0.350` ($\ge 0.35$) | `0.314` ($< 0.35$) |
| **`is_systemic_stage_rise` activado** | `True` (`18,926 px` filtrados como cinturón uniforme) | **`False`** (`0 px` filtrados como cinturón uniforme) |
| **Dipolos migración bancos filtrados** | `19 pares` | `22 pares` |
| **Deriva fenológica vegetal (`riparian_ndvi_shift`)** | `-0.0797` (`bsi_shift = -0.0285`) | `-0.0373` (`bsi_shift = -0.0454`) |
| **`frente_excavacion_inundado`** | `177` polígonos (`329.58 ha`) | `220` polígonos (`517.19 ha`) |
| **`desmonte_ribera_acopio`** | `255` polígonos (`199.05 ha`) | `235` polígonos (`222.29 ha`) |
| **`apertura_camino_acceso`** | `20` polígonos (`13.95 ha`) | `21` polígonos (`12.92 ha`) |
| **Total polígonos candidatos** | **`452` polígonos (`542.58 ha`)** | **`476` polígonos (`752.41 ha`)** |

---

## 4. Etapa 3 — Productos Visuales y Vectoriales Generados

Todos los productos se encuentran almacenados en [data/experiments/exp_001_parana_medio](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio):

- **Imagen $T_0$ (`2024-11-06`):** [01_imagen_t0_rgb.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/01_imagen_t0_rgb.png)
- **Imagen $T_1$ (`2026-11-08`):** [02_imagen_t1_rgb.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/02_imagen_t1_rgb.png)
- **Comparación Antes/Después con máscara SCL:** [03_comparacion_antes_despues_t0_t1.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/03_comparacion_antes_despues_t0_t1.png)
- **Mapa triple de diferencias espectrales ($\Delta$MNDWI, $\Delta$BSI_adj, $\Delta$NDVI_adj):** [04_mapa_diferencias_espectrales.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/04_mapa_diferencias_espectrales.png)
- **Máscara diagnóstica de filtros hidrológicos vs residuales:** [05_mascara_cambios_detectados.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/05_mascara_cambios_detectados.png)
- **Polígonos vectoriales completos:** [06_anomalias_detectadas.geojson](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/06_anomalias_detectadas.geojson) y `06_anomalias_detectadas.gpkg` (+ corrida mediana multianual)
- **Mapa final numerado (Top-25 por área + focos costeros):** [07_mapa_final_anomalias_numeradas.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/07_mapa_final_anomalias_numeradas.png)
- **Inspección de recortes (Top-6 por área):** [08_recortes_zoom_anomalias_principales.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/08_recortes_zoom_anomalias_principales.png)
- **Inspección de recortes (Focos costeros con suelo seco brillante real `B11_T1 >= 700`):** [09_recortes_zoom_sospechosos_ribera.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/09_recortes_zoom_sospechosos_ribera.png)

---

## 5. Etapa 4 — Evaluación Crítica y Clasificación de Anomalías

El contraste directo entre la imagen RGB $T_0$ (estiaje relativo, bancos expuestos y cubetas isleñas secas/fangosas), la imagen RGB $T_1$ (pulso de creciente con inundación generalizada de bañados e islas), los recortes individuales (`08` y `09`) y los valores espectrales crudos por polígono permite clasificar objetivamente las **452 detecciones** de la corrida principal:

### 5.1 Distribución Espectral Global de los 452 Polígonos Detectados

| Tipo Asignado por Fase 2 | `SCL_T1` Dominante | Cantidad | Área (ha) | Mediana `B11_T1` (SWIR1) | Mediana `B08_T1` (NIR) | Diagnóstico Físico Real |
| --- | --- | --- | --- | --- | --- | --- |
| `frente_excavacion_inundado` | `6` (Agua) | `146` | `313.24` | `121 DN` | `131 DN` | Inundación natural de bañados/lagunas isleñas y bancos bajos |
| `frente_excavacion_inundado` | `7` / `5` (Mixto/Orilla) | `31` | `16.34` | `254–275 DN` | `351–368 DN` | Bordes inundados de bañados / interfase húmeda |
| `desmonte_ribera_acopio` | `6` (Agua) | `94` | `119.00` | `125 DN` | `48 DN` | **Falso positivo:** agua negra/húmica muy oscura (`<1.3%` refl.) |
| `desmonte_ribera_acopio` | `7` / `5` / `4` (`B11<700`) | `152` | `78.47` | `151–261 DN` | `89–215 DN` | **Falso positivo:** pastizal/bosque inundado oscuro o sombra palustre |
| `apertura_camino_acceso` | `6` / `7` / `5` (`B11<700`) | `18` | `13.39` | `199–260 DN` | `17–234 DN` | **Falso positivo:** paleocauces/albardones someros inundados alargados |
| `desmonte + camino` **`B11_T1 >= 700`** | `5` / `4` / `7` (Suelo seco) | **`11`** | **`2.78`** | **`874–2839 DN`** | **`471–3054 DN`** | **Suelo desnudo / movimiento real de material brillante en $T_1$** |

### 5.2 Evaluación Detallada de Anomalías Principales y Focos Costeros

Se evalúan los **Top-10 por superficie** y los **focos costeros de suelo brillante (`B11_T1 >= 700`)** usando exclusivamente las 7 categorías solicitadas, sin afirmar actividad ilegal en ningún caso:

| # / ID | Tipo Fase 2 | Sup. (ha) | Coord. WGS84 (Lon, Lat) | Índices $\Delta$ / $T_1$ | Nivel de Incertidumbre y Limitación Principal | Clasificación Pericial Crítica | Justificación Visual y Espectral |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `#1 RIO-0170` | `frente_excavacion_inundado` | `27.47` | `-60.5339, -31.6554` | $\Delta\text{MNDWI}=+0.80$, $\Delta\text{NDVI}=-1.33$, `B11_T1=19`, `SCL=6` | Confounder hidrométrico `+26.3%` (`score=0.25`); resolución SWIR `20 m` | **Cambio hidrológico natural** | Cubeta/laguna isleña previamente seca/salitrosa en $T_0$ (mancha beige en `01` y `08`) completamente llenada por agua negra de creciente en $T_1$. No conectada a `water_t0`. |
| `#2 RIO-0067` | `frente_excavacion_inundado` | `23.71` | `-60.6096, -31.6940` | $\Delta\text{MNDWI}=+0.99$, $\Delta\text{NDVI}=-0.81$, `B11_T1=132`, `SCL=6` | Confounder hidrométrico `+26.3%` (`score=0.25`); forma alargada natural (`rect=0.46`) | **Cambio hidrológico natural** | Depresión lagunar isleña al norte del canal principal que pasó de planicie vegetada/húmeda en $T_0$ a espejo de agua en $T_1$. |
| `#3 RIO-0153` | `frente_excavacion_inundado` | `16.39` | `-60.5952, -31.6680` | $\Delta\text{MNDWI}=+1.10$, $\Delta\text{NDVI}=-1.44$, `B11_T1=52`, `SCL=6` | Contigua a albardón y terraplén RN 168; confounder `+26.3%` | **Cambio hidrológico natural** | Bajo triangular encerrado junto al cauce/puente Colastiné (`NDVI_T0=+0.84` en `T0`) inundado íntegramente en $T_1$. Borde neto por albardón topográfico, no por frente de draga. |
| `#4 RIO-0131` | `frente_excavacion_inundado` | `15.50` | `-60.5621, -31.6766` | $\Delta\text{MNDWI}=+0.99$, $\Delta\text{NDVI}=-0.78$, `B11_T1=122`, `SCL=6` | Confounder hidrométrico `+26.3%`; geometría dendrítica (`rect=0.50`) | **Cambio hidrológico natural** | Bajo inundado al norte del trazo vial isleño RN 168; morfología sinuosa de antiguo curso meandriforme. |
| `#5 RIO-0429` | `desmonte_ribera_acopio` | `13.72` | `-60.5600, -31.6483` | $\Delta\text{BSI}=+0.57$, $\Delta\text{NDVI}=-1.24$, **`B11_T1=56`, `SCL=6`** | **Artefacto espectral SWIR en agua oscura** (`B08_T1=1 DN`) | **Cambio hidrológico natural** | En RGB $T_1$ (`08` fila 5) es agua negra profunda de creciente bajo bosque/esteros (`SCL=6`). No existe suelo desnudo ni acopio alguno. |
| `#6 RIO-0041` | `frente_excavacion_inundado` | `11.73` | `-60.6120, -31.7082` | $\Delta\text{MNDWI}=+0.97$, $\Delta\text{NDVI}=-0.80$, `B11_T1=150`, `SCL=6` | Confounder hidrométrico `+26.3%`; muy baja rectangularidad (`rect=0.22`) | **Cambio hidrológico natural** | Madrejon fluvial paralelo a la costa de isla principal cubierto por agua turbia/oscura de creciente en $T_1$. |
| `#7 RIO-0168` | `frente_excavacion_inundado` | `11.08` | `-60.6202, -31.6546` | $\Delta\text{MNDWI}=+0.88$, `B11_T1=146`, `SCL=6` | **`cloud_shadow_adjacent=True`** (`score=0.60`), adyacente a nube en $T_1$ | **Cambio hidrológico natural** *(con adyacencia a nube)* | Estero isleño inundado al oeste del puente Colastiné, con borde norte lindante (`<=40 m`) a nube enmascarada por `SCL` en $T_1$. |
| `#8 RIO-0118` | `frente_excavacion_inundado` | `10.46` | `-60.5769, -31.6791` | $\Delta\text{MNDWI}=+0.90$, `B11_T1=240`, `SCL=6` | Confounder hidrométrico `+26.3%`; `rect=0.58`, `elong=4.18` | **Cambio hidrológico natural** | Albardón/estero bajo al sur del viaducto RN 168 cubierto por lámina de agua en $T_1$. |
| `#10 RIO-0215` | `desmonte_ribera_acopio` | `8.75` | `-60.6328, -31.7147` | $\Delta\text{BSI}=+0.55$, $\Delta\text{NDVI}=-0.92$, **`B11_T1=71`, `SCL=6`** | **Artefacto espectral SWIR en agua oscura** (`B08_T1=18 DN`) | **Cambio hidrológico natural** | Estero isleño vegetado en $T_0$ inundado con agua negra en $T_1$; disparó `desmonte` por colapso NIR/BSI. |
| `#15 RIO-0027` | `frente_excavacion_inundado` | `6.12` | `-60.5319, -31.7128` | $\Delta\text{MNDWI}=+0.67$, `MNDWI_T0=+0.17` $\to$ `+0.87`, `B04 988->641` | Confounder hidrométrico `+26.3%`; margen este del cauce principal | **Migración de bancos de arena** / **Cambio hidrológico natural** | Se verifica en `09` fila 5: gran banco de arena blanco/amarillento expuesto junto a la costa norte de Paraná en $T_0$ que quedó sumergido bajo agua turbia en $T_1$. El filtro de turbidez no lo captó por signo negativo en $\Delta B04$. |
| **`#218 RIO-0184`** | `desmonte_ribera_acopio` | **`0.48`** | `-60.5634, -31.7284` | $\Delta\text{BSI}=+0.41$, $\Delta\text{NDVI}=-0.43$, **`B11_T1=1573`, `SCL=4->5`** | Superficie pequeña (`4,797 m²`, ~12 px SWIR); sin catastro cargado | **Posible extracción o movimiento de áridos** *(requiere inspección humana)* | En `09` fila 1 se observa directamente en Bajada Grande/Paraná una gran playa/cava brillante preexistente en $T_0$ cuyo borde occidental vegetado fue removido/cubierto por suelo o árido brillante en $T_1$ (`B11=1573`). |
| **`#322 RIO-0225`** | `desmonte_ribera_acopio` | **`0.27`** | `-60.5696, -31.7065` | $\Delta\text{BSI}=+0.40$, $\Delta\text{NDVI}=-0.54$, **`B11_T1=1911`, `SCL=4->5`** | Baja superficie (`2,698 m²`, ~7 px SWIR); mixto borde agua/tierra 20m | **Posible extracción o movimiento de áridos** *(o **Indeterminado** — obra costera)* | Directamente sobre la punta ribereña norte de Paraná/Bajada Grande (`09` fila 2): franja de ribera verde en $T_0$ reemplazada por superficie muy brillante en SWIR/Rojo en $T_1$ (compatible con playa de descarga/acopio ribereño o defensa costera). |
| **`#380 RIO-0199`** | `desmonte_ribera_acopio` | **`0.21`** | `-60.5695, -31.7177` | $\Delta\text{BSI}=+0.36$, $\Delta\text{NDVI}=-0.37$, **`B11_T1=2676`, `SCL=4->5`** | Entorno periurbano/loma de Bajada Grande; `0.21 ha` (~5 px SWIR) | **Cambio de vegetación** / **Indeterminado** | Pérdida puntual de cobertura vegetal sobre parcela de alta reflectancia (`09` fila 3), probablemente vinculada a obra urbana, loteo o movimiento de suelo terrestre fuera del cauce. |
| **`#389 RIO-0365`** | `desmonte_ribera_acopio` | **`0.21`** | `-60.6111, -31.6575` | $\Delta\text{BSI}=+0.31$, $\Delta\text{NDVI}=-0.40$, **`B11_T1=2839`, `SCL=4->5`** | Adyacente a traza pavimentada RN 168; `0.21 ha` | **Indeterminado** *(obra vial / acopio lineal)* | En `09` fila 4 se ubica pegado a la banquina del terraplén de la Ruta Nacional 168: exposición de suelo/agregado muy brillante en obra vial o relleno. |
| `#293 RIO-0395`, `#349 RIO-0239` | `apertura_camino_acceso` | `0.32` / `0.24` | Alto Verde / Santa Fe oeste | $\Delta\text{BSI}=+0.35/+0.45$, `elong=5.8/6.0`, `B11_T1=1123/746` | Muy angostos (`~1-2 px` de ancho SWIR); efecto mixto borde | **Cambio de vegetación** / **Indeterminado** | Trazas elongadas en bordes urbanos/isleños de la ribera occidental (`SCL=4` mixto), requieren verificación VHR para confirmar si corresponden a huellas/caminos nuevos o mantenimiento agrícola/costero. |
| Corrida Mediana `#1 RIO-0038` | `frente_excavacion_inundado` | `72.70` | `-60.5884, -31.7296` | $\Delta\text{MNDWI}=+1.06$, `B11_T1` bajo agua, `SCL=6` | En Mediana 3 años `shoreline_flooding_fraction=0.314 < 0.35` apagó el filtro | **Cambio hidrológico natural** | Gran banco de arena/punta seca al sur del cauce principal en $T_0$ (`01`) completamente inundada por la crecida de $T_1$ (`02`). |

*(Nota sobre otras categorías evaluadas: no se identificaron falsos positivos dominados directamente por nubes no enmascaradas en los primeros puestos gracias al enmascaramiento SCL, salvo `#7 RIO-0168` con borde adyacente `<=40 m` a nube SCL — **Artefacto atmosférico o satelital** descartado correctamente en el interior de la nube pero presente como riesgo de borde en `10` polígonos del total.)*

---

## 6. Limitaciones Reales Observadas y Falsos Positivos Potenciales

Este experimento demostró en terreno tres limitaciones estructurales críticas que deben corregirse antes de integrar Fase 3:

1. **Singularidad matemática de `BSI` y `MNDWI` sobre aguas negras de baja reflectancia (`< 1.5%`):**
   - En aguas claras o ricas en taninos de las lagunas isleñas del Paraná inundadas en $T_1$, la reflectancia en `B03` (`~30-70 DN`), `B04` (`~35-65 DN`), `B08` (`1-50 DN`) y `B11` (`50-120 DN`) cae cerca del piso de ruido atmosférico Sen2Cor. Como `B08` se aproxima a `0` y `B11` residual supera levemente a `B03`, `(B11+B04)-(B08+B02)` queda positivo (`BSI > +0.20`) mientras `MNDWI` cae a valores negativos (`-0.05` a `-0.45`), satisfaciendo la regla actual de suelo desnudo seco (`BSI_T1 > -0.02` y `MNDWI_T1 < +0.10`) aunque `SCL_T1 == 6` indique agua y la reflectancia SWIR real sea inferior al `1.5%`.
   - **Impacto observado:** Generó **`264` falsos positivos (`~210 ha`)** de supuestos desmontes/caminos sobre zonas inundadas.
   - **Mitigación física recomendada (para calibración post-experimento):** Exigir brillo físico real en suelo/arena (`B11_T1 >= 600–800 DN` y `B04_T1 >= 400 DN`) y excluir píxeles con `SCL_T1 == 6` (agua) de la máscara `riparian_clearing_mask`.

2. **Insuficiencia geométrica del filtro de crecida (`uniform_stage_belt_mask`) en llanura aluvial con islas y albardones:**
   - El filtro actual solo descuenta componentes de ganancia de agua que tocan la orilla de $T_0$ (`min_dist <= 1.5 px`) y tienen ancho estrecho y uniforme (`d90 <= max(6.0, 2.2 * d50)`), y además depende de un umbral binario frágil (`shoreline_flooding_fraction >= 0.35`).
   - **Falla 1 (Desconexión topográfica):** Las lagunas internas isleñas (`#1 RIO-0170`, `#2 RIO-0067`, `#3 RIO-0153`, `#4 RIO-0131`) se inundan por desborde laminar o napa freática detrás de albardones de `10–40 m`, quedando desconectadas de `water_t0` (`min_dist > 1.5 px`).
   - **Falla 2 (Sensibilidad del disparador):** En la corrida de mediana de 3 años, `water_stage_delta_pct = +25.15%` con `shoreline_flooding_fraction = 0.314 < 0.35` dejó `is_systemic_stage_rise = False`, desactivando todo el filtro y elevando el área falsa de `329.58 ha` a `517.19 ha`.
   - **Impacto observado:** Cuando el río sube `> +15%` entre $T_0$ y $T_1$, una búsqueda de "excavaciones inundadas" por $\Delta\text{MNDWI}$ sobre el valle de inundación entero colapsa por saturación de falsos positivos naturales. Durante pulsos de crecida fuerte, la vigilancia de excavaciones inundadas debe restringirse o exigir morfología antrópica estricta, o comparar escenas de cota hidrométrica equivalente (integrando datos de hidrómetro PNA Puerto Santa Fe / Puerto Paraná).

3. **Filtro de turbidez unidireccional (`compute_turbidity_confounder_flag`):**
   - El caso `#15 RIO-0027` (`6.12 ha`) evidenció que cuando un banco somero con mezcla agua/sedimento muy rojizo en $T_0$ (`B04=988, MNDWI=+0.17`) queda cubierto por agua más profunda en $T_1$ (`B04=641, MNDWI=+0.87`), $\Delta\text{B04}$ es negativo (`-347`), burlando el filtro `red_increase > 150`. En realidad cualquier píxel con `MNDWI_T0 > 0.15` o `SCL_T0 == 6` que ya estaba dentro del cauce húmedo o barra sumergible en $T_0$ no debería clasificarse como excavación nueva en tierra firme.

---

## 7. Anomalías Priorizadas para Inspección Humana y Hoja de Ruta

### 7.1 Lista Acotada para Inspección Visual de Alta Resolución (VHR / Campo)

De las 452 geometrías generadas automáticamente, la auditoría espectral y visual de este experimento reduce la lista de interés antrópico genuino sobre ribera firme a **4 focos principales** (ver panel [09_recortes_zoom_sospechosos_ribera.png](file:///Users/pablotempone/rio-alerta-mcp/data/experiments/exp_001_parana_medio/09_recortes_zoom_sospechosos_ribera.png)):

1. **Prioridad 1 — `#218 RIO-0184`** (`-60.5634, -31.7284` | `0.48 ha` | `B11_T1 = 1573 DN` | Bajada Grande, Paraná): Expansión de suelo/material brillante inmediatamente lindante con una cava/playón industrial costero preexistente. Requiere verificación con imagen VHR (Google/IGN/drone) y consulta de habilitación municipal/provincial (Entre Ríos).
2. **Prioridad 2 — `#322 RIO-0225`** (`-60.5696, -31.7065` | `0.27 ha` | `B11_T1 = 1911 DN` | Punta ribereña norte Paraná): Alteración ribereña directa de alta reflectancia SWIR sobre borde del río. Verificar si corresponde a playa de descarga/acopio de arena o defensa costera autorizada.
3. **Prioridad 3 — `#380 RIO-0199`** (`-60.5695, -31.7177` | `0.21 ha` | `B11_T1 = 2676 DN` | Bajada Grande periurbano): Remoción de cobertura vegetal sobre parcela de alto albedo; verificar uso de suelo.
4. **Prioridad 4 — `#389 RIO-0365`** (`-60.6111, -31.6575` | `0.21 ha` | `B11_T1 = 2839 DN` | Corredor RN 168): Terraplén/acopio brillante adyacente a ruta pavimentada; verificar obra vial.

### 7.2 Qué debe calibrarse antes de pasar a Fase 3

1. En `change_detector.py`: restringir `riparian_clearing_mask` a suelo/arena físicamente reflectivo (`B11_T1 >= 600` y `B04_T1 >= 350`) y `SCL_T1 != 6`, eliminando de raíz el `96%` de falsos positivos de agua oscura sobre islas.
2. En `hydrological_control.py`: mejorar el control hidrológico de creciente considerando **altura hidrométrica de puertos de referencia (PNA Santa Fe / Paraná)** o reduciendo automáticamente la sensibilidad / activando máscara de llanura de inundación cuando `water_stage_delta_pct >= 10%` sin depender únicamente de `shoreline_flooding_fraction >= 0.35` ni de conectividad estricta a `1.5 px`.
3. Validar nuevamente sobre este mismo caso de prueba (`exp_001_parana_medio`) y un segundo tramo en **estiaje comparable** (`ej. Sep 2024 vs Sep 2026`, donde el agua en AOI pasó solo de ~`23%` a `23.7%`) antes de construir el catastro de permisos, el juez VLM y el generador de expedientes de Fase 3.
