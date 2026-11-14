# RíoAlerta (`rio-alerta-mcp`)

Servidor **FastMCP** para la vigilancia satelital multitemporal y fiscalización ambiental de **extracción ilegal de áridos** (arena, canto rodado, ripio) en cursos de agua de Argentina, orientado a autoridades ambientales provinciales, municipios, Prefectura Naval Argentina y organismos de recursos hídricos.

## Arquitectura técnica

- **Proyección métrica oficial (POSGAR 2007):** selección automática de fajas Gauss-Krüger (`EPSG:5343` a `EPSG:5349`) según longitud geográfica para cálculos exactos de superficie ($\mathrm{m^2}$ / $\mathrm{ha}$) y distancias.
- **Ingesta eficiente por HTTP Range Requests (`GDAL/VSICURL`):** lectura exclusiva de ventanas de interés sobre Cloud-Optimized GeoTIFFs (COG) de Sentinel-2 L2A (Element 84 Earth Search / Microsoft Planetary Computer) con topes estrictos de RAM y píxeles.
- **Motor multitemporal (`T0` vs `T1`):**
  - `ΔMNDWI` (`B03`, `B11`): detección de retracción/avance de bancos de arena, excavaciones inundadas y alteración de cauces.
  - `ΔBSI` (`B02`, `B04`, `B08`, `B11`) y `ΔNDVI` (`B04`, `B08`): detección de desmontes de ribera, playas de acopio, caminos de acceso y movimiento de suelo.
- **Cruce catastral de permisos (`data/permits.gpkg`):** evaluación geométrica y temporal (`fecha_desde` / `fecha_hasta`) e hidrografía IGN WFS / OpenStreetMap Overpass para clasificar anomalías en:
  - `EXTRACCION_AUTORIZADA`
  - `EXCESO_DE_POLIGONO`
  - `PERMISO_VENCIDO`
  - `EXTRACCION_SIN_PERMISO`
- **Inspección multimodal y expediente reproducible:** láminas comparativas `T0`/`T1`, Juez VLM fluvial para discriminar dinámica fluvial natural de frentes extractivos antrópicos, y actas técnicas reproducibles en Markdown y JSON con trazabilidad STAC.

## Estructura del proyecto

```text
rio-alerta-mcp/
├── .github/workflows/ci.yml
├── .env.example
├── Dockerfile
├── pyproject.toml
├── src/
│   ├── config.py          # Configuración CWD-safe, GDAL/VSICURL y fajas POSGAR 2007
│   ├── schemas.py         # Modelos Pydantic v2 estrictos
│   ├── server.py          # Servidor FastMCP (RioAlerta-MCP)
│   └── core/
│       └── crs_manager.py # Gestión geodésica POSGAR 2007 (EPSG:5343-5349) y WGS84
└── tests/
    ├── conftest.py
    └── test_phase1_scaffolding.py
```

## Desarrollo local y pruebas

```bash
pytest -v
```
