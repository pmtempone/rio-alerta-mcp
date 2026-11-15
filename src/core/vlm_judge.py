"""Dictamen pericial multimodal y heurístico auxiliar de geomorfología fluvial (Fase 3)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from src.schemas import (
    ExtractionAnomalyFeature,
    PermitValidationResult,
    VLMFluvialVerdict,
    VLMVerdictLiteral,
    coerce_anomaly_feature,
)


def build_vlm_fluvial_prompt(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
    t0_date: str,
    t1_date: str,
    permit_result: Optional[PermitValidationResult] = None,
) -> str:
    """Construye un prompt pericial estructurado para evaluación visual comparada T0 vs T1.

    Incluye reglas estrictas de prudencia epistémica para diferenciar actividad antrópica
    potencial (taludes rectilíneos, caminos de acarreo, dársenas) de dinámica fluvial natural
    (estiaje, migración de barras de arena, pulso hidrométrico o turbidez).
    """
    feat = coerce_anomaly_feature(anomaly)
    confounder_score = (
        feat.uncertainty.natural_dynamics_confounder_score if feat.uncertainty is not None else 0.0
    )
    water_delta_pct = feat.uncertainty.water_stage_delta_pct if feat.uncertainty is not None else 0.0
    valid_frac = feat.uncertainty.valid_pixel_fraction if feat.uncertainty is not None else 1.0
    cloud_adj = feat.uncertainty.cloud_shadow_adjacent if feat.uncertainty is not None else False

    cadastre_line = "No evaluado aún."
    if permit_result is not None:
        cadastre_line = (
            f"{permit_result.classification} (prioridad={permit_result.priority}, "
            f"exceso={permit_result.excess_area_m2:.1f} m²)"
        )

    return (
        f"Evaluar visualmente en pares RGB/Falso Color T0 ({t0_date}) vs T1 ({t1_date}) el candidato "
        f"'{feat.id}' ({feat.anomaly_type}) ubicado en WGS84 {feat.centroid_wgs84} "
        f"(POSGAR EPSG:{feat.crs_epsg}, área={feat.area_m2:.1f} m², "
        f"rectangularidad={feat.rectangularity:.2f}, elongación={feat.elongation:.2f}).\n"
        f"Indicadores espectrales: ΔBSI={feat.delta_bsi_mean:+.3f}, ΔMNDWI={feat.delta_mndwi_mean:+.3f}, "
        f"ΔNDVI={feat.delta_ndvi_mean:+.3f}, puntaje de señal={feat.confidence:.2f}, "
        f"zona geomorfológica='{feat.geomorphological_zone}', distancia al agua T0="
        f"{feat.distance_to_water_t0_m if feat.distance_to_water_t0_m is not None else 'N/D'} m.\n"
        f"Controles e incertidumbre: fracción válida SCL={valid_frac:.2f}, nube/sombra<=40m={cloud_adj}, "
        f"variación lámina de agua={water_delta_pct:+.1f}%, score confusor natural={confounder_score:.2f}.\n"
        f"Contexto catastral: {cadastre_line}.\n"
        "Criterios periciales obligatorios:\n"
        "1. Distinguir frentes rectilíneos de excavación, acopios claros con caminos de acceso o lagunas "
        "de decantación respecto de migración natural de bancos de arena, estiaje o cambios fenológicos.\n"
        "2. Considerar las limitaciones físicas Sentinel-2 (10 m VNIR / 20 m SWIR) y abstenerse de imputar "
        "ilegalidad automática.\n"
        "3. Emitir únicamente uno de los cuatro veredictos auxiliares: EXTRACCION_ANTROPICA, "
        "DINAMICA_FLUVIAL_NATURAL, ARTEFACTO_NUBES_SOMBRAS o PENDIENTE_REVISION."
    )


def evaluate_candidate_vlm_verdict(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
    verdict: Optional[VLMVerdictLiteral] = None,
    certainty_pct: Optional[Union[int, float]] = None,
    extraction_evidence: Optional[str] = None,
    fluvial_dynamics_assessment: Optional[str] = None,
    backend: str = "heuristic_spectral_morphology_v1",
    permit_result: Optional[PermitValidationResult] = None,
) -> VLMFluvialVerdict:
    """Emite o registra un dictamen pericial auxiliar sobre el origen morfo-espectral del candidato.

    Si se provee `verdict` explícito (por ejemplo, desde un modelo multimodal o un perito humano),
    lo valida y registra manteniendo `human_review_required=True`. Si se omite, aplica la evaluación
    heurística determinista sobre las métricas geométricas y espectrales de `anomaly`.
    """
    feat = coerce_anomaly_feature(anomaly)
    confounder_score = (
        feat.uncertainty.natural_dynamics_confounder_score if feat.uncertainty is not None else 0.30
    )
    valid_frac = feat.uncertainty.valid_pixel_fraction if feat.uncertainty is not None else 1.0
    cloud_adj = feat.uncertainty.cloud_shadow_adjacent if feat.uncertainty is not None else False
    eff_res = feat.uncertainty.effective_resolution_m if feat.uncertainty is not None else feat.effective_resolution_m

    if verdict is None:
        if valid_frac < 0.70 or (cloud_adj and feat.confidence < 0.60):
            resolved_verdict: VLMVerdictLiteral = "ARTEFACTO_NUBES_SOMBRAS"
            resolved_recommended = False
            computed_certainty = int(round(max(55.0, min(90.0, (1.0 - valid_frac) * 100.0 + 40.0))))
        elif confounder_score >= 0.65 or (
            feat.geomorphological_zone == "BARRAS_Y_BANCOS_MOVILES" and feat.confidence < 0.65
        ):
            resolved_verdict = "DINAMICA_FLUVIAL_NATURAL"
            resolved_recommended = False
            computed_certainty = int(round(max(55.0, min(90.0, confounder_score * 100.0))))
        elif (
            feat.confidence >= 0.72
            and confounder_score <= 0.40
            and (
                abs(feat.delta_bsi_mean) >= 0.12
                or abs(feat.delta_mndwi_mean) >= 0.18
                or feat.anomaly_type in ("frente_excavacion_inundado", "desmonte_ribera_acopio", "complejo_extractivo")
            )
        ):
            resolved_verdict = "EXTRACCION_ANTROPICA"
            resolved_recommended = True
            computed_certainty = int(round(max(60.0, min(92.0, feat.confidence * 100.0 * (1.0 - 0.25 * confounder_score)))))
        else:
            resolved_verdict = "PENDIENTE_REVISION"
            resolved_recommended = True
            computed_certainty = int(round(max(35.0, min(65.0, feat.confidence * 70.0))))
    else:
        resolved_verdict = verdict
        resolved_recommended = resolved_verdict in ("EXTRACCION_ANTROPICA", "PENDIENTE_REVISION")
        computed_certainty = 50

    final_certainty = (
        int(max(0, min(100, round(float(certainty_pct)))))
        if certainty_pct is not None
        else computed_certainty
    )

    if extraction_evidence is None or not extraction_evidence.strip():
        cadastre_note = (
            f" Cruce catastral asociado: {permit_result.classification}." if permit_result is not None else ""
        )
        extraction_evidence = (
            f"Candidato '{feat.id}' ({feat.anomaly_type}) en zona '{feat.geomorphological_zone}' con "
            f"superficie de {feat.area_m2:.1f} m² (rectangularidad={feat.rectangularity:.2f}, "
            f"elongación={feat.elongation:.2f}), deltas ΔBSI={feat.delta_bsi_mean:+.3f}, "
            f"ΔMNDWI={feat.delta_mndwi_mean:+.3f}, ΔNDVI={feat.delta_ndvi_mean:+.3f} y puntaje de señal "
            f"{feat.confidence:.2f} a resolución efectiva {eff_res:.0f} m.{cadastre_note}"
        )

    if fluvial_dynamics_assessment is None or not fluvial_dynamics_assessment.strip():
        fluvial_dynamics_assessment = (
            f"Evaluación frente a dinámica fluvial natural: confounder_score={confounder_score:.2f}, "
            f"fracción SCL válida={valid_frac:.2f}, proximidad nube/sombra={cloud_adj}. "
            "Dictamen auxiliar de gabinete que requiere verificación visual de chips de alta resolución "
            "y/o inspección presencial por autoridad competente."
        )

    return VLMFluvialVerdict(
        verdict=resolved_verdict,
        certainty_pct=final_certainty,
        extraction_evidence=extraction_evidence.strip(),
        fluvial_dynamics_assessment=fluvial_dynamics_assessment.strip(),
        field_inspection_recommended=resolved_recommended,
        backend=backend,
        human_review_required=True,
    )
