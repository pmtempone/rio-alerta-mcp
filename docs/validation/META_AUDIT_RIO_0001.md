# META-AUDITORÍA P0 — `RIO-0001` (Fase 3.2)

> **Protocolo rector:** [`PROTOCOL_INDEPENDENT_VALIDATION.md`](PROTOCOL_INDEPENDENT_VALIDATION.md) §1 y Apéndice A.
> **Verificado con:** `scripts/verify_audit_inventory.py` (`meta_audit_version: phase3.2.p0.v1`).
> **Reporte JSON completo:** [`meta_audit_rio_0001_results.json`](meta_audit_rio_0001_results.json).
> **Resultado global: `PASS=12 · WARN=1 · MANUAL=2 · FAIL=0`** — la auditoría de `RIO-0001` es
> íntegra y reproducible en sus artefactos; persisten dos hallazgos de provenance **ya documentados
> por el protocolo** (M3-1/M3-2) y dos checks que requieren revisión humana (M5/M6).

---

## 1. Ejecución

| Parámetro | Valor |
| --- | --- |
| Directorio auditado | `reports/audits/RIO-0001/` |
| `producer_commit` de la auditoría | `1d13eb7` (inventario) |
| Modo | Offline (M1, R5) + en línea (M2 contra Earth Search STAC) |
| Verificado | 2026-11-17 (UTC, ver marca en el JSON) |

Los artefactos de la generación auditada (`data/reports/demo_real/…`) y los rasters
(`data/experiments/…`) son **locales por política de `.gitignore`**; sus hashes quedaron
verificados en esta ejecución y quedan registrados en el inventario y en el manifest de la
generación. Un clon fresco puede re-verificarlos tras regenerar los datos con los scripts de
experimento (procedimiento ya documentado en `AGENTS.md`).

## 2. Resultados por check

### M1 — Integridad de artefactos locales (`PASS 8/8`)

| Artefacto | Estado | Detalle |
| --- | --- | --- |
| `report.md` (GEN-20261116T220512Z-1e9574ce) | ✅ PASS | SHA-256 re-calculado = declarado |
| `report.json` (idem) | ✅ PASS | SHA-256 re-calculado = declarado |
| `manifest.json` (idem) | ✅ PASS | SHA-256 re-calculado = declarado |
| `t0_S2B_20HQK_20230928_0_L2A_vnir10m.tif` | ✅ PASS | SHA-256 re-calculado = declarado |
| `t0_S2B_20HQK_20230928_0_L2A_swir_scl20m.tif` | ✅ PASS | SHA-256 re-calculado = declarado |
| `t1_S2C_20HQK_20260917_0_L2A_vnir10m.tif` | ✅ PASS | SHA-256 re-calculado = declarado |
| `t1_S2C_20HQK_20260917_0_L2A_swir_scl20m.tif` | ✅ PASS | SHA-256 re-calculado = declarado |
| Cruzado `report_generation_id` (inventario ↔ manifest) | ✅ PASS | Consistente |

**Conclusión M1:** los artefactos que la auditoría declara usar son íntegros y no fueron
alterados desde su registro. La trazabilidad del expediente (`report.json` ↔ manifest) es
consistente.

### M2 — Escenas y fechas contra el catálogo STAC (`PASS 4/4`)

| Scene ID | Fecha declarada | STAC | Nubosidad | Estado |
| --- | --- | --- | --- | --- |
| `S2B_20HQK_20230928_0_L2A` | 2023-09-28 | 2023-09-28 | — | ✅ PASS |
| `S2A_20HQK_20241116_0_L2A` | 2024-11-16 | 2024-11-16 | consistente (≤0.05 pp) | ✅ PASS |
| `S2B_20HQK_20251126_1_L2A` | 2025-11-26 | 2025-11-26 | consistente (≤0.05 pp) | ✅ PASS |
| `S2C_20HQK_20260917_0_L2A` | 2026-09-17 | 2026-09-17 | — | ✅ PASS |

**Conclusión M2:** las cuatro escenas (dos del par T0/T1 del detector y dos intermedias de la
auditoría) existen en Earth Search y sus fechas coinciden exactamente con las declaradas.
Las nubosidades declaradas para las escenas intermedias coinciden con `eo:cloud_cover`.

### R5 (M3/M4) — Provenance metodológica del análisis espectral (`WARN 1`)

- **Estado:** ⚠️ WARN — hallazgo **esperado y ya documentado** como M3-1 en el protocolo (§1.2).
- **Detalle:** `spectral_audit_metrics.json` no declara, por escena ni por métrica, con qué código
  se calcularon los índices, qué remuestreo se aplicó ni cuál es el dominio de conteo de los
  píxeles SCL (fallos del hallazgo M3-2: 57 px de parche de la auditoría vs 46 px del soporte del
  detector, sin declaración).
- **Consecuencia:** la independencia **metodológica** de la FASE B de la auditoría queda
  *parcial* (no acreditada) — la independencia de *fuente* es parcial por diseño (4 adquisiciones
  independientes del mismo programa Sentinel-2 vía el mismo proveedor, ver protocolo §1.3).
- **Consecuencia operativa (ya establecida por la propia auditoría):** la evidencia multitemporal
  fortalece la **persistencia** del cambio; no separa H1 (extracción) de H3 (dinámica fluvial).
  El dictamen `INDETERMINADO` se mantiene.
- **Remediación:** hacia adelante, toda auditoría bajo este protocolo debe emitir métricas con el
  esquema R5 completo (claves `compute_method`, `resampling`, `count_domain`, `source_sha256`);
  el check del script pasa a `PASS` cuando el esquema se cumple (verificado en tests).

### M5 / M6 — Revisión humana (`MANUAL 2`)

- **M5 (mapeo afirmación→fuente):** las secciones FASE A–F del informe citan artefactos con hash
  para sus afirmaciones cuantitativas; la verificación exhaustiva de cada afirmación de texto es
  manual y queda como tarea del revisor designado (procedimiento en protocolo §1.1).
- **M6 (limitaciones documentadas):** resolución (10/20 m), catastro `NO_DISPONIBLE`, Overpass
  `HTTP 406` y ausencia de VHR están declaradas en el informe y en `pending_verifications.md`
  (4 verificaciones externas listadas). Confirmación de completitud: manual.

## 3. Dictamen de la meta-auditoría

1. **Íntegra:** M1 8/8 y M2 4/4 — los insumos y artefactos de la auditoría son los declarados.
2. **Reproducible:** la geometría, las fechas y los IDs de escenas se verifican de forma
   independiente del proceso que las produjo (checks automatizados en este script).
3. **Con provenance metodológica parcial (M3-1/M3-2):** no invalida el dictamen `INDETERMINADO`,
   pero **obliga** a que las auditorías futuras (y las métricas de referencia del protocolo)
   incluyan el esquema R5. Se registra como la primera entrada de la bitácora de decisiones del
   protocolo.
4. **El dictamen final de la auditoría se sostiene:** `RIO-0001` permanece
   **`INDETERMINADO`** (cambio real y persistente 2023–2026 sobre `5.065,02 m²` en
   `PLANICIE_INUNDABLE_E_ISLAS`; H1 y H3 ambas plausibles; sin determinación jurídica).

## 4. Estado de avance del plan P0–P5

| Fase | Estado |
| --- | --- |
| P0 — Meta-auditoría formal | ✅ **Completada** (este informe + script + tests + hallazgos M3-1/M3-2) |
| P1 — Tests de regresión del dossier (R1–R4) | ⏳ Siguiente; requiere decisión sobre C1–C5 |
| P2 — Esquema de ficha y estados | ⏳ |
| P3 — Cola de revisión ciega | ⏳ |
| P4 — Muestreo estratificado piloto | ⏳ |
| P5 — Sanción de referencia y métricas | ⏳ (bloqueada por evidencia externa: catastro/VHR/campo) |
