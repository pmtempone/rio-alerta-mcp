# Dossier de Investigación Territorial — RIO-0013

> **Regla epistémica obligatoria:** Este documento es una herramienta de orientación investigativa y fiscalización ambiental. Ninguna medición satelital por sí sola demuestra extracción de áridos ni infracción legal.

## 1. Ficha de identificación

| Campo | Valor |
| :--- | :--- |
| **ID de candidato** | `RIO-0013` |
| **Sector / AOI** | Paraná Medio — Sector Isla/Ribera Santa Fe–Paraná (contraste Exp 001 / RIO-0184) |
| **Período analizado** | 2023-11-07 .. 2025-11-13 (T0) -> 2026-11-08 (T1) |
| **Tipología morfológica** | `desmonte_ribera_acopio` |
| **Unidad geomorfológica (T0)** | `PLANICIE_INUNDABLE_E_ISLAS` |
| **Centroide WGS84 (lon, lat)** | `-60.563387, -31.728294` |
| **Centroide POSGAR 2007 (EPSG:5347)** | `5446607.16, 6490103.82` |
| **Superficie vectorial exacta** | **1,848.69 m²** (0.1849 ha) |
| **Distancia al cuerpo de agua T0** | 245.1 m |
| **Estado de verificación** | `PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO` |
| **Fecha de emisión** | 2026-11-15 00:11 UTC |

## 2. Resumen ejecutivo

- **Observación:** En el polígono `RIO-0013` (1,848.69 m², `PLANICIE_INUNDABLE_E_ISLAS`), las escenas Sentinel-2 registran un cambio espectral compatible con `desmonte_ribera_acopio` (diferencia media de píxeles: `ΔBSI_adj=0.3767`, `ΔNDVI_adj=-0.4842`, `ΔMNDWI=0.0522`).
- **Interpretación:** El patrón espectral sugiere pérdida de cobertura vegetal y exposición de superficie mineral o inundación localizada; no obstante, coexisten explicaciones alternativas (obra rural/portuaria, variación hidrométrica o artefactos de borde SWIR 20 m / SCL=7).
- **Hipótesis:** Compatible con posible movimiento de suelos, acopio o frente de extracción ribereña, así como con usos antrópicos autorizados o dinámica de borde aluvial.
- **Verificación:** Requiere inspección visual de alta resolución / drone, constatación en terreno y consulta de permisos ante la autoridad provincial competente.

## 3. Evidencia territorial y mapas

### Observación morfológica y espacial
- **Área geométrica (`shapely_polygon_posgar_2007`):** 1,848.69 m² (0.1849 ha).
- **Muestreo en grilla:** 12 píxeles a 10 m (~3.0 píxeles nativos SWIR de 20 m).
- **Morfología:** Rectangularidad `0.561`, elongación `3.666`.
- **Contexto geomorfológico T0:** `PLANICIE_INUNDABLE_E_ISLAS`, distancia al agua T0 `245.1 m`.
- **Productos cartográficos asociados:**
  - `data/experiments/exp_001_recalibrated/exp_001_recalibrated.geojson`
  - `data/experiments/exp_001_parana_medio/08_recortes_zoom_anomalias_principales.png`

## 4. Evolución multitemporal

> **Nota metodológica:** Se distinguen explícitamente las **medianas espectrales por fecha** calculadas sobre el polígono de las **medias de diferencias por píxel (`delta_*_mean`)** ajustadas fenológicamente entre T0 y T1.

| Fecha | Escena / Sensor | Altura río (m) | B11 med. (DN) | NDVI med. | BSI med. | MNDWI med. | Clases SCL |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| 2023-11-07 | S2B_20HQK_20231107_0_L2A | 3.51 m | 1932 | +0.729 | -0.246 | -0.430 | 4:20 |
| 2024-11-06 | S2A_20HQK_20241106_0_L2A | 0.67 m | 1832 | +0.643 | -0.176 | -0.411 | 4:20 |
| 2025-11-13 | S2A_20HQK_20251113_1_L2A | 2.64 m | 2425 | +0.374 | +0.070 | -0.491 | 4:7, 5:13 |
| 2026-11-08 | S2A_20HQK_20261108_1_L2A | 4.85 m | 1686 | +0.157 | +0.142 | -0.347 | 4:1, 5:14, 7:5 |

**Resumen de diferencias por píxel (T0 -> T1):** `delta_bsi_mean=0.3767`, `delta_ndvi_mean=-0.4842`, `delta_mndwi_mean=0.0522`.

## 5. Análisis de hipótesis alternativas

| Hipótesis | Nivel | Evidencia observada compatible | Evidencia en contra / incertidumbre | Estado |
| :--- | :--- | :--- | :--- | :--- |
| **H1. Extracción / acopio de áridos o desmonte operativo** | Hipótesis | Cambio localizado `desmonte_ribera_acopio` de 1,849 m² en `PLANICIE_INUNDABLE_E_ISLAS`, `ΔBSI_adj=0.377`. | Resolución SWIR 20 m insuficiente para distinguir acopio mineral de obra civil sin VHR/campo. | `A VERIFICAR EN TERRENO` |
| **H2. Obra civil, camino rural o infraestructura ribereña** | Hipótesis | Incremento de suelo expuesto (`B11_T1=1719 DN`) y geometría con elongación `3.67`. | No se dispone de catastro parcelario ni permisos de obra cargados en el sistema. | `NO DESCARTABLE` |
| **H3. Dinámica fluvial / humedad edáfica / crecida** | Interpretación | Contexto hidrométrico: Δagua AOI=25.15%, Δaltura=+2.07 m (2.69 -> 4.76 m). | El polígono superó filtros de cinturón de crecida, migración dipolar y agua oscura (`B11_T1=1719`). | `RESIDUAL TRAS FILTROS` |
| **H4. Artefacto atmosférico / sombra / borde SCL=7** | Interpretación | Píxeles `SCL=7` en T1: 0; adyacencia nube/sombra: `False`. | Fracción de píxeles válidos `1.0`. | BAJA PROBABILIDAD |

## 6. Confiabilidad por dimensión

> **Score numérico interno:** `0.663` (escala 0–1). Este índice resume fuerza de señal, compacidad geométrica y penalizaciones por confundidores; **no** se traduce automáticamente a una categoría cualitativa de certeza ni sustituye la evaluación pericial.

| Dimensión | Medición / Estado |
| :--- | :--- |
| **Calidad espectral y máscara SCL** | Fracción válida: `1.0`, adyacencia nube/sombra: `False`, SCL=7: `0` px |
| **Resolución espacial efectiva** | 20.009770821454982 m SWIR (~3.0 píxeles SWIR en el polígono) |
| **Estabilidad hidrométrica del tramo** | Variación lámina AOI: `25.15%`; score de confundidores naturales: `0.25` |
| **Persistencia temporal de línea base** | `3` escena(s) en T0 |

## 7. Limitaciones y datos faltantes

- Resolución física limitante 20 m (bandas SWIR B11/B12 a 20 m nativos).
- Variación hidrométrica regional detectada en el tramo (+25.1%).
- Cruce administrativo/catastral: Sin cruce realizado contra catastro oficial provincial o registro de concesiones mineras/áridos.
- Superficie cercana al límite operativo SWIR 20 m (1,848.7 m² ≈ 3.0 px SWIR): susceptible a efecto de píxel mixto en bordes.

## 8. Recomendación de actuación (Nivel: Verificación)

1. **Inspector ambiental:** Verificar en terreno las coordenadas WGS84 indicadas para constatar presencia de dragado, refulado, acopio o movimiento de suelos, o descartar uso rural/obra ajena a áridos.
2. **Autoridad provincial / Catastro y Minería:** Cruzar el polígono contra padrón catastral, concesiones mineras de 3ª categoría y permisos ambientales vigentes.
3. **Prefectura / Control ribereño:** Utilizar el centroide como orientación geográfica preliminar en patrullajes de jurisdicción fluvial, sin asumir infracción previa.
4. **Seguimiento satelital:** Incorporar imagen VHR o vuelo UAV si se requiere precisar volúmenes o límites parcelarios por debajo de 20 m.

## 9. Fuentes, parámetros y trazabilidad técnica

- **CRS métrico de cálculo:** `EPSG:5347` (POSGAR 2007). Exportación GeoJSON: `EPSG:4326`.
- **Resolución de grilla / SWIR nativo:** 9.996810442680092 m / 20.009770821454982 m.
- **Área mínima de detección:** 1600.0 m².
- **Parámetros congelados Fase 2.1:** `bsi_reg_albedo_k=800.0`, `dark_water_min_b11_dn=600.0`, `dark_water_min_b04_dn=420.0`, `dark_water_min_b08_dn=350.0`, `scl7_min_b11_dn=850.0`, `scl7_min_b04_dn=500.0`, `hydrometric_stage_rise_alert_m=0.8`.
- **Nota institucional del motor:** Las geometrías detectadas son anomalías morfológicas candidatas; no constituyen por sí solas infracción ni extracción ilegal sin cruce catastral oficial y revisión humana.
