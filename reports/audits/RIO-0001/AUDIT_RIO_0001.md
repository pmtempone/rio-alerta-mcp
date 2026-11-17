# AUDITORÍA INDEPENDIENTE RIO-0001 — RÍOALERTA

**Objetivo:** Auditar de forma independiente el candidato `RIO-0001` (`GEN-20261116T220512Z-1e9574ce`) sin re-ejecutar el detector para auto-confirmarse ni modificar parámetros congelados.

---

## FASE A — Reproducibilidad e Inventario

- **Geometría y coordenadas:** Reproyectando `geometry_geojson` desde `EPSG:4326` a `EPSG:5347` (POSGAR 2007 Faja 5) se obtuvo exactamente **`5.065,02 m²`** y centroide **`(5441127.25, 6488813.89)`** (`(-60.621284, -31.739658)` WGS84), coincidiendo al 100% con `report.json`.
- **Escenas originales:** `t0_S2B_20HQK_20230928_0_L2A` (`2023-09-28`) y `t1_S2C_20HQK_20260917_0_L2A` (`2026-09-17`), alineadas en `EPSG:5347` (10 m VNIR `1116x1239`, 20 m SWIR/SCL `558x619`).
- **Inventario completo y hashes SHA-256:** Ver [evidence_inventory.json](evidence_inventory.json).

---

## FASE B — Análisis Visual Multitemporal (2023–2026)

Se incorporaron dos escenas independientes de **Earth Search STAC** (`S2A_20HQK_20241116_0_L2A` con `0.003%` de nubosidad y `S2B_20HQK_20251126_1_L2A` con `0.78%`), generando:
- Secuencia cronológica RGB: [01_rgb_timeline_2023_2026.png](timeline/01_rgb_timeline_2023_2026.png)
- Mapa de contexto (buffer 500 m, EPSG:5347): [context_map.png](context_map.png)
- Evolución espectral: [spectral_evolution_chart.png](spectral_analysis/spectral_evolution_chart.png) y [spectral_audit_metrics.json](spectral_analysis/spectral_audit_metrics.json)

| Fecha | Escena Sentinel-2 L2A | NDVI (med.) | MNDWI (med.) | BSI (med.) | B11 (med. DN) |
| --- | --- | ---: | ---: | ---: | ---: |
| `2023-09-28 (T0)` | `S2B_20HQK_20230928_0_L2A` | `+0.5287` | `-0.2619` | `-0.1398` | `1087.2` |
| `2024-11-16` | `S2A_20HQK_20241116_0_L2A` | `-0.2487` | `+0.1901` | `+0.1202` | `525.9` |
| `2025-11-26` | `S2B_20HQK_20251126_1_L2A` | `-0.3221` | `+0.3705` | `+0.0771` | `420.3` |
| `2026-09-17 (T1)` | `S2C_20HQK_20260917_0_L2A` | `-0.4150` | `+0.4999` | `+0.0927` | `248.4` |

**Observación:** El cambio territorial **es real y persistente** desde al menos noviembre de 2024 hasta septiembre de 2026: pasó de cobertura vegetal firme (`NDVI +0.53`) a superficie inundada / agua abierta (`MNDWI +0.50`, `NDVI -0.42`). A resolución de 10 m/20 m no es posible observar maquinaria, taludes rectilíneos ni caminos de acarreo.

---

## FASE C — Hipótesis Alternativas

- **H1 (Extracción o acopio de áridos):** *Plausible, no confirmada.* Explica la transformación de ribera vegetada en cuerpo de agua (`frente_excavacion_inundado`), pero faltan imágenes VHR (< 1 m) y catastro oficial.
- **H2 (Obra civil / infraestructura):** *Baja plausibilidad sin VHR.* No hay evidencia de traza vial o estructura lineal en 10 m, aunque OSM Overpass falló (`HTTP 406`).
- **H3 (Dinámica fluvial, erosión o inundación):** *Plausible, no confirmada.* `RIO-0001` se ubica en `PLANICIE_INUNDABLE_E_ISLAS`; la erosión lateral o reconexión de cauce secundario produce exactamente la misma firma espectral de transición vegetación → agua abierta.
- **H4 (Variación estacional):** *Descartada como causa única.* El cambio persiste en 4 años consecutivos (`2024–2026`) en meses comparables de primavera.
- **H5 (Artefactos atmosféricos / desalineación):** *Descartada.* La transformación es consistente en 4 escenas independientes de distintos sensores (`S2A`, `S2B`, `S2C`).

---

## FASE D — Auditoría Espectral y Geométrica

1. **Interpretación de `B11 T0=1051.8` → `T1=240 DN`:** `B11 = 240 DN` en `T1` **no** indica exposición mineral ni arena seca; refleja absorción fuerte del agua abierta en el SWIR1 (`1.6 µm`), totalmente coherente con `MNDWI = +0.4964`, `NDVI = -0.4257` y `SCL = 6` (agua en 32/46 píxeles 10 m).
2. **Contradicción `SCL=7` en el dossier:**
   - En `T1` (`2026-09-17`) dentro del polígono: **0 píxeles** `SCL=7` (`SCL 5=14, 6=32` en 10 m; `5=4, 6=12` en 20 m). Por eso la Sección 5 informa `SCL=7: 0 píxeles`.
   - Sin embargo, en `T0` (`2023-09-28`) dentro del polígono hay **4/16 píxeles** `SCL=7` (20 m), y en el buffer de 40 m de `T1` hay **2 píxeles** `SCL=7` y **5 píxeles** `SCL=2`. La advertencia del dossier refleja la condición en `T0`/buffer, generando una inconsistencia de redacción frente a la tabla centrada en `T1`.

---

## FASE E — Contexto Administrativo

- **Catastro / concesiones:** `NO_DISPONIBLE` (`SIN_CATASTRO_OFICIAL_CARGADO`). No implica falta de autorización.
- **OSM Overpass API:** Consultada y fallida con `HTTP 406 Not Acceptable`.
- **Verificaciones pendientes:** Detalladas en [pending_verifications.md](pending_verifications.md).

---

## FASE F — Dictamen Final

- **Clasificación:** **`INDETERMINADO`** *(no equivale a determinación jurídica).*
- **Justificación:** Está comprobado que existe un cambio físico real y persistente (`2023–2026`) de vegetación firme a agua abierta sobre `5.065,02 m²` en planicie inundable. Sin embargo, con Sentinel-2 (10 m/20 m) y sin catastro oficial ni imágenes VHR/campo, tanto **H1** (excavación inundada) como **H3** (dinámica fluvial/erosión) permanecen plausibles.
