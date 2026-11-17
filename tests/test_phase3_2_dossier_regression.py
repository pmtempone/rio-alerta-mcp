"""Tests de regresión R1–R4 (Fase 3.2 / protocolo §3.2): dominio de advertencias SCL,
provenance de métricas, regla de lectura B11/agua y lenguaje del score heurístico.

Ninguno de estos tests modifica umbrales ni parámetros de detección; pinnan la presentación
y la provenance del dossier para impedir que reaparezcan las contradicciones documentadas
en la meta-auditoría de RIO-0001 (M3-1/M3-2).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict

from src.core.dossier_generator import generate_candidate_dossier
from tests.test_phase3_dossier_t4 import _sample_feature, _sample_metadata


def _feature_with(
    overrides: Dict[str, Any] | None = None,
    stats_overrides: Dict[str, Any] | None = None,
    unc_overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Deriva una feature sintética del T4 base con overrides puntuales."""
    feat = copy.deepcopy(_sample_feature())
    props = feat["properties"]
    if overrides:
        props.update(overrides)
    if stats_overrides:
        props["spectral_stats"].update(stats_overrides)
    if unc_overrides:
        props["uncertainty"].update(unc_overrides)
    return feat


def _h4_row(md: str) -> str:
    """Extrae la fila H4 del dossier (Sección 5)."""
    match = re.search(r"\|\s*\*\*H4\..*\|", md)
    assert match is not None, "Fila H4 ausente en el dossier"
    return match.group(0)


def _h2_row(md: str) -> str:
    match = re.search(r"\|\s*\*\*H2\..*\|", md)
    assert match is not None, "Fila H2 ausente en el dossier"
    return match.group(0)


# ---------------------------------------------------------------------------
# R1 — Dominio de advertencias SCL (C1)
# ---------------------------------------------------------------------------


def test_r1_scl_warning_declares_domain_and_distinguishes_interior_from_adjacency() -> None:
    """El caso exacto de la contradicción de RIO-0001: SCL=7 solo en T0/buffer, interior T1 = 0."""
    feat = _feature_with(
        stats_overrides={"t1_scl_counts": {"5": 14, "6": 32}},
        unc_overrides={
            "cloud_shadow_adjacent": True,
            "limitations": [
                "Contorno adyacente (buffer ≤ 40 m) a píxeles de nube o sombra en máscara SCL combinada de T0 y T1."
            ],
        },
    )
    md = generate_candidate_dossier(feat, _sample_metadata())
    h4 = _h4_row(md)

    # El estado NO debe afirmar presencia interior de SCL=7 cuando el conteo T1 es 0
    assert "ATENCIÓN: ADYACENCIA NUBE/SOMBRA (SIN SCL=7 INTERIOR)" in h4
    assert "PRESENCIA SCL=7" not in h4
    # El dominio completo debe estar declarado: interior/T1/10 m para el conteo y buffer/T0∪T1 para la adyacencia
    assert "SCL=7 interior del polígono (T1, 10 m): 0 px" in h4
    assert "buffer ≤ 40 m sobre máscara combinada T0∪T1" in h4
    # La limitación heredada del detector también declara dominio
    assert "máscara SCL combinada de T0 y T1" in md


def test_r1_scl_interior_present_states_interior_only() -> None:
    feat = _feature_with(
        stats_overrides={"t1_scl_counts": {"5": 16, "7": 10}},
        unc_overrides={"cloud_shadow_adjacent": False},
    )
    md = generate_candidate_dossier(feat, _sample_metadata())
    h4 = _h4_row(md)
    assert "ATENCIÓN: SCL=7 INTERIOR (T1)" in h4
    assert "SCL=7 interior del polígono (T1, 10 m): 10 px" in h4

    feat_both = _feature_with(
        stats_overrides={"t1_scl_counts": {"5": 16, "7": 10}},
        unc_overrides={"cloud_shadow_adjacent": True},
    )
    md_both = generate_candidate_dossier(feat_both, _sample_metadata())
    assert "ATENCIÓN: SCL=7 INTERIOR Y ADYACENCIA NUBE/SOMBRA" in _h4_row(md_both)


def test_r1_no_scl_indicators_reports_no_indicators() -> None:
    feat = _feature_with(
        stats_overrides={"t1_scl_counts": {"5": 20, "4": 10}},
        unc_overrides={"cloud_shadow_adjacent": False},
    )
    md = generate_candidate_dossier(feat, _sample_metadata())
    assert "SIN INDICIOS" in _h4_row(md)


# ---------------------------------------------------------------------------
# R3 — Regla de lectura B11 / agua abierta (C2)
# ---------------------------------------------------------------------------


def test_r3_low_b11_with_open_water_forbids_mineral_exposure_claim() -> None:
    """El caso de la auditoría: B11_T1=240 DN con MNDWI=0.50 y SCL=6 dominante."""
    feat = _feature_with(
        overrides={
            "anomaly_type": "frente_excavacion_inundado",
            "delta_mndwi_mean": 0.60,
            "delta_bsi_mean": -0.10,
        },
        stats_overrides={
            "t1_b11_median": 240.0,
            "t1_mndwi_median": 0.4999,
            "t1_scl_counts": {"5": 14, "6": 32},
        },
    )
    md = generate_candidate_dossier(feat, _sample_metadata())
    h2 = _h2_row(md)

    assert "Incremento de suelo expuesto" not in h2
    assert "no con exposición mineral" in h2
    assert "No hay evidencia de suelo expuesto en T1" in h2
    # La nota debe citar el valor de B11 con dominio T1
    assert "B11_T1=240 DN" in h2
    # H3 también incorpora la nota de lectura física
    h3_match = re.search(r"\|\s*\*\*H3\..*\|", md)
    assert h3_match is not None and "no con exposición mineral" in h3_match.group(0)


def test_r3_high_b11_terrain_keeps_mineral_wording_without_note() -> None:
    feat = _feature_with()  # caso T4 base: B11_T1=1573, MNDWI=-0.363 (terreno emergido)
    md = generate_candidate_dossier(feat, _sample_metadata())
    h2 = _h2_row(md)
    assert "Incremento de suelo expuesto" in h2
    assert "no con exposición mineral" not in h2


# ---------------------------------------------------------------------------
# R4 — Lenguaje del score heurístico (C5)
# ---------------------------------------------------------------------------


def test_r4_forbidden_probability_language_absent() -> None:
    md = generate_candidate_dossier(_feature_with(), _sample_metadata())
    lowered = md.lower()
    for forbidden in (
        "certeza estadística",
        "probabilidad de extracción",
        "probabilidad de que",
        "confianza del modelo",
        "baja probabilidad",
    ):
        assert forbidden not in lowered, f"Lenguaje prohibido presente: '{forbidden}'"
    # El score debe presentarse con la aclaración de no-probabilidad ya establecida
    assert "no" in md and "Score numérico interno" in md


# ---------------------------------------------------------------------------
# R2 — Provenance de métricas del dossier (C3)
# ---------------------------------------------------------------------------


def test_r2_key_metrics_in_dossier_match_structured_payload() -> None:
    """Cada métrica clave impresa en el dossier proviene del payload estructurado (report.json).

    Se replica el payload que `report_generator` ensambla (feature + detection_metadata) y se
    verifica que los valores formateados del Markdown correspondan al valor estructurado.
    """
    feat = _feature_with()
    meta = _sample_metadata()
    md = generate_candidate_dossier(feat, meta)

    props = feat["properties"]
    stats = props["spectral_stats"]
    checks: list[tuple[str, Any]] = [
        # (subcadena esperada en md, valor estructurado que la origina)
        (f"{props['area_m2']:,.2f} m²", props["area_m2"]),
        (f"ΔBSI_adj={props['delta_bsi_mean']:.4f}", props["delta_bsi_mean"]),
        (f"ΔNDVI_adj={props['delta_ndvi_mean']:.4f}", props["delta_ndvi_mean"]),
        (f"ΔMNDWI={props['delta_mndwi_mean']:.4f}", props["delta_mndwi_mean"]),
        (f"`{props['confidence']:.3f}`", props["confidence"]),
        (f"{meta['hydrological_controls']['stage_t0_m']:.2f}", meta["hydrological_controls"]["stage_t0_m"]),
        (f"{meta['hydrological_controls']['stage_t1_m']:.2f}", meta["hydrological_controls"]["stage_t1_m"]),
        (f"{meta['hydrological_controls']['water_stage_delta_pct']}%", meta["hydrological_controls"]["water_stage_delta_pct"]),
        (f"`B11_T1={stats['t1_b11_median']:.0f} DN`", stats["t1_b11_median"]),
        (f"{stats['t1_mndwi_median']:.3f}", stats["t1_mndwi_median"]),
    ]
    for expected_substring, structured_value in checks:
        assert expected_substring in md, (
            f"Métrica estructurada {structured_value!r} no aparece con su formato declarado en el dossier"
        )

    # Parámetros congelados: los citados en el dossier deben coincidir exactamente con el payload
    calib = meta["calibration_parameters"]
    for key, value in calib.items():
        assert f"`{key}={value}`" in md
