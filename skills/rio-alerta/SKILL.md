---
name: rio-alerta
description: Ejecuta y opera el servidor FastMCP RíoAlerta (rio-alerta-mcp) para vigilancia satelital multitemporal de extracción de áridos en ríos argentinos mediante Sentinel-2 L2A, proyección POSGAR 2007 (EPSG:5343-5349) y controles de dinámica fluvial. Usar cuando el usuario pida analizar un tramo fluvial argentino, detectar anomalías T0 vs T1, resolver fajas POSGAR 2007, correr experimentos Sentinel-2 o verificar el estado del servidor MCP.
---

# Skill: Operación de RíoAlerta (`rio-alerta-mcp`)

Esta guía permite a cualquier agente (Antigravity, OpenCode, Claude Code, Codex) instalar, verificar, ejecutar y operar **RíoAlerta** de forma reproducible y científicamente prudente.

## 1. Ubicación y Entorno Virtual

El proyecto debe ejecutarse siempre desde su propio entorno virtual `.venv` (Python 3.11+):

```bash
cd /Users/pablotempone/rio-alerta-mcp   # o ruta del repo clonado
source .venv/bin/activate
```

Si `.venv` no existe:
```bash
uv venv .venv --python 3.11
.venv/bin/pip install -e ".[dev]"
```

## 2. Verificación Rápida antes de Operar

Antes de lanzar análisis o modificar código, verificar salud del entorno:

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src
.venv/bin/pytest -v
```

## 3. Cómo Conectar o Invocar el Servidor MCP

### Opción A: Cliente MCP (Antigravity / Claude / OpenCode)
Registrar el servidor en la configuración MCP usando `stdio`:
```json
{
  "mcpServers": {
    "rio-alerta": {
      "command": "/Users/pablotempone/rio-alerta-mcp/.venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "/Users/pablotempone/rio-alerta-mcp"
    }
  }
}
```

### Opción B: Invocación Directa por Python (CLI / Scripts)
Si no hay cliente MCP conectado, importar directamente las funciones de `src/server.py` o `src/core/`:
```bash
.venv/bin/python -c "from src.server import tool_server_status; print(tool_server_status())"
```

## 4. Flujo de Trabajo Estándar con las Herramientas MCP

1. **Verificar estado y configuración:**
   Llamar a `tool_server_status()` para confirmar rutas (`data/`, `processed/`, `reports/`), fajas POSGAR y límites de memoria.
2. **Resolver CRS oficial argentino (POSGAR 2007):**
   Llamar a `tool_resolve_posgar_crs(lon, lat)` para obtener el código `EPSG:5343–5349` correspondiente al tramo fluvial.
3. **Calcular diferencias espectrales (`ΔMNDWI`, `ΔBSI`, `ΔNDVI`):**
   Llamar a `tool_compute_spectral_changes(t0_path, t1_path, t0_swir_20m_path, t1_swir_20m_path)` respetando bandas VNIR `10 m` y SWIR+SCL `20 m`.
4. **Detectar anomalías morfológicas candidatas:**
   Llamar a `tool_detect_extraction_anomalies(t0_path, t1_path, t0_swir_20m_path, t1_swir_20m_path, min_area_m2=1600.0)`.
5. **Evaluar piloto Paraná Medio:**
   Llamar a `tool_evaluate_parana_pilot(offline_mode=True)` (o `False` para consultar STAC en vivo).

## 5. Ejecución de Experimentos Reproducibles

Para correr el Experimento 001 (Paraná Medio, `2024-11-06 -> 2026-11-08`):
```bash
.venv/bin/python scripts/run_exp_001_parana_medio.py
```
Los resultados se guardan en `data/experiments/exp_001_parana_medio/` (GeoJSON, GeoPackage, láminas PNG, `experiment_summary.json` e `INFORME_EXPERIMENTO_PARANA_MEDIO.md`).

## 6. Reglas Estrictas de Interpretación y Dominio

- **No afirmar ilegalidad de forma autónoma:** toda geometría detectada es una *anomalía morfológica candidata* con `UncertaintyMetadata`. Requiere cruce catastral oficial (`permits.gpkg`) y revisión humana.
- **No inventar datos:** nunca fabricar alturas hidrométricas, permisos ni coordenadas. Consultar `https://alerta.ina.gob.ar/pub/datos` para alturas oficiales PNA/INA.
- **No mezclar dominios:** este repositorio es exclusivamente para vigilancia fluvial de áridos en Argentina; no introducir referencias arqueológicas.
