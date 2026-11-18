# Manual de Uso Rápido — RíoAlerta-MCP

> **Cómo pedirle cosas al servidor por chat.** Cada ejemplo muestra: 💬 lo que le escribís al asistente,
> 🔧 la herramienta MCP que se invoca con sus parámetros, y 📊 el resultado **real** que devuelve.
> Ningún resultado es una determinación jurídica: todo hallazgo es un **candidato** que requiere
> verificación documental y constatación presencial.

---

## 0. Conectar el servidor (una sola vez)

Agregá el servidor a tu cliente MCP (Claude Desktop, OpenCode, Antigravity, Cursor):

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

Las 12 herramientas disponibles: `tool_server_status`, `tool_resolve_posgar_crs`,
`tool_search_multitemporal_pair`, `tool_compute_spectral_changes`, `tool_detect_extraction_anomalies`,
`tool_evaluate_parana_pilot`, `tool_validate_permits`, `tool_evaluate_vlm_verdict`,
`tool_generate_enforcement_report`, `tool_manage_case_workflow`, `tool_generate_case_file`,
`tool_export_blind_review_queue`.

---

## 1. ¿El servidor está listo?

💬 **Vos:** *"¿Está funcionando el servidor de RíoAlerta y qué configuración tiene?"*

🔧 `tool_server_status()` — sin parámetros.

📊 **Resultado (real, abreviado):**

```json
{
  "status": "ok",
  "server": "RioAlerta-MCP",
  "version": "0.1.0",
  "permits_gpkg_exists": false,
  "limits": { "min_anomaly_area_m2": 1600.0, "seasonal_window_days": 45, "...": "..." }
}
```

**Qué te dice:** si `status=ok`, el motor está operativo. `permits_gpkg_exists=false` avisa que
todavía no cargaste un catastro de permisos (el cruce catastral funcionará en modo
`SIN_CATASTRO_OFICIAL_CARGADO`).

---

## 2. ¿Qué proyección métrica corresponde a esta coordenada?

💬 **Vos:** *"¿Qué faja POSGAR corresponde a Diamante, alrededor de (-60.63, -32.06)? Quiero las
coordenadas métricas."*

🔧 `tool_resolve_posgar_crs(lon=-60.63, lat=-32.06)`

📊 **Resultado (real):**

```json
{
  "lon_wgs84": -60.63,
  "lat_wgs84": -32.06,
  "posgar_faja": 5,
  "crs_epsg": 5347,
  "crs_name": "POSGAR 2007 / Argentina 5",
  "x_posgar_m": 5440507.72,
  "y_posgar_m": 6453287.05
}
```

**Qué te dice:** toda medición de áreas y distancias del sistema se hace en metros POSGAR 2007
(aquí Faja 5, `EPSG:5347`), no en grados.

---

## 3. Buscar y descargar un par de escenas Sentinel-2

💬 **Vos:** *"Buscá un par multitemporal comparable para el cuadrante (-60.66, -31.74, -60.53, -31.64)
del Paraná, con escena actual hasta el 2026-11-08, y descargálo listo para analizar."*

🔧 `tool_search_multitemporal_pair(min_lon=-60.66, min_lat=-31.74, max_lon=-60.53, max_lat=-31.64,
t1_end_date="2026-11-08")`

📊 **Resultado (estructura real):**

```json
{
  "status": "success",
  "t0_scene": { "scene_id": "S2B_20HQK_20231107_0_L2A", "acquisition_date": "2023-11-07", "cloud_cover_pct": 2.4 },
  "t1_scene":  { "scene_id": "S2A_20HQK_20261108_1_L2A", "acquisition_date": "2026-11-08", "cloud_cover_pct": 1.9 },
  "t0_raster_path":      "<proyecto>/data/raw/t0_S2B_..._vnir10m.tif",
  "t1_raster_path":      "<proyecto>/data/raw/t1_S2A_..._vnir10m.tif",
  "t0_swir_raster_path": "<proyecto>/data/raw/t0_S2B_..._swir_scl20m.tif",
  "t1_swir_raster_path": "<proyecto>/data/raw/t1_S2A_..._swir_scl20m.tif",
  "crs_epsg": 5347,
  "bounds_posgar": [5436832.9, 6485822.3, 5454021.4, 6498612.7],
  "resolution_m": 10.0
}
```

**Qué te dice:** el sistema elige una T1 reciente y una línea base T0 estacionalmente comparable
(mismo día del año ± 45 días), recorta solo tu cuadrante por HTTP Range Request y te devuelve las
rutas que usarás en los pasos siguientes. VNIR a 10 m, SWIR+SCL a 20 m nativos.

**Errores que vas a ver si algo falla:** `Bounding box inválido` (lon/lat invertidos), `fuera de
rango` (coordenadas imposibles), `t1_end_date inválido` (formato no ISO), `lookback_years debe
estar entre 1 y 10`, o `No se hallaron escenas recientes` (nubosidad > 20% en toda la ventana).

---

## 4. Detectar anomalías candidatas (motor completo)

💬 **Vos:** *"Con las escenas que descargaste, detectá las anomalías morfológicas candidatas y
decime qué quedó descartado y por qué."*

🔧 `tool_detect_extraction_anomalies(t0_path="data/raw/t0_..._vnir10m.tif",
t1_path="data/raw/t1_..._vnir10m.tif", t0_swir_20m_path="data/raw/t0_..._swir_scl20m.tif",
t1_swir_20m_path="data/raw/t1_..._swir_scl20m.tif")`
*(opcional: `stage_t0_m=0.67, stage_t1_m=4.85` con lecturas hidrométricas oficiales de INA)*

📊 **Resultado (real, corrida 2024-11-06 → 2026-11-08, Paraná Medio, abreviado):**

```json
{
  "type": "FeatureCollection",
  "metadata": {
    "candidate_count": 29,
    "discarded_count": 1715,
    "discarded_truncated": true,
    "calibration_parameters_n": 42,
    "hydrological_controls": {
      "water_stage_delta_pct": 26.29,
      "is_systemic_stage_rise": true,
      "stage_t0_m": 0.67, "stage_t0_source": "INA_API_A5",
      "stage_t1_m": 4.85, "stage_t1_source": "INA_API_A5",
      "bar_migration_dipoles_filtered": 22
    }
  },
  "features": [{
    "id": "RIO-0001",
    "candidate_uid": "RIO-5347-5449470-6494910-44910BDC",
    "anomaly_type": "frente_excavacion_inundado",
    "confidence": 0.71,
    "area_m2": 2497.54,
    "geomorphological_zone": "RIBERA_FIRME_Y_TERRAZA",
    "centroid_wgs84": [-60.621, -31.740],
    "uncertainty": {
      "effective_resolution_m": 20.0,
      "valid_pixel_fraction": 0.94,
      "cloud_shadow_adjacent": false,
      "cloud_shadow_source_dates": [],
      "water_stage_delta_pct": 26.29,
      "natural_dynamics_confounder_score": 0.25,
      "limitations": ["Resolución física limitante 20 m (bandas SWIR B11/B12 a 20 m nativos)."]
    }
  }]
}
```

**Cómo leerlo:**
- `candidate_count`: polígonos que sobrevivieron los filtros (candidatos, **no** confirmaciones).
- `discarded_count`: lo que se descartó, con motivo tipado en `discarded_candidates` (y el log
  completo persistido en `discarded_candidates_<t0>_vs_<t1>.json`).
- `candidate_uid` es estable entre corridas — usalo para referenciar el caso en el chat.
- `anomaly_type` es una **etiqueta de hipótesis de detección**, no una conclusión de causa.
- `confidence` es un puntaje de señal, **no una probabilidad estadística**.
- Si en tu chat pide "los descartados por crecida", pedile al asistente que lea
  `metadata.discarded_candidates` filtrando por `filter_status`.

---

## 5. Cruce catastral contra permisos

💬 **Vos:** *"¿El candidato RIO-0001 está cubierto por algún permiso vigente de extracción para el
2026-11-08? El padrón está en `data/permits.gpkg` y lo emite la Secretaría de Minería."*

🔧 `tool_validate_permits(anomaly_dict=<feature>, t1_date="2026-11-08",
permits_gpkg_path="data/permits.gpkg", cadastre_status="OFICIAL_COMPLETO",
authority_name="Secretaría de Minería Provincial", jurisdiction="Entre Ríos")`

📊 **Resultado (real, sin catastro cargado):**

```json
{
  "classification": "SIN_CATASTRO_OFICIAL_CARGADO",
  "priority": "MEDIA_VERIFICACION",
  "excess_area_m2": 2497.54,
  "excess_pct": 100.0,
  "human_review_required": true,
  "observations": "Sin padrón catastral oficial cargado... La ausencia de coincidencia o de
  registro digital cargado NO constituye prueba de actividad no autorizada; requiere consulta
  documental al organismo provincial/municipal competente y verificación en terreno."
}
```

**Valores posibles de `classification`:** `EXTRACCION_AUTORIZADA` (unión de permisos vigentes y
compatibles cubre el polígono dentro de tolerancia), `EXCESO_DE_POLIGONO` (parte fuera del permiso),
`PERMISO_VENCIDO`, `PERMISO_NO_VERIFICABLE_O_INCOMPATIBLE` (fechas faltantes o actividad de obra/no
extractiva), `SIN_COINCIDENCIA_EN_CATASTRO_LOCAL` / `_EN_REGISTROS_CONSULTADOS` (padrón cargado pero
sin intersección), `SIN_CATASTRO_OFICIAL_CARGADO` (no hay padrón).

⚠️ **Trampa clásica:** declarar `cadastre_status="OFICIAL_COMPLETO"` sin `authority_name` — el
sistema lo degrada automáticamente a `PARCIAL_LOCAL` y te avisa en `observations`.

---

## 6. Cribado heurístico del candidato

💬 **Vos:** *"Dame un cribado inicial de RIO-0001, aclarando que no es una evaluación visual."*

🔧 `tool_evaluate_vlm_verdict(anomaly_dict=<feature>, evaluation_mode="HEURISTIC_SCREENING")`

📊 **Resultado (real):**

```json
{
  "evaluation_mode": "HEURISTIC_SCREENING",
  "verdict": "EXTRACCION_ANTROPICA",
  "certainty_pct": 79,
  "is_statistical_probability": false,
  "expert_declaration_recorded": false,
  "extraction_evidence": "Cribado heurístico determinista basado únicamente en métricas
  morfo-espectrales tabulares (NO utiliza inspección visual ni modelo VLM; NO constituye dictamen
  pericial): ..."
}
```

**También podés registrar una evaluación externa o de un perito:**

💬 *"Registra que el perito `perito_conicet_01` revisó los chips `chip_t0.png` y `chip_t1.png` y
concluyó `EXTRACCION_ANTROPICA` con 92% de certeza."*

🔧 `tool_evaluate_vlm_verdict(anomaly_dict=<feature>, evaluation_mode="HUMAN_EXPERT",
verdict="EXTRACCION_ANTROPICA", certainty_pct=92, evaluator_id="perito_conicet_01",
image_refs=["data/processed/chip_t0.png", "data/processed/chip_t1.png"])`

El resultado registra los SHA-256 de las imágenes revisadas. `HUMAN_EXPERT` exige `evaluator_id`;
`EXTERNAL_VLM` exige además `model_id`. La acreditación del perito la valida el organismo.

---

## 7. Expediente técnico completo (con figuras incluidas)

💬 **Vos:** *"Generá el expediente técnico reproducible de RIO-0001 con las escenas del paso 3."*

🔧 `tool_generate_enforcement_report(anomaly_dict=<feature>, t0_scene_id="S2A_20HQK_20241106_0_L2A",
t0_date="2024-11-06", t1_scene_id="S2A_20HQK_20261108_1_L2A", t1_date="2026-11-08",
t0_vnir_10m_path="data/raw/t0_..._vnir10m.tif", t1_vnir_10m_path="data/raw/t1_..._vnir10m.tif")`

📊 **Resultado (real):**

```json
{
  "status": "success",
  "anomaly_id": "RIO-0001",
  "report_generation_id": "GEN-20261117T235835Z-1c45bfc9",
  "classification": "SIN_CATASTRO_OFICIAL_CARGADO",
  "priority": "MEDIA_VERIFICACION",
  "figure_paths": [
    "mapa_contexto_RIO-0001.png",
    "chip_rgb_t0_RIO-0001.png",
    "chip_rgb_t1_RIO-0001.png",
    "comparacion_t0_t1_RIO-0001.png"
  ]
}
```

**Qué obtienes:** un dossier de 9 secciones (`report.md`) con las **4 figuras embebidas**
(mapa de contexto, chips RGB T0/T1 y comparación multitemporal — generadas automáticamente si no
le pasaste imágenes), `report.json` estructurado con toda la provenance, y `manifest.json` con
SHA-256 de cada artefacto. Todo publicado atómicamente en
`data/reports/<candidato>/generations/<gen_id>/`. Para verificar que nadie tocó el expediente:

💬 *"Verificá la integridad de esa generación."* → `verify_report_generation_integrity(<gen_dir>)`.

---

## 8. Abrir y seguir el expediente administrativo

💬 **Vos:** *"Abrí un caso para RIO-0001 y anotá que se pidió inspección con oficio PNA-2026-001.
Si me equivoco y lo repito, no lo dupliques."*

🔧 `tool_manage_case_workflow(action="open", anomaly_dict=<feature>)` → luego
`tool_manage_case_workflow(action="append_event", case_id=<case_id>, action_type="SOLICITUD_INSPECCION",
summary="Oficio PNA-2026-001", new_status="INSPECCION_SOLICITADA", idempotency_key="oficio-pna-2026-001")`

📊 **Resultado (real):**

```json
{ "case_id": "CASE-RIO-5347-5449470-6494910-44910BDC", "status": "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO", "events": 1 }
```
```json
{ "status": "INSPECCION_SOLICITADA", "events": 2 }
```
*(reintento con la misma `idempotency_key` → `events: 2`, sin duplicado)*

💬 *"El cambio físico es real pero no pudimos determinar la causa. Cerrá el caso como indeterminado."*

🔧 `tool_manage_case_workflow(action="append_event", case_id=..., action_type="CAMBIO_ESTADO",
summary="Cambio real persistente sin causa determinada", new_status="CAMBIO_REAL_INDETERMINADO")`

📊 `{"status": "CAMBIO_REAL_INDETERMINADO", "events": 3}` — es un estado terminal válido y
científicamente honesto.

Estados válidos: `PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO`, `EN_REVISION_GABINETE`,
`INSPECCION_SOLICITADA`, `INSPECCIONADO_SIN_PERMISO_REGISTRADO`, `EXTRACCION_AUTORIZADA`,
`EXCESO_DE_POLIGONO_EN_VERIFICACION`, `DESCARTADO_DINAMICA_NATURAL`, `ARCHIVADO_FALSO_POSITIVO`,
`CAMBIO_REAL_INDETERMINADO`, entre otros. La bitácora es **append-only** (SQLite + JSONL con
cadena de hashes); no se puede editar ni borrar eventos.

---

## 9. Ficha de caso del protocolo de validación

💬 **Vos:** *"Generá la ficha de validación del caso, vinculada al expediente."*

🔧 `tool_generate_case_file(anomaly_dict=<feature>, case_id="CASE-RIO-...")`

📊 **Resultado (real, abreviado):** escribe `data/reports/<candidato>/case_file.json`:

```json
{
  "ficha_version": "phase3.2.v1",
  "identificador": { "candidate_uid": "RIO-5347-...", "caso_id": "CASE-RIO-5347-..." },
  "deteccion": { "signal_score": 0.71, "es_probabilidad_estadistica": false,
                 "dominio_scl": { "interior_t1_10m_counts": {...}, "buffer": {...} } },
  "revision_humana_independiente": { "ciego_aplicado": null, "evaluator_id": null },
  "resultado_verificacion": { "nivel_evidencia": null, "etiqueta_referencia": null }
}
```

Los campos de revisión y verificación quedan **pendientes hasta que la institución los complete**;
la ficha nunca los fabrica.

---

## 10. Cola de revisión en ciego (para validación con peritos)

💬 **Vos:** *"Del GeoJSON de resultados, armá una cola de revisión en ciego con 5 casos por
estrato para los peritos — que no vean la tipología ni el score del detector."*

🔧 `tool_export_blind_review_queue(source_geojson_path="data/processed/resultados.geojson",
min_per_stratum=5, resolution_m=20.0)`

📊 **Resultado (real, abreviado):**

```json
{
  "status": "success",
  "n_casos": 5,
  "advertencia": "Entregue al revisor únicamente review_worksheet.json, los chips y la plantilla
  de acta; private_manifest.json permanece bajo custodia del coordinador del protocolo."
}
```

Cada caso del worksheet tiene **solo** `blind_id` (ej. `REV-AB11366604`), polígono, centroide,
escenas, resolución y chips — la tipología, el score, la zona y hasta la condición de descarte
quedan ocultos en el manifiesto privado. Incluye plantilla de acta de revisión.

---

## 11. Errores comunes y cómo resolverlos

| Mensaje | Causa | Solución |
| --- | --- | --- |
| `No existe el archivo raster requerido` | Ruta mal escrita o fuera de `data/` | Usá rutas relativas al proyecto o dentro de `data/`, `data/processed`, `data/reports` |
| `Ruta fuera de las raíces permitidas` | Path traversal o directorio del sistema | El MCP solo lee/escribe dentro del proyecto y sus raíces de datos |
| `min_area_m2 debe ser mayor que 0` | Parámetro inválido | Valores > 0 (default 1600 m² excavación, 800 m² desmonte) |
| `t1_end_date inválido` | Formato no ISO | Usar `YYYY-MM-DD` |
| `cadastre_status inválido` | Valor no admitido | `OFICIAL_COMPLETO` / `PARCIAL_LOCAL` / `NO_DISPONIBLE` |
| `action='append_event' requiere case_id...` | Falta algún campo del evento | Pasar `case_id`, `action_type` y `summary` |
| `idempotencia` no duplica | Comportamiento correcto | El reintento con la misma clave no crea eventos |

---

## 12. Reglas de oro

1. **Un candidato ≠ una infracción.** Todo queda `human_review_required=true` hasta constatación.
2. **`INDETERMINADO` es una respuesta legítima.** Con Sentinel-2 (10/20 m), la mayoría de los
   casos reales termina sin causa determinada sin evidencia VHR/campo.
3. **Nunca edites a mano** `data/reports/**/generations/**` ni los logs del workflow: los hashes
   SHA-256 delatan la alteración.
4. **El score (`confidence`/`signal_score`) no es una probabilidad** ni sustituye al perito.
5. Antes de analizar un tramo nuevo, pedile al asistente que corra `tool_server_status`.
