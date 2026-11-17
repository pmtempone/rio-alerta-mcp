# RÍOALERTA — Protocolo de Validación Independiente de Candidatos (Fase 3.2)

> **Estado:** PROPUESTA DOCUMENTADA — **no implementado**. Este documento define el protocolo,
> su fundamentación y su plan de implementación secuencial. Nada de lo aquí descripto modifica
> parámetros del detector, introduce nuevas funcionalidades de IA ni recalifica hallazgos existentes.
>
> **Principio rector invariante:** ninguna detección satelital, cribado automático, dictamen auxiliar
> ni resultado de este protocolo constituye imputación jurídica ni prueba definitiva. Todo hallazgo
> es **candidato** sujeto a verificación documental y constatación presencial.
>
> **Alcance temporal:** aplica sobre las corridas congeladas de Exp 002 (`exp_001_recalibrated`,
> `aoi1_2024_2025`, `aoi1_2023_2026`, `aoi2_diamante_2023_2025`) y sus sucesoras con parámetros
> congelados. Los parámetros vigentes (`42` en `Settings.get_calibration_parameters()`, commit
> `1d13eb7`) permanecen congelados durante toda la Fase 3.2.

---

## 0. Resumen ejecutivo

Este protocolo convierte la auditoría de `RIO-0001` en un procedimiento científico reutilizable para
evaluar la calidad del detector sobre **muestras de candidatos** de distintas tipologías y unidades
geomorfológicas. Sus decisiones estructurales son cinco:

1. **El conjunto de referencia etiqueta física e hipótesis, no legalidad** (§4.6).
2. **Procedimiento de ciego obligatorio** para toda revisión humana (§4.3).
3. **Muestreo estratificado con cuotas** por tipología × zona geomorfológica, con criterio de
   parada por ancho de intervalo de confianza (§4.2).
4. **Jerarquía explícita de admisibilidad de evidencia** (§4.5), con `INDETERMINADO` como
   resultado terminal de primera clase (§4.7).
5. **Prohibición de computar métricas de rendimiento hasta que exista un conjunto de referencia
   independiente, suficiente y sancionado** (§5.7).

---

## 1. Auditoría de la auditoría (meta-auditoría)

Todo protocolo de validación que use artefactos de auditoría previos debe auditarlos primero. La
meta-auditoría aplica a `reports/audits/RIO-0001/` y a toda auditoría futura producida bajo este
protocolo.

### 1.1 Checklist de comprobación

| # | Check de la consigna | Método | Automatizable |
| --- | --- | --- | --- |
| M1 | Las escenas utilizadas son las descargadas de Earth Search | Comparar SHA-256 de cada raster contra `evidence_inventory.json` y re-descargar verificación puntual (window read) del asset STAC | Parcial (hashes locales ✅; re-descarga puntual manual) |
| M2 | Fechas e IDs STAC corresponden a cada imagen | Cruzar `scene_id` ↔ `datetime` contra la API STAC de origen (items por id) | ✅ |
| M3 | Los productos espectrales se calcularon de forma reproducible | Verificar que cada métrica registra **código, versión, función y remuestreo** usados (ver hallazgo M3-1) | ✅ una vez exigido el registro |
| M4 | El análisis temporal no reutiliza resultados del detector como evidencia independiente | Revisar que los índices de escenas intermedias se recalcularon desde los rasters (no copiados de GeoJSONs del detector); verificar independencia de código (M3-1) | Parcial |
| M5 | Cada conclusión del informe tiene un artefacto o fuente que la respalda | Recorrer cada afirmación del informe y mapearla a un artefacto con hash o fuente externa citada | Manual, asistido |
| M6 | Las limitaciones están correctamente documentadas | Verificar que cada limitación material (resolución, catastro ausente, OSM fallida, soporte de píxeles) figure en el informe y en `pending_verifications.md` | Parcial |

### 1.2 Hallazgos preliminares sobre `reports/audits/RIO-0001/`

La meta-auditoría inicial ya identifica dos brechas de trazabilidad **en la propia auditoría**
(no invalidan sus conclusiones; definen lo que el protocolo exigirá de aquí en adelante):

- **M3-1 — Provenance metodológica incompleta:** `spectral_analysis/spectral_audit_metrics.json`
  registra valores por escena (NDVI/MNDWI/BSI/B11 medianos y conteos SCL) pero **no** registra con
  qué código, función, versión ni remuestreo se calcularon. Si se usaron las funciones del proyecto
  (`compute_spectral_indices`), la independencia *metodológica* de la FASE B de la auditoría es
  parcial. El protocolo exige que toda auditoría futura declare la herramienta computacional
  empleada por métrica (re-implementación independiente, funciones del proyecto con commit citado,
  o herramienta externa) — ver §1.3.
- **M3-2 — Soporte de píxeles no declarado:** los conteos SCL de la auditoría en T0
  (10 m: 22+19+16 = 57 px) no coinciden con el soporte del polígono del detector (46 px citados en
  FASE D). La auditoría usó una definición de parche distinta sin declararla. **Regla que introduce
  el protocolo:** toda métrica satelital debe declarar su **dominio**: `fecha × (interior|buffer)
  × resolución × definición-de-soporte`. Esta regla es la generalización de la contradicción SCL
  encontrada (advertencia basada en T0/buffer frente a tabla basada en T1/interior) y se convierte
  en test de regresión (§3.2, R1–R3).

Otros items verificados y conformes: geometría reproyectada coincide al 100% con `report.json`
(`5.065,02 m²`, centroide exacto), hashes de artefactos locales completos, IDs y fechas STAC
consistentes, y el dictamen final respeta la regla de no determinación jurídica
(`legal_determination: false`).

### 1.3 Independencia metodológica vs independencia de fuente

Definición formal que adopta el protocolo (y que toda auditoría debe aplicar a su propia evidencia):

| Nivel | Definición | Ejemplo en RIO-0001 |
| --- | --- | --- |
| **Independencia de fuente** | Los *datos de entrada* provienen de adquisiciones distintas (otro sensor, otra fecha, otro proveedor o programa) | Las 4 escenas son adquisiciones independientes (`S2A/B/C`, 2023–2026), pero todas Sentinel-2 L2A vía el mismo proveedor (Earth Search) → independencia de fuente **parcial**: mismo programa de sensores, misma resolución, misma cadena de procesamiento L2A |
| **Independencia metodológica** | El *procesamiento* se realizó con código, algoritmo o personas distintas del proceso evaluado | Por confirmar (M3-1). Re-procesar Sentinel-2 con las mismas funciones del proyecto **no** es independencia metodológica |
| **Consecuencia operativa** | Aun con independencia de fuente completa, la **firma física es compartida**: a 10/20 m, H1 (excavación) y H3 (dinámica fluvial) permanecen indistinguibles por construcción. La evidencia multitemporal fortalece *persistencia del cambio*, nunca *confirmación de causa* | El dictamen `INDETERMINADO` de la auditoría es la única conclusión compatible con esta restricción |

Regla derivada: **ninguna evidencia satelital óptica en resolución nativa ≥ 10 m puede, por sí sola,
elevar un candidato por encima del nivel E1** (§4.5). La separación H1/H3 requiere evidencia
externa (VHR, campo o catastro).

---

## 2. Separación conceptual: detección de cambio (A) vs interpretación (B)

### 2.1 Definición formal

**A. Detección de cambio territorial** — afirmaciones medibles y reproducibles sobre el estado
observado. Campos actuales del sistema que pertenecen a A:

- Cambio espectral: `delta_mndwi_mean`, `delta_bsi_mean`, `delta_ndvi_mean`, `spectral_stats` (medianas por fecha, conteos SCL con dominio declarado).
- Cambio geométrico: `geometry_geojson`, `area_m2/ha` (POSGAR 2007), `rectangularity`, `elongation`.
- Persistencia y evolución temporal: `uncertainty.persistence_scenes_count`, serie multitemporal del dossier, `coarse_location_uid` (seguimiento entre épocas).
- Calidad de observación: `uncertainty.valid_pixel_fraction`, `cloud_shadow_adjacent`, `effective_resolution_m`, `water_stage_delta_pct`, `natural_dynamics_confounder_score`.

**B. Interpretación del cambio** — hipótesis sobre la causa, con grados de plausibilidad y siempre
revisables: probable actividad antrópica, dinámica fluvial, obra o infraestructura, cambio ambiental,
artefacto, **indeterminado**.

### 2.2 Regla de no conversión automática

> **Regla 2.2:** ningún resultado de A produce una conclusión de B por inferencia automática.
> Los componentes del sistema que mapean A→B (cribado heurístico, clasificación catastral, prioridad
> de inspección) deben etiquetarse como **hipótesis de trabajo** en todo artefacto que los presente,
> con la aclaración explícita de que no son dictamen (`is_statistical_probability=false`,
> `human_review_required=true` — ya implementado en Fase 3.1 y preservado aquí).

### 2.3 Fugas A→B catalogadas en el código vigente (no se corrigen en 3.2; se documentan)

Estas ambiguiedades son estructurales y aceptadas por conveniencia de trazabilidad. El protocolo
obliga a **leerlas** con el significado de la izquierda:

| Campo / concepto actual | Naturaleza real | Lectura correcta bajo este protocolo |
| --- | --- | --- |
| `anomaly_type` (`frente_excavacion_inundado`, `desmonte_ribera_acopio`, ...) | Hipótesis tipológica de B, generada por reglas de A | Es una **etiqueta de hipótesis de detección**, no una conclusión sobre causa |
| `candidate_uid` (incluye `anomaly_type` en su digest) | Identificador estable de A | Su componente tipológico **no** constituye afirmación de B; es key de trazabilidad |
| `field_inspection_recommended` (cribado heurístico) | Salida interpretativa derivada de A | Recomendación de trabajo, no dictamen |
| `priority` (`ALTA_FISCALIZACION_INMEDIATA`, ...) | Priorización operativa sobre A + catastro | Orden de cola de revisión, no juicio de mérito |
| `signal_score` / `confidence` | Puntaje compuesto de señal morfo-espectral | **No es probabilidad estadística** (documentado en el schema); nunca se cita como "% de certeza" |

---

## 3. Correcciones del dossier y tests de regresión

Se corrigen **presentación y provenance**, nunca umbrales ni parámetros de detección.

### 3.1 Correcciones identificadas (a partir de la meta-auditoría de RIO-0001)

- **C1 — Dominio de advertencias SCL:** toda advertencia SCL del dossier debe declarar su dominio
  completo: `fecha (T0/T1) × interior-polígono|buffer-40m × resolución (10m/20m)`. Hoy la advertencia
  puede provenir de T0 o del buffer mientras la tabla reporta T1/interior (contradicción detectada).
- **C2 — Interpretación de B11 y agua abierta:** el dossier debe incluir la regla de lectura física:
  `B11` bajo en T1 con `MNDWI` alto y `SCL=6` dominante indica **absorción de agua abierta en
  SWIR1**, no exposición mineral ni arena seca. Toda mención de B11 en discusión de causa debe ir
  acompañada de MNDWI/NDVI/SCL del mismo dominio.
- **C3 — Procedencia de cada métrica:** cada métrica citada en el dossier debe poder rastrearse a
  (a) campo de un artefacto hasheado (`report.json`/`manifest.json`), (b) cómputo declarado
  (función, remuestreo, dominio), o (c) fuente externa citada. Sin excepciones.
- **C4 — Observaciones vs conclusiones:** las secciones del dossier deben separar tipográficamente
  *observaciones* (valores medidos con dominio) de *interpretaciones* (hipótesis con plausibilidad).
  El formato actual de 4 niveles (Observación/Interpretación/Hipótesis/Verificación) se mantiene y
  se hace verificable por test.
- **C5 — Uso del score heurístico:** toda mención de `confidence`/`signal_score` debe acompañarse de
  la aclaración "puntaje de señal, no probabilidad estadística" (ya en el schema; falta exigirlo en
  la prosa del dossier).

### 3.2 Tests de regresión propuestos (aditivos; no alteran detección)

| ID | Test | Qué pina |
| --- | --- | --- |
| R1 | `test_dossier_scl_warnings_declare_domain` | Toda advertencia SCL generada menciona fecha(s) e interior/buffer que efectivamente la originan; prohibida la advertencia sin dominio |
| R2 | `test_dossier_metric_provenance_complete` | Cada valor numérico del dossier existe en `report.json` o deriva de un cómputo declarado en `inputs_provenance` |
| R3 | `test_dossier_b11_water_interpretation_guard` | Un candidato con `B11_T1` bajo + `MNDWI_T1` alto + `SCL=6` dominante **no** puede presentar texto de "exposición mineral/arena" sin la aclaración de absorción de agua |
| R4 | `test_dossier_score_language_guard` | El texto del dossier no usa "probabilidad", "certeza estadística" ni "% de confianza" para referirse al score heurístico |
| R5 | `test_audit_artifacts_provenance_schema` | Todo nuevo `spectral_audit_metrics.json` incluye, por métrica y por escena: función/computo declarado, remuestreo, dominio de conteo y SHA-256 del raster de origen |

R1–R4 son tests de prosa generada (inspección del `report.md` contra el payload estructurado);
R5 valida el esquema de provenance de futuras auditorías. Ninguno modifica `Settings`.

---

## 4. Protocolo de validación de candidatos

### 4.1 Unidad de validación: la ficha de caso

Cada candidato evaluado se materializa en una **ficha** (apéndice B) con campos obligatorios:

identificador estable (`candidate_uid`), polígono original (WGS84 + POSGAR con dominio declarado),
escenas y fuentes (IDs STAC + SHA-256 + proveedor), evidencia visual multitemporal (chips con
procedencia), hipótesis alternativas con plausibilidad (formato de la auditoría RIO-0001: H1..Hn con
estados `PLAUSIBLE_NO_CONFIRMADA` / `DESCARTADA` / `BAJA_PLAUSIBILIDAD`), revisión humana
independiente (ver §4.4), resultado de verificación, **nivel de evidencia** (§4.5), etiqueta de
referencia (§4.7) y limitaciones.

### 4.2 Marco muestral y muestreo estratificado

- **Marco:** candidatos emitidos por corridas **congeladas** (hoy: las 4 de Exp 002 + el par
  bitemporal 2024→2026), identificados por `candidate_uid`. Cada corrida aporta su marco completo
  (candidates + discarded log) para poder calcular tasas de descarte.
- **Estratos:** tipología de hipótesis de detección (`anomaly_type`) × zona geomorfológica. Dado el
  desbalance (la mayoría de los candidatos cae en `PLANICIE_INUNDABLE_E_ISLAS`), el muestreo es
  **estratificado con cuotas mínimas por estrato**, no proporcional.
- **Cuotas mínimas:** n ≥ 20 por estrato presente en el marco, o el total del estrato si es menor.
  Con n < 20, la métrica del estrato se reporta solo como exploratoria con IC.
- **Criterio de parada:** un estrato deja de muestrearse cuando el IC 95% de su métrica principal
  (§5) tiene ancho < 0.20, o cuando se agota el marco del estrato.
- **Los descartados también se muestrean** (muestra aleatoria de la bitácora, ≥ 20): son la mitad
  de la historia — un descarte erróneo de un foco real es tan grave como un falso positivo
  (antecedente: los 3 focos de RIO-0184/0225/0199/0365 que el piso de área había eliminado).

### 4.3 Procedimiento de ciego (obligatorio)

1. El revisor recibe **únicamente**: chips RGB/falso-color por fecha (misma ventana espacial),
   polígono superpuesto, fechas y resolución. **No recibe** `anomaly_type`, `confidence`,
   `geomorphological_zone`, prioridad, clasificación catastral ni el dictamen del cribado.
2. El revisor etiqueta **primero** (física del cambio + ranking de hipótesis B con plausibilidad).
3. Recién entonces se desvela la salida del detector, y se registran **concordancias y
   discrepancias** como datos del estudio (son, precisamente, lo que se mide).
4. Se prohíbe al revisor acceder al catastro o a resultados de otros revisores antes de etiquetar.
5. La revisión se registra como `HUMAN_EXPERT` con `expert_declaration_recorded=true`; la
   acreditación del revisor la valida el organismo (el flag registra la declaración, no la
   verifica — decisión de Fase 3.1 que este protocolo respeta y documenta).

### 4.4 Criterios de independencia del revisor

- No haber participado en el desarrollo, calibración o auditoría del detector ni de sus parámetros.
- Sin acceso al catastro de permisos antes del etiquetado en ciego.
- Identificación documentada (`evaluator_id`), fecha de revisión, declaración de conflictos de interés.
- Capacidad técnica declarada para interpretación geomorfológica fluvial en imágenes ópticas.

### 4.5 Jerarquía de admisibilidad de evidencia

| Nivel | Definición | Puede elevar un caso hasta |
| --- | --- | --- |
| **E0** | Solo-satelital en resolución nativa (≥ 10 m), cualquier cantidad de fechas | Cambio real persistente (A) + ranking de hipótesis B. **Nunca confirmación de causa** |
| **E1** | E0 + evidencia contextual pública verificada (caminos/parcelas OSM confirmados manualmente, hidrografía IGN, hidrometría oficial INA) | Refuerza o debilita hipótesis; no confirma causa |
| **E2** | Imagen VHR verificada (≤ 1 m, con fecha y fuente citadas) mostrando geometría antrópica inequívoca (taludes rectilíneos, dragas, dársenas, acopios) o ausencia de la misma en varias fechas | Puede confirmar/descartar H1 vs H3 |
| **E3** | Constatación presencial documentada (acta de inspección con coordenadas, fecha, funcionario) y/o registro catastral oficial contrastado | Nivel de referencia pleno |

Regla de adquisición: toda evidencia E2/E3 se archiva con hash SHA-256, fecha, fuente y titularidad
declarada en la ficha; evidencia sin provenance no es admisible.

### 4.6 Regla estructural: el conjunto de referencia etiqueta física e hipótesis, no legalidad

> Sin catastro oficial cargado, **nadie** puede etiquetar si una extracción fue o no autorizada.
> Las etiquetas de referencia se limitan a: (a) realidad del cambio (A), (b) ranking de hipótesis de
> causa (B) con nivel de evidencia, y (c) para el subconjunto con catastro oficial verificado,
> estado catastral. Las métricas catastrales (§5) se reportan solo para ese subconjunto y siempre
> como métricas del **cruce**, no del detector espectral.

### 4.7 Categorías de referencia y criterios de etiquetado

| Categoría | Criterio de etiquetado (mínimo) |
| --- | --- |
| `CAMBIO_ANTROPICO_CONFIRMADO` | H1 (o H2) mejor explicada con evidencia ≥ E2 (VHR) o ≥ E3 (campo/catastro) |
| `DINAMICA_NATURAL_CONFIRMADA` | H3 (u otra natural) mejor explicada con evidencia ≥ E2/E3, o E1 con signatura repetida estacional y contexto geomorfológico concordante documentado |
| `CAMBIO_REAL_CAUSA_INDETERMINADA` | Cambio real y persistente (A verificado en ≥ 2 escenas independientes) sin evidencia suficiente para separar hipótesis (**es el estado esperado mayoritario a resolución nativa**) |
| `SIN_CAMBIO_REAL` | Re-análisis no reproduce el cambio (artefacto, desalineación, nube no detectada) |
| `INDETERMINADO_REVISION_IMPOSIBLE` | Imposible revisar (datos perdidos, inaccesibilidad, conflicto de jurisdicción); se reporta aparte y no entra al denominador de métricas de rendimiento |

- `INDETERMINADO` **no es un fracaso del protocolo**: es el resultado científicamente correcto
  cuando la física observable no separa hipótesis (es lo que ocurrió con RIO-0001 y debe ocurrir con
  la mayoría de E0).
- Dos revisores independientes por caso cuando sea posible; discrepancias se resuelven por tercer
  revisor o se escalan al estado del caso con ambas etiquetas registradas (nunca se promedian).

### 4.8 Extensión de estados de caso (propuesta, no implementada)

El workflow actual (`CaseStatusLiteral`) termina en `ARCHIVADO_FALSO_POSITIVO` o estados de
verificación, pero no tiene un cierre `INDETERMINADO`. Propuesta para la fase de implementación:
agregar `CAMBIO_REAL_INDETERMINADO` como estado terminal de caso (manteniendo `append-only` e
`idempotencia`), de modo que el registro institucional refleje la epistemología del protocolo.

---

## 5. Métricas (definiciones formales — **no computar todavía**)

> **§5.7 (Guard):** ninguna métrica de esta sección se calcula hasta que exista un conjunto de
> referencia independiente, suficientemente documentado y **sancionado** por el organismo receptor.
> Antes de eso, solo se reportan *conteos descriptivos* del protocolo (fichas completadas, estratos
> cubiertos, estado del muestreo), nunca rendimiento.

Definiciones (denominadores explícitos):

- **Precisión (PPV) por estrato** = TP / (TP + FP), sobre candidatos etiquetados como
  `CAMBIO_ANTROPICO_CONFIRMADO` (referencia) y retenidos por el detector. El denominador es la
  **muestra etiquetada**, no el marco completo; se reporta siempre con n y IC.
- **Recall (sensibilidad)** = TP / (TP + FN), donde FN requiere muestrear también los **descartados**
  (§4.2) — un FN es un candidato real etiquetado `CAMBIO_ANTROPICO_CONFIRMADO` que el detector
  descartó o no emitió. Sin muestreo de descartados, el recall **no es calculable** y se declara
  `NO_CALCULADO`.
- **Falsos positivos** = candidatos retenidos etiquetados `DINAMICA_NATURAL_CONFIRMADA` o
  `SIN_CAMBIO_REAL`.
- **Indeterminados** = fracción de candidatos etiquetados `CAMBIO_REAL_CAUSA_INDETERMINADA`.
  Se reporta **como métrica propia** (es la medida de la capacidad resolutiva del sistema a su
  resolución), nunca se pliega en FP ni FN.
- **Rendimiento por tipología** (según `anomaly_type` de detección) y **por unidad geomorfológica**:
  las métricas anteriores estratificadas; con n < 20, solo exploratorias con IC.
- **Carga de revisión humana**: fichas abiertas vs cerradas, tiempo medio de revisión por nivel de
  evidencia, casos re-abiertos, derivados de los eventos del workflow SQLite (fuente ya auditable).
- Reporte obligatorio junto a toda métrica: marco, muestra, estrato, n, nivel de evidencia
  alcanzado por caso, y versión del protocolo. Una métrica sin estos metadatos no es publicable.

---

## 6. Evaluación del estado actual (los 5 puntos de la consigna)

### 6.1 Qué partes del protocolo actual son válidas

- **Motor A:** motor espectral/geomorfológico con parámetros congelados y verificables
  (42 parámetros en `get_calibration_parameters()`), corrección geométrica con soporte de medio
  píxel, registros completos de descartes con motivo tipado — validado por la regresión 4/4 de focos
  costeros y por `exp_002_summary.json`.
- **Identificadores:** `candidate_uid` determinista y `coarse_location_uid` para seguimiento
  longitudinal — suficientes como claves del protocolo.
- **Integridad documental:** publicación atómica por generación con manifest SHA-256,
  `verify_report_generation_integrity`, bitácora append-only SQLite/JSONL con cadena de hashes,
  idempotencia y reconstrucción exacta desde JSONL.
- **Separación de modalidades** (heurístico / VLM externo / experto) con hashes de imágenes y
  declaración (`expert_declaration_recorded`) — base suficiente para la revisión humana del protocolo.
- **La auditoría de RIO-0001 como plantilla de ficha:** estructura FASES A–F, hipótesis alternativas
  con estados, verificaciones pendientes y dictamen `INDETERMINADO` — se adopta como formato
  canónico de ficha (apéndice B).

### 6.2 Qué limitaciones persisten

- **Resolución física:** a 10/20 m, H1 vs H3 es indistinguible por construcción (§1.3) → la mayoría
  de los casos terminará en `CAMBIO_REAL_CAUSA_INDETERMINADA` sin evidencia E2/E3. Esto es una
  propiedad del sensor, no un defecto del software.
- **Catastro:** sin padrones oficiales cargados, las métricas catastrales no son calculables y la
  verificación administrativa depende de los organismos.
- **Trazabilidad de auditorías previas:** brechas M3-1/M3-2 (§1.2) — resueltas hacia adelante por
  R5, no retroactivamente.
- **Desbalance de marcos:** el piso de 800 m² para desmonte concentra candidatos en
  `PLANICIE_INUNDABLE_E_ISLAS`; el muestreo estratificado mitiga el sesgo de evaluación, pero el
  volumen de revisión humana sigue siendo la restricción operativa principal.
- **Escalas de evidencia no unificadas:** falta un mecanismo institucional para adquirir y archivar
  evidencia E2/E3 con provenance (§6.4).

### 6.3 Qué cambios de arquitectura son necesarios (enumerados, no implementados en 3.2)

1. **Ficha de caso persistente:** materializar el formato de apéndice B como artefacto versionado
   ligado al workflow (nuevo tipo de evento o subdirectorio por caso en `reports/`), con la ficha
   incluida en el manifest de cada generación.
2. **Estado terminal `CAMBIO_REAL_INDETERMINADO`** en `CaseStatusLiteral` (§4.8).
3. **Dominio explícito en metadatos de incertidumbre:** ampliar `UncertaintyMetadata` con campos
   que declaren la provenance de la advertencia SCL (fecha × interior/buffer × resolución),
   sin alterar el cálculo (C1/R1).
4. **Provenance metodológica de auditorías:** esquema R5 como requisito de esquema para
   `spectral_audit_metrics.json` futuros.
5. **Cola de revisión ciega:** vista/exportación del marco muestral que oculte los campos B al
   revisor (§4.3) — puede ser una herramienta de exportación del workflow, no un cambio del detector.
6. **Extensión de triggers de CI** a ramas `fix/**`/`docs/**`/`chore/**` para que los artefactos del
   protocolo pasen por el mismo pipeline.

### 6.4 Qué evidencia externa se necesita

- **Catastros oficiales** de concesiones/permisos por provincia y organismo (Entre Ríos, Santa Fe,
  y jurisdicciones de expansión), con fecha de vigencia y actividad — único camino a métricas
  catastrales y a la mitad "autorizada" del problema.
- **Imágenes VHR** (Planet ~3 m, Maxar/SkySat submétrico, o vuelo UAV institucional) para la
  separación H1/H3 en casos muestreados (verificaciones pendientes de RIO-0001).
- **Inspecciones de campo** coordinadas (Prefectura / organismos ambientales) con actas que incluyan
  coordenadas, fecha y funcionario — nivel E3.
- **Serie hidrométrica oficial continua** (INA A5) ya integrada; mantener procedencia por punto.
- **Protocolo de adquisición de evidencia:** quién compra/quÉ organismo provee VHR, bajo qué
  licencia de uso, y cómo se archiva con hash — decisión institucional previa a cualquier fase de
  muestreo con E2.

### 6.5 Cómo se validará el propio protocolo

1. **Validación estructural:** las fichas producidas cumplen el esquema (apéndice B) sin campos
   faltantes; los tests R1–R5 pasan en CI.
2. **Validación de reproductibilidad del protocolo:** dos auditores independientes aplican el
   protocolo a la **misma ficha de RIO-0001** y alcanzan la misma etiqueta de referencia
   (`CAMBIO_REAL_CAUSA_INDETERMINADA`) de forma independiente y ciega. Concordancia documentada.
3. **Validación de sensibilidad del muestreo:** el protocolo se aplica primero a un estrato piloto
   (n pequeño) y se verifica que las cuotas, el ciego y la jerarquía de evidencia operan sin
   ambigüedad de aplicación (bitácora de decisiones del piloto).
4. **Validación de no-contaminación:** verificación de que ninguna decisión de implementación
   posterior al sancionado del conjunto de referencia modifica parámetros del detector
   (`get_calibration_parameters()` idéntico antes/después, verificado por hash en CI).
5. **Validación de utilidad:** el organismo receptor confirma que las fichas + métricas sirven para
   priorizar inspecciones (criterio declarado en §5, "carga de revisión humana").

---

## 7. Plan de implementación secuencial

| Fase | Contenido | Entregables | Criterio de salida | Qué NO se hace |
| --- | --- | --- | --- | --- |
| **P0 — Meta-auditoría formal** | Ejecutar checklist §1.1 sobre `reports/audits/RIO-0001/` (y futuras auditorías); automatizar M2 y la validación de hashes | `docs/validation/META_AUDIT_RIO_0001.md` + script de verificación de hashes/inventario | Todos los checks M1–M6 con estado (✅/⚠️/❌) y hallazgos M3-1/M3-2 cerrados documentalmente | Recalcular el detector; modificar la auditoría original |
| **P1 — Tests de regresión del dossier (C1–C5, R1–R5)** | Implementar R1–R5 (aditivos, sin umbrales); corregir la prosa del generador de dossiers donde falle | Tests en CI + correcciones de `dossier_generator.py` | 58+N tests verdes, CI success | Cambiar `Settings`, máscaras o umbrales |
| **P2 — Esquema de ficha y estados** | Agregar `CAMBIO_REAL_INDETERMINADO`; materializar ficha versionada por caso; ampliar `UncertaintyMetadata` con dominio SCL | Cambios de schema + migración compatible de estados existentes | Backward-compatible: expedientes existentes siguen legibles | Re-etiquetar casos históricos |
| **P3 — Cola de revisión ciega** | Exportador del marco muestral con campos B ocultos + registro de revisión ciego→desvelado | Herramienta/workflow de exportación + plantilla de acta de revisión | Piloto del §6.5.3 en un estrato con n ≥ 5 | Integrar revisores automáticos |
| **P4 — Muestreo estratificado piloto** | Aplicar §4.2 a un AOI congelado; completar fichas con E0 (+E1 cuando exista) | Fichas del piloto + reporte descriptivo de conteos (§5.7 guard) | Piloto revisado por organismo receptor | Cómputo de precisión/recall |
| **P5 — Sanción del conjunto de referencia y métricas** | Con catastro y/o evidencia E2/E3 disponible, sancionar referencia; recién entonces computar §5 | Primer reporte de métricas con metadatos completos | Sanción documentada por el organismo | Ajustar parámetros del detector en base a métricas (prohibido en 3.2; sólo permite planificar 3.3) |

Dependencias externas críticas: P4 requiere acceso al marco congelado y capacidad de revisión
humana; P5 requiere catastros y/o evidencia E2/E3 (§6.4). El protocolo declara explícitamente que
**P5 puede no ocurrir hasta que la institución provea esas fuentes**, y que eso es un resultado
aceptable del sistema, no un fracaso.

---

## Apéndice A — Mapa de automatización de la meta-auditoría

| Check | Entradas | Procedimiento | Salida |
| --- | --- | --- | --- |
| M1 | `evidence_inventory.json`, rasters locales | Re-hash SHA-256 de cada artefacto; comparar | `verify_hash`: ✅/❌ por artefacto |
| M2 | `scene_id` + `datetime` de cada escena | Consulta STAC por item id (proveedor declarado en la ficha) | `stac_match`: ✅/❌ |
| M3/M4 | `spectral_audit_metrics.json` | Verificar presencia de: computo declarado, remuestreo, dominio, SHA de origen (R5) | `method_provenance`: completa/parcial/ausente |
| M5 | `AUDIT_*.md` + `audit_findings.json` | Extraer afirmaciones (secciones FASE) y mapear a artefactos citados | Matriz afirmación→fuente |
| M6 | Informe + `pending_verifications.md` | Verificar limitaciones materiales declaradas vs hallazgos | Lista de limitaciones no documentadas |

## Apéndice B — Ficha de caso (esquema canónico)

```yaml
ficha_version: "phase3.2.v1"
identificador:
  candidate_uid: ""          # estable entre corridas
  coarse_location_uid: ""    # celda 100 m para seguimiento longitudinal
  anomaly_id_corrida: ""     # RIO-XXXX de la corrida congelada de origen
  caso_id: ""                # CASE-... del workflow SQLite
geometria:
  poligono_wgs84: {}         # geometry_geojson
  crs_metrico: 5347          # POSGAR 2007 faja
  area_m2: 0.0               # dominio: polígono con soporte de medio píxel
escenas_y_fuentes:
  - scene_id: ""             # ID STAC
    fecha: ""                # YYYY-MM-DD
    proveedor: ""            # earth_search | planetary | otro
    sha256_rasters: {vnir_10m: "", swir_scl_20m: ""}
  rol: t0 | intermedia | t1
evidencia_visual:
  chips: []                  # rutas + sha256 + ventana espacial + resolución
hipotesis_alternativas:      # formato canónico de la auditoría RIO-0001
  - id: "H1"
    descripcion: ""
    estado: "PLAUSIBLE_NO_CONFIRMADA | BAJA_PLAUSIBILIDAD | DESCARTADA"
    fundamento: ""           # referencia a artefacto/fuente
revision_humana_independiente:
  ciego_aplicado: true
  evaluator_id: ""           # HUMAN_EXPERT, declaración registrada
  fecha_revision: ""
  etiqueta_pre_desvelado: "" # antes de ver la salida del detector
  concordancias_discrepancias: ""
resultado_verificacion:
  nivel_evidencia: "E0 | E1 | E2 | E3"
  etiqueta_referencia: ""    # §4.7
  limitaciones: []
provenance:
  protocolo: "PROTOCOL_INDEPENDENT_VALIDATION.md @ <commit>"
  calibracion_detector: "42 parámetros congelados (hash de get_calibration_parameters())"
```

## Apéndice C — Glosario

- **Candidato:** geometría emitida por el detector con metadatos de incertidumbre; nunca una
  confirmación.
- **Descartado:** componente suprimido por controles naturales/SCL/área, registrado en la bitácora
  con motivo tipado; también muestreable como FN potencial.
- **Estrato:** combinación tipología × zona geomorfológica del marco muestral.
- **Ciego:** evaluación sin acceso a campos interpretativos del detector (§4.3).
- **Nivel de evidencia (E0–E3):** jerarquía de admisibilidad (§4.5).
- **Sanción:** acto institucional documentado por el cual un conjunto de fichas se declara
  conjunto de referencia.
- **`INDETERMINADO`:** resultado científicamente correcto cuando la evidencia disponible no separa
  hipótesis; estado terminal de primera clase, nunca se pliega en métricas binarias.
