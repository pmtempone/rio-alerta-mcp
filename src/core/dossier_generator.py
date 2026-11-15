"""Generador de Dossiers de Investigación Territorial (P1).

Produce informes auditables, comprensibles y accionables para organismos públicos
e investigadores, con 9 secciones fijas y separación explícita de los 4 niveles de
conocimiento: Observación, Interpretación, Hipótesis y Verificación.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _fmt_num(val: Optional[float], decimals: int = 3, suffix: str = "") -> str:
    if val is None:
        return "Sin dato"
    return f"{val:.{decimals}f}{suffix}"


def _format_scl_counts(scl_counts: Optional[Dict[str, int]]) -> str:
    if not scl_counts:
        return "Sin conteo SCL registrado"
    scl_names = {
        "4": "Vegetación (4)",
        "5": "Suelo desnudo/arena (5)",
        "6": "Agua (6)",
        "7": "No clasificado/baja prob. nube (7)",
        "8": "Nube media (8)",
        "9": "Nube alta (9)",
        "10": "Cirro (10)",
    }
    parts = [f"{scl_names.get(str(k), f'Clase {k}')}: {v} px" for k, v in sorted(scl_counts.items())]
    return "; ".join(parts)


def generate_candidate_dossier(
    feature: Dict[str, Any],
    detection_metadata: Dict[str, Any],
    aoi_name: str = "Tramo fluvial monitoreado",
    period_label: str = "T0 -> T1",
    multitemporal_series: Optional[Sequence[Dict[str, Any]]] = None,
    map_artifacts: Optional[Sequence[str]] = None,
    cadastre_note: Optional[str] = None,
) -> str:
    """Genera un Dossier de Investigación Territorial de 9 secciones para un candidato.

    Separa explícitamente:
    - Observación (mediciones satelitales e hidrométricas directas)
    - Interpretación (qué procesos físicos podrían explicar las mediciones)
    - Hipótesis (posibles actividades o dinámicas compatibles)
    - Verificación (acciones externas requeridas: campo, catastro, permisos)
    """
    props = feature.get("properties", feature)
    fid = str(feature.get("id") or props.get("id") or "RIO-SIN-ID")
    atype = str(props.get("anomaly_type", "desconocido"))
    zone = str(props.get("geomorphological_zone", "NO_CLASIFICADA"))
    area_m2 = float(props.get("area_m2", 0.0))
    area_ha = float(props.get("area_ha", area_m2 / 10000.0))
    rect = float(props.get("rectangularity", 0.0))
    elong = float(props.get("elongation", 1.0))
    conf_raw = float(props.get("confidence", 0.0))
    crs_epsg = props.get("crs_epsg", detection_metadata.get("crs_metric_epsg", 5347))
    centroid_wgs = props.get("centroid_wgs84", (None, None))
    centroid_pos = props.get("centroid_posgar", (None, None))
    dist_water_m = props.get("distance_to_water_t0_m")

    dm_mean = props.get("delta_mndwi_mean")
    db_mean = props.get("delta_bsi_mean")
    dn_mean = props.get("delta_ndvi_mean")

    stats = props.get("spectral_stats") or {}
    unc = props.get("uncertainty") or {}
    hydro = detection_metadata.get("hydrological_controls") or {}
    calib = detection_metadata.get("calibration_parameters") or {}

    scl_counts = stats.get("t1_scl_counts") or {}
    scl7_px = int(scl_counts.get("7", 0))
    px_10m = int(stats.get("pixel_count_10m") or 0)
    swir_px = stats.get("effective_swir_pixels_20m")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cadastre_text = (
        cadastre_note
        or "Sin cruce realizado contra catastro oficial provincial o registro de concesiones mineras/áridos."
    )

    lines: List[str] = []
    lines.append(f"# Dossier de Investigación Territorial — {fid}")
    lines.append("")
    lines.append("> **Regla epistémica obligatoria:** Este documento es una herramienta de orientación investigativa y fiscalización ambiental. Ninguna medición satelital por sí sola demuestra extracción de áridos ni infracción legal.")
    lines.append("")

    # 1. Ficha de identificación
    lines.append("## 1. Ficha de identificación")
    lines.append("")
    lines.append("| Campo | Valor |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **ID de candidato** | `{fid}` |")
    lines.append(f"| **Sector / AOI** | {aoi_name} |")
    lines.append(f"| **Período analizado** | {period_label} |")
    lines.append(f"| **Tipología morfológica** | `{atype}` |")
    lines.append(f"| **Unidad geomorfológica estimada (heurística satelital T0)** | `{zone}` *(no equivale a cartografía geomorfológica oficial)* |")
    lines.append(f"| **Centroide WGS84 (lon, lat)** | `{centroid_wgs[0]}, {centroid_wgs[1]}` |")
    lines.append(f"| **Centroide POSGAR 2007 (EPSG:{crs_epsg})** | `{centroid_pos[0]}, {centroid_pos[1]}` |")
    lines.append(f"| **Superficie vectorial exacta** | **{area_m2:,.2f} m²** ({area_ha:.4f} ha) |")
    lines.append(f"| **Distancia al cuerpo de agua T0** | {_fmt_num(dist_water_m, 1, ' m')} |")
    lines.append("| **Estado de verificación** | `PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO` |")
    lines.append(f"| **Fecha de emisión** | {generated_at} |")
    lines.append("")

    # 2. Resumen ejecutivo
    lines.append("## 2. Resumen ejecutivo")
    lines.append("")
    lines.append(
        f"- **Observación:** En el polígono `{fid}` ({area_m2:,.2f} m², `{zone}`), las escenas Sentinel-2 "
        f"registran un cambio espectral compatible con `{atype}` "
        f"(diferencia media de píxeles: `ΔBSI_adj={_fmt_num(db_mean, 4)}`, "
        f"`ΔNDVI_adj={_fmt_num(dn_mean, 4)}`, `ΔMNDWI={_fmt_num(dm_mean, 4)}`)."
    )
    lines.append(
        "- **Interpretación:** El patrón espectral sugiere pérdida de cobertura vegetal y exposición de "
        "superficie mineral o inundación localizada; no obstante, coexisten explicaciones alternativas "
        "(obra rural/portuaria, variación hidrométrica o artefactos de borde SWIR 20 m / SCL=7)."
    )
    lines.append(
        "- **Hipótesis:** Compatible con posible movimiento de suelos, acopio o frente de extracción ribereña, "
        "así como con usos antrópicos autorizados o dinámica de borde aluvial."
    )
    lines.append(
        "- **Verificación:** Requiere inspección visual de alta resolución / drone, constatación en terreno y "
        "consulta de permisos ante la autoridad provincial competente."
    )
    lines.append("")

    # 3. Evidencia territorial y mapas
    lines.append("## 3. Evidencia territorial y mapas")
    lines.append("")
    lines.append("### Observación morfológica y espacial")
    lines.append(f"- **Área geométrica (`{stats.get('area_method', 'shapely_polygon_posgar_2007')}`):** {area_m2:,.2f} m² ({area_ha:.4f} ha).")
    lines.append(f"- **Muestreo en grilla:** {px_10m} píxeles a 10 m (~{swir_px if swir_px is not None else 'N/D'} píxeles nativos SWIR de 20 m).")
    lines.append(f"- **Morfología:** Rectangularidad `{rect:.3f}`, elongación `{elong:.3f}`.")
    lines.append(f"- **Contexto geomorfológico estimado (heurística satelital T0):** `{zone}`, distancia al agua T0 `{_fmt_num(dist_water_m, 1, ' m')}`.")
    if map_artifacts:
        lines.append("- **Productos cartográficos asociados:**")
        for m_path in map_artifacts:
            lines.append(f"  - `{m_path}`")
    else:
        lines.append("- **Productos cartográficos asociados:** Geometría exportada en GeoJSON (`EPSG:4326`) derivada de procesamiento métrico `EPSG:{}`.".format(crs_epsg))
    lines.append("")

    # 4. Evolución multitemporal
    lines.append("## 4. Evolución multitemporal")
    lines.append("")
    lines.append(
        "> **Nota metodológica:** Se distinguen explícitamente las **medianas espectrales por fecha** "
        "calculadas sobre el polígono de las **medias de diferencias por píxel (`delta_*_mean`)** "
        "ajustadas fenológicamente entre T0 y T1."
    )
    lines.append("")
    stage_t0 = hydro.get("stage_t0_m")
    stage_t1 = hydro.get("stage_t1_m")
    stage_t0_src = hydro.get("stage_t0_source", "NO_DISPONIBLE")
    stage_t1_src = hydro.get("stage_t1_source", "NO_DISPONIBLE")
    stage_delta = hydro.get("stage_delta_m")
    stage_station = hydro.get("stage_station", "Estación INA/PNA configurada")
    stage_queried_at = hydro.get("stage_queried_at_utc")

    if multitemporal_series and len(multitemporal_series) >= 2:
        t0_row = multitemporal_series[-2]
        t1_row = multitemporal_series[-1]
        if stage_t0_src == "NO_DISPONIBLE" and t0_row.get("_stage_src"):
            stage_t0 = t0_row.get("_stage_val", stage_t0)
            stage_t0_src = str(t0_row["_stage_src"])
        if stage_t1_src == "NO_DISPONIBLE" and t1_row.get("_stage_src"):
            stage_t1 = t1_row.get("_stage_val", stage_t1)
            stage_t1_src = str(t1_row["_stage_src"])
        if stage_t0 is not None and stage_t1 is not None:
            stage_delta = round(float(stage_t1) - float(stage_t0), 3)

    if multitemporal_series:
        lines.append("| Fecha | Escena / Sensor | Altura río (m) | Procedencia hidrométrica | B11 med. (DN) | NDVI med. | BSI med. | MNDWI med. | Clases SCL |")
        lines.append("| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :--- |")
        for row in multitemporal_series:
            row_stage = row.get("_stage_val")
            row_stage_txt = f"{row_stage:.2f} m" if isinstance(row_stage, (int, float)) else str(row.get("stage_m", "N/D"))
            row_src = str(row.get("_stage_src") or row.get("stage_source") or "NO_DISPONIBLE")
            lines.append(
                f"| {row.get('date', 'N/D')} | {row.get('scene_id', 'N/D')} | "
                f"{row_stage_txt} | `{row_src}` | {row.get('b11_median', 'N/D')} | "
                f"{row.get('ndvi_median', 'N/D')} | {row.get('bsi_median', 'N/D')} | "
                f"{row.get('mndwi_median', 'N/D')} | {row.get('scl_summary', 'N/D')} |"
            )
    else:
        lines.append("| Instante | B11 mediana (DN) | NDVI mediana | BSI mediana | MNDWI mediana | Conteo SCL |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
        lines.append(
            f"| **Línea base T0** | {_fmt_num(stats.get('t0_b11_median'), 1)} | "
            f"{_fmt_num(stats.get('t0_ndvi_median'), 3)} | {_fmt_num(stats.get('t0_bsi_median'), 3)} | "
            f"N/D | Línea base mediana ({detection_metadata.get('baseline_scenes_count', 1)} escena/s) |"
        )
        lines.append(
            f"| **Evaluación T1** | {_fmt_num(stats.get('t1_b11_median'), 1)} | "
            f"{_fmt_num(stats.get('t1_ndvi_median'), 3)} | {_fmt_num(stats.get('t1_bsi_median'), 3)} | "
            f"{_fmt_num(stats.get('t1_mndwi_median'), 3)} | {_format_scl_counts(scl_counts)} |"
        )
    lines.append("")
    lines.append(
        "**Criterio de procedencia hidrométrica:** `INA_API_A5` (observación oficial consultada en tiempo de ejecución), "
        "`FALLBACK_EXP001` (valor histórico heredado de Exp 001 usado solo si falla la API; no es observación nueva), "
        "`FALLBACK_MANUAL` (respaldo manual) y `NO_DISPONIBLE` (sin dato)."
    )
    lines.append("")
    lines.append(
        f"**Resumen de diferencias por píxel (T0 -> T1):** "
        f"`delta_bsi_mean={_fmt_num(db_mean, 4)}`, "
        f"`delta_ndvi_mean={_fmt_num(dn_mean, 4)}`, "
        f"`delta_mndwi_mean={_fmt_num(dm_mean, 4)}`."
    )
    lines.append("")

    # 5. Análisis de hipótesis alternativas
    lines.append("## 5. Análisis de hipótesis alternativas")
    lines.append("")
    lines.append("| Hipótesis | Nivel | Evidencia observada compatible | Evidencia en contra / incertidumbre | Estado |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    lines.append(
        f"| **H1. Extracción / acopio de áridos o desmonte operativo** | Hipótesis | "
        f"Cambio localizado `{atype}` de {area_m2:,.0f} m² en `{zone}`, `ΔBSI_adj={_fmt_num(db_mean, 3)}`. | "
        f"Resolución SWIR 20 m insuficiente para distinguir acopio mineral de obra civil sin VHR/campo. | `A VERIFICAR EN TERRENO` |"
    )
    lines.append(
        "| **H2. Obra civil, camino rural o infraestructura ribereña** | Hipótesis | "
        f"Incremento de suelo expuesto (`B11_T1={_fmt_num(stats.get('t1_b11_median'), 0)} DN`) y geometría con elongación `{elong:.2f}`. | "
        "No se dispone de catastro parcelario ni permisos de obra cargados en el sistema. | `NO DESCARTABLE` |"
    )
    hydro_txt = (
        f"Δagua AOI={hydro.get('water_stage_delta_pct', 'N/D')}%"
        + (
            f", Δaltura={stage_delta:+.2f} m ({stage_t0} m [`{stage_t0_src}`] -> {stage_t1} m [`{stage_t1_src}`])"
            if stage_delta is not None
            else ""
        )
    )
    lines.append(
        f"| **H3. Dinámica fluvial / humedad edáfica / crecida** | Interpretación | "
        f"Contexto hidrométrico: {hydro_txt}. | "
        f"El polígono superó filtros de cinturón de crecida, migración dipolar y agua oscura (`B11_T1={_fmt_num(stats.get('t1_b11_median'), 0)}`). | `RESIDUAL TRAS FILTROS` |"
    )
    lines.append(
        f"| **H4. Artefacto atmosférico / sombra / borde SCL=7** | Interpretación | "
        f"Píxeles `SCL=7` en T1: {scl7_px}; adyacencia nube/sombra: `{unc.get('cloud_shadow_adjacent', False)}`. | "
        f"Fracción de píxeles válidos `{unc.get('valid_pixel_fraction', 'N/D')}`. | "
        f"{'ATENCIÓN: PRESENCIA SCL=7' if scl7_px > 0 or unc.get('cloud_shadow_adjacent') else 'BAJA PROBABILIDAD'} |"
    )
    lines.append("")

    # 6. Confiabilidad por dimensión
    lines.append("## 6. Confiabilidad por dimensión")
    lines.append("")
    lines.append(
        f"> **Score numérico interno:** `{conf_raw:.3f}` (escala 0–1). Este índice resume fuerza de señal, "
        "compacidad geométrica y penalizaciones por confundidores; **no** se traduce automáticamente a una "
        "categoría cualitativa de certeza ni sustituye la evaluación pericial."
    )
    lines.append("")
    lines.append("| Dimensión | Medición / Estado |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **Calidad espectral y máscara SCL** | Fracción válida: `{unc.get('valid_pixel_fraction', 'N/D')}`, adyacencia nube/sombra: `{unc.get('cloud_shadow_adjacent', False)}`, SCL=7: `{scl7_px}` px |")
    lines.append(f"| **Resolución espacial efectiva** | {unc.get('effective_resolution_m', 20.0)} m SWIR (~{swir_px if swir_px is not None else 'N/D'} píxeles SWIR en el polígono) |")
    lines.append(f"| **Estabilidad hidrométrica del tramo** | Variación lámina AOI: `{unc.get('water_stage_delta_pct', 'N/D')}%`; score de confundidores naturales: `{unc.get('natural_dynamics_confounder_score', 'N/D')}` |")
    lines.append(f"| **Persistencia temporal de línea base** | `{unc.get('persistence_scenes_count', 1)}` escena(s) en T0 |")
    lines.append("")

    # 7. Limitaciones y datos faltantes
    lines.append("## 7. Limitaciones y datos faltantes")
    lines.append("")
    lims = list(unc.get("limitations") or [])
    lims.append(f"Cruce administrativo/catastral: {cadastre_text}")
    if px_10m > 0 and px_10m < 40:
        lims.append(
            f"Superficie cercana al límite operativo SWIR 20 m ({area_m2:,.1f} m² ≈ {swir_px} px SWIR): "
            "susceptible a efecto de píxel mixto en bordes."
        )
    for item in lims:
        lines.append(f"- {item}")
    lines.append("")

    # 8. Recomendación de actuación
    lines.append("## 8. Recomendación de actuación (Nivel: Verificación)")
    lines.append("")
    lines.append("1. **Inspector ambiental:** Verificar en terreno las coordenadas WGS84 indicadas para constatar presencia de dragado, refulado, acopio o movimiento de suelos, o descartar uso rural/obra ajena a áridos.")
    lines.append("2. **Autoridad provincial / Catastro y Minería:** Cruzar el polígono contra padrón catastral, concesiones mineras de 3ª categoría y permisos ambientales vigentes.")
    lines.append("3. **Prefectura / Control ribereño:** Utilizar el centroide como orientación geográfica preliminar en patrullajes de jurisdicción fluvial, sin asumir infracción previa.")
    lines.append("4. **Seguimiento satelital:** Incorporar imagen VHR o vuelo UAV si se requiere precisar volúmenes o límites parcelarios por debajo de 20 m.")
    lines.append("")

    # 9. Fuentes, parámetros y trazabilidad técnica
    lines.append("## 9. Fuentes, parámetros y trazabilidad técnica")
    lines.append("")
    lines.append(f"- **CRS métrico de cálculo:** `EPSG:{crs_epsg}` (POSGAR 2007). Exportación GeoJSON: `EPSG:4326`.")
    queried_txt = f", consulta={stage_queried_at}" if stage_queried_at else ""
    lines.append(
        f"- **Procedencia hidrométrica ({stage_station}, unidad `m`{queried_txt}):** "
        f"T0=`{stage_t0}` m (`{stage_t0_src}`), T1=`{stage_t1}` m (`{stage_t1_src}`)."
    )
    lines.append(f"- **Resolución de grilla / SWIR nativo:** {detection_metadata.get('grid_resolution_m', 10.0)} m / {detection_metadata.get('effective_swir_resolution_m', 20.0)} m.")
    lines.append(f"- **Área mínima de detección:** {detection_metadata.get('min_anomaly_area_m2', 1600.0)} m².")
    if calib:
        calib_str = ", ".join(f"`{k}={v}`" for k, v in calib.items())
        lines.append(f"- **Parámetros congelados Fase 2.1:** {calib_str}.")
    lines.append(f"- **Nota institucional del motor:** {detection_metadata.get('institutional_note', '')}")
    lines.append("")

    return "\n".join(lines)


def generate_negative_or_area_dossier(
    detection_metadata: Dict[str, Any],
    aoi_name: str = "Tramo fluvial monitoreado",
    period_label: str = "T0 -> T1",
    bbox_wgs84: Optional[Sequence[float]] = None,
) -> str:
    """Genera un dossier auditable cuando no hay detecciones residuales (`candidate_count == 0`)."""
    hydro = detection_metadata.get("hydrological_controls") or {}
    zones = detection_metadata.get("geomorphological_zone_fractions") or {}
    calib = detection_metadata.get("calibration_parameters") or {}
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# Dossier de Vigilancia Territorial — Resultado Negativo ({aoi_name})",
        "",
        "## 1. Ficha de identificación",
        "",
        "| Campo | Valor |",
        "| :--- | :--- |",
        f"| **Sector / AOI** | {aoi_name} |",
        f"| **Período analizado** | {period_label} |",
        f"| **Bounding Box WGS84** | `{list(bbox_wgs84) if bbox_wgs84 else 'N/D'}` |",
        f"| **Candidatos residuales detectados** | **{detection_metadata.get('candidate_count', 0)}** |",
        f"| **Fecha de emisión** | {generated_at} |",
        "",
        "## 2. Resumen ejecutivo",
        "",
        "- **Observación:** No se detectaron polígonos residuales que superen simultáneamente los umbrales espectrales, morfológicos (`>= 1600 m²`), de agua oscura y de dinámica fluvial.",
        "- **Interpretación:** En las escenas comparadas no hay evidencia satelital de frentes de extracción o desmontes ribereños compatibles con la firma del detector.",
        "- **Hipótesis:** Tramo sin anomalías detectables a resolución Sentinel-2 (10–20 m) durante el período analizado, o cambios por debajo del umbral de área/contraste.",
        "- **Verificación:** No se requiere inspección de campo de urgencia derivada de esta corrida; mantener monitoreo periódico.",
        "",
        "## 3. Evidencia territorial y mapas",
        "",
        f"- **Fracción de píxeles válidos combinados:** {detection_metadata.get('valid_pixel_fraction', 'N/D')}",
        f"- **Distribución geomorfológica T0:** {zones if zones else 'N/D'}",
        "",
        "## 4. Evolución multitemporal",
        "",
        f"- **Escenas en línea base T0:** {detection_metadata.get('baseline_scenes_count', 'N/D')}",
        f"- **Variación hidrométrica AOI:** {hydro.get('water_stage_delta_pct', 'N/D')}% "
        f"(crecida sistémica: `{hydro.get('is_systemic_stage_rise')}`, bajante sistémica: `{hydro.get('is_systemic_stage_drop')}`).",
        "",
        "## 5. Análisis de hipótesis alternativas",
        "",
        "- Las variaciones estacionales de vegetación y humedad fueron absorbidas por la normalización fenológica "
        f"(`NDVI_shift={hydro.get('riparian_ndvi_seasonal_shift')}`, `BSI_shift={hydro.get('riparian_bsi_seasonal_shift')}`) "
        f"y por el filtrado de dipolos de migración de bancos (`{hydro.get('bar_migration_dipoles_filtered', 0)}` pares).",
        "",
        "## 6. Confiabilidad por dimensión",
        "",
        "- Resultado negativo respaldado por fracción válida `{}` y controles hidrológicos activos.".format(
            detection_metadata.get("valid_pixel_fraction", "N/D")
        ),
        "",
        "## 7. Limitaciones y datos faltantes",
        "",
        "- Eventos menores a 1.600 m² o cubiertos por nubes/sombras SCL quedan fuera del alcance de detección.",
        "",
        "## 8. Recomendación de actuación (Nivel: Verificación)",
        "",
        "- Archivar como línea base de control sin anomalías y programar próxima verificación en estiaje comparable.",
        "",
        "## 9. Fuentes, parámetros y trazabilidad técnica",
        "",
        f"- **CRS métrico:** `EPSG:{detection_metadata.get('crs_metric_epsg', 5347)}`.",
        f"- **Parámetros congelados:** {calib if calib else 'N/D'}.",
        "",
    ]
    return "\n".join(lines)


def write_dossier_markdown(content: str, out_path: Path) -> Path:
    """Persiste un dossier en disco creando directorios padres si es necesario."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path
