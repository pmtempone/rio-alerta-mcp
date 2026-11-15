"""Evaluación auxiliar fluvial separada por modalidad (Heurística, VLM Externo y Experto Humano)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from src.schemas import (
    EvaluationModeLiteral,
    ExtractionAnomalyFeature,
    PermitValidationResult,
    VLMFluvialVerdict,
    VLMVerdictLiteral,
    coerce_anomaly_feature,
)

DEFAULT_PROMPT_VERSION = "rio-alerta-fluvial-v1.1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_and_hash_images(image_refs: Optional[Sequence[str]]) -> tuple[List[str], Dict[str, str]]:
    """Normaliza referencias de imagen y calcula SHA-256 para archivos locales."""
    refs: List[str] = []
    hashes: Dict[str, str] = {}
    if not image_refs:
        return refs, hashes

    for raw_ref in image_refs:
        ref = str(raw_ref).strip()
        if not ref:
            continue
        refs.append(ref)
        lowered = ref.lower()
        if lowered.startswith(("http://", "https://", "stac://", "s3://", "gs://")):
            continue
        path = Path(ref)
        if not path.is_file():
            raise FileNotFoundError(f"La imagen de evaluación referenciada no existe en disco: '{ref}'.")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[ref] = digest

    return refs, hashes


def build_vlm_fluvial_prompt(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
    t0_date: str,
    t1_date: str,
    permit_result: Optional[PermitValidationResult] = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str:
    """Construye un prompt versionado para evaluación visual comparada T0 vs T1.

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
        f"[Prompt {prompt_version}] Evaluar visualmente en pares RGB/Falso Color T0 ({t0_date}) vs T1 ({t1_date}) "
        f"el candidato '{feat.id}' ({feat.anomaly_type}) ubicado en WGS84 {feat.centroid_wgs84} "
        f"(POSGAR EPSG:{feat.crs_epsg}, área={feat.area_m2:.1f} m², "
        f"rectangularidad={feat.rectangularity:.2f}, elongación={feat.elongation:.2f}).\n"
        f"Indicadores espectrales: ΔBSI={feat.delta_bsi_mean:+.3f}, ΔMNDWI={feat.delta_mndwi_mean:+.3f}, "
        f"ΔNDVI={feat.delta_ndvi_mean:+.3f}, señal morfo-espectral={feat.confidence:.2f}, "
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
        "3. Emitir únicamente uno de los cuatro códigos auxiliares: EXTRACCION_ANTROPICA, "
        "DINAMICA_FLUVIAL_NATURAL, ARTEFACTO_NUBES_SOMBRAS o PENDIENTE_REVISION."
    )


def evaluate_candidate_vlm_verdict(
    anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
    verdict: Optional[VLMVerdictLiteral] = None,
    certainty_pct: Optional[Union[int, float]] = None,
    extraction_evidence: Optional[str] = None,
    fluvial_dynamics_assessment: Optional[str] = None,
    backend: Optional[str] = None,
    permit_result: Optional[PermitValidationResult] = None,
    evaluation_mode: Optional[EvaluationModeLiteral] = None,
    model_id: Optional[str] = None,
    model_version: Optional[str] = None,
    prompt_version: Optional[str] = DEFAULT_PROMPT_VERSION,
    image_refs: Optional[Sequence[str]] = None,
    evaluator_id: Optional[str] = None,
    evaluated_at_utc: Optional[str] = None,
) -> VLMFluvialVerdict:
    """Evalúa o registra un resultado separando estrictamente cribado heurístico, VLM externo y experto.

    Reglas de integridad (Fase 3.1):
    - `HEURISTIC_SCREENING`: determinista sobre métricas tabulares; aclara explícitamente que no
      es un VLM ni un dictamen pericial y que `certainty_pct` no es probabilidad estadística.
    - `EXTERNAL_VLM`: requiere `verdict`, `model_id` y al menos una referencia en `image_refs`
      (calculando SHA-256 para imágenes locales). Tampoco equivale a constatación pericial.
    - `HUMAN_EXPERT`: requiere `verdict` explícito y `evaluator_id`.
    """
    feat = coerce_anomaly_feature(anomaly)
    confounder_score = (
        feat.uncertainty.natural_dynamics_confounder_score if feat.uncertainty is not None else 0.30
    )
    valid_frac = feat.uncertainty.valid_pixel_fraction if feat.uncertainty is not None else 1.0
    cloud_adj = feat.uncertainty.cloud_shadow_adjacent if feat.uncertainty is not None else False
    eff_res = feat.uncertainty.effective_resolution_m if feat.uncertainty is not None else feat.effective_resolution_m

    # Resolver modalidad explícita o compatibilidad legacy sin ocultar su naturaleza
    resolved_mode: EvaluationModeLiteral
    if evaluation_mode is not None:
        resolved_mode = evaluation_mode
    elif verdict is None:
        resolved_mode = "HEURISTIC_SCREENING"
    elif evaluator_id and evaluator_id.strip():
        resolved_mode = "HUMAN_EXPERT"
    elif model_id and model_id.strip():
        resolved_mode = "EXTERNAL_VLM"
    elif backend and any(tok in backend.lower() for tok in ("human", "expert", "perito")):
        resolved_mode = "HUMAN_EXPERT"
    elif backend and any(tok in backend.lower() for tok in ("vlm", "gemini", "gpt", "claude", "openai")):
        resolved_mode = "EXTERNAL_VLM"
    else:
        resolved_mode = "HEURISTIC_SCREENING"

    if evaluation_mode == "EXTERNAL_VLM" or (resolved_mode == "EXTERNAL_VLM" and model_id is not None):
        if verdict is None:
            raise ValueError("evaluation_mode='EXTERNAL_VLM' requiere `verdict` explícito.")
        if not model_id or not model_id.strip():
            raise ValueError("evaluation_mode='EXTERNAL_VLM' requiere `model_id` no vacío.")
        if not image_refs or not any(str(r).strip() for r in image_refs):
            raise ValueError("evaluation_mode='EXTERNAL_VLM' requiere al menos una referencia en `image_refs`.")
    elif evaluation_mode == "HUMAN_EXPERT":
        if verdict is None:
            raise ValueError("evaluation_mode='HUMAN_EXPERT' requiere `verdict` explícito.")
        if not evaluator_id or not evaluator_id.strip():
            raise ValueError("evaluation_mode='HUMAN_EXPERT' requiere `evaluator_id` no vacío.")
    elif resolved_mode == "HEURISTIC_SCREENING" and verdict is not None and evaluation_mode == "HEURISTIC_SCREENING":
        # En modo estrictamente heurístico, si no se proveyó backend, fijarlo al heurístico
        pass

    resolved_refs, image_hashes = _resolve_and_hash_images(image_refs)

    if resolved_mode == "HEURISTIC_SCREENING" and verdict is None:
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
            computed_certainty = int(
                round(max(60.0, min(92.0, feat.confidence * 100.0 * (1.0 - 0.25 * confounder_score))))
            )
        else:
            resolved_verdict = "PENDIENTE_REVISION"
            resolved_recommended = True
            computed_certainty = int(round(max(35.0, min(65.0, feat.confidence * 70.0))))
    else:
        assert verdict is not None
        resolved_verdict = verdict
        resolved_recommended = resolved_verdict in ("EXTRACCION_ANTROPICA", "PENDIENTE_REVISION")
        computed_certainty = 50

    final_certainty = (
        int(max(0, min(100, round(float(certainty_pct)))))
        if certainty_pct is not None
        else computed_certainty
    )

    resolved_backend = backend or (
        "heuristic_spectral_morphology_v1"
        if resolved_mode == "HEURISTIC_SCREENING"
        else (model_id.strip() if resolved_mode == "EXTERNAL_VLM" and model_id else "human_expert_review")
    )
    eval_timestamp = evaluated_at_utc or _utc_now_iso()

    if extraction_evidence is None or not extraction_evidence.strip():
        cadastre_note = (
            f" Cruce catastral asociado: {permit_result.classification}." if permit_result is not None else ""
        )
        if resolved_mode == "HEURISTIC_SCREENING":
            prefix = (
                "Cribado heurístico determinista basado únicamente en métricas morfo-espectrales tabulares "
                "(NO utiliza inspección visual ni modelo VLM; NO constituye dictamen pericial): "
            )
        elif resolved_mode == "EXTERNAL_VLM":
            prefix = (
                f"Evaluación auxiliar por modelo multimodal externo ({model_id or resolved_backend}, "
                f"imágenes={len(resolved_refs)}; NO equivale a constatación pericial humana): "
            )
        else:
            prefix = f"Evaluación registrada por especialista humano (evaluator_id={evaluator_id or 'no_informado'}): "

        extraction_evidence = (
            f"{prefix}candidato '{feat.id}' ({feat.anomaly_type}) en zona '{feat.geomorphological_zone}' "
            f"de {feat.area_m2:.1f} m² (rectangularidad={feat.rectangularity:.2f}, "
            f"elongación={feat.elongation:.2f}), deltas ΔBSI={feat.delta_bsi_mean:+.3f}, "
            f"ΔMNDWI={feat.delta_mndwi_mean:+.3f}, ΔNDVI={feat.delta_ndvi_mean:+.3f} y puntaje de señal "
            f"{feat.confidence:.2f} a resolución efectiva {eff_res:.0f} m.{cadastre_note}"
        )

    if fluvial_dynamics_assessment is None or not fluvial_dynamics_assessment.strip():
        uncertainty_note = (
            "El índice certainty_pct refleja coherencia heurística interna y NO es una probabilidad estadística calibrada."
            if resolved_mode == "HEURISTIC_SCREENING"
            else "Evaluación auxiliar sujeta a verificación documental y constatación presencial en terreno."
        )
        fluvial_dynamics_assessment = (
            f"Evaluación frente a dinámica fluvial natural: confounder_score={confounder_score:.2f}, "
            f"fracción SCL válida={valid_frac:.2f}, proximidad nube/sombra={cloud_adj}. {uncertainty_note}"
        )

    return VLMFluvialVerdict(
        verdict=resolved_verdict,
        certainty_pct=final_certainty,
        extraction_evidence=extraction_evidence.strip(),
        fluvial_dynamics_assessment=fluvial_dynamics_assessment.strip(),
        field_inspection_recommended=resolved_recommended,
        backend=resolved_backend,
        evaluation_mode=resolved_mode,
        model_id=model_id.strip() if model_id and model_id.strip() else None,
        model_version=model_version.strip() if model_version and model_version.strip() else None,
        prompt_version=prompt_version,
        image_refs=resolved_refs,
        image_hashes=image_hashes,
        evaluated_at_utc=eval_timestamp,
        evaluator_id=evaluator_id.strip() if evaluator_id and evaluator_id.strip() else None,
        is_statistical_probability=False,
        is_forensic_certification=(resolved_mode == "HUMAN_EXPERT" and bool(evaluator_id)),
        human_review_required=True,
    )
