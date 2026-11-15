"""Tests P1 (T4) del generador de Dossiers de Investigación Territorial."""

from __future__ import annotations

from pathlib import Path

from src.core.dossier_generator import (
    generate_candidate_dossier,
    generate_negative_or_area_dossier,
    write_dossier_markdown,
)


def _sample_feature() -> dict:
    return {
        "type": "Feature",
        "id": "RIO-0184",
        "geometry": {"type": "Polygon", "coordinates": [[[-60.6, -31.7], [-60.59, -31.7], [-60.59, -31.69], [-60.6, -31.69], [-60.6, -31.7]]]},
        "properties": {
            "id": "RIO-0184",
            "anomaly_type": "desmonte_ribera_acopio",
            "geomorphological_zone": "RIBERA_FIRME_Y_TERRAZA",
            "area_m2": 3097.80,
            "area_ha": 0.3098,
            "rectangularity": 0.452,
            "elongation": 2.31,
            "confidence": 0.593,
            "crs_epsg": 5347,
            "centroid_wgs84": (-60.578912, -31.648123),
            "centroid_posgar": (5445120.45, 6498123.11),
            "distance_to_water_t0_m": 253.0,
            "delta_mndwi_mean": -0.094,
            "delta_bsi_mean": 0.341,
            "delta_ndvi_mean": -0.495,
            "spectral_stats": {
                "area_method": "shapely_polygon_posgar_2007",
                "pixel_count_10m": 29,
                "effective_swir_pixels_20m": 7.25,
                "t0_b11_median": 1846.0,
                "t1_b11_median": 1573.0,
                "t1_b04_median": 930.0,
                "t0_ndvi_median": 0.646,
                "t1_ndvi_median": 0.151,
                "t0_bsi_median": -0.174,
                "t1_bsi_median": 0.149,
                "t1_mndwi_median": -0.363,
                "t1_scl_counts": {"4": 3, "5": 16, "7": 10},
            },
            "uncertainty": {
                "effective_resolution_m": 20.0,
                "valid_pixel_fraction": 0.96,
                "cloud_shadow_adjacent": False,
                "water_stage_delta_pct": 14.2,
                "natural_dynamics_confounder_score": 0.25,
                "persistence_scenes_count": 3,
                "limitations": ["Presencia parcial de clase SCL=7 en T1 (10 px)."],
            },
        },
    }


def _sample_metadata() -> dict:
    return {
        "crs_metric_epsg": 5347,
        "grid_resolution_m": 10.0,
        "effective_swir_resolution_m": 20.0,
        "min_anomaly_area_m2": 1600.0,
        "baseline_scenes_count": 3,
        "valid_pixel_fraction": 0.94,
        "hydrological_controls": {
            "water_stage_delta_pct": 14.2,
            "is_systemic_stage_rise": True,
            "is_systemic_stage_drop": False,
            "stage_t0_m": 2.64,
            "stage_t1_m": 4.85,
            "stage_delta_m": 2.21,
            "bar_migration_dipoles_filtered": 2,
            "riparian_ndvi_seasonal_shift": -0.05,
            "riparian_bsi_seasonal_shift": 0.03,
        },
        "geomorphological_zone_fractions": {
            "cauce_activo": 0.28,
            "barras_moviles": 0.04,
            "planicie_islas": 0.45,
            "ribera_firme": 0.23,
        },
        "calibration_parameters": {
            "bsi_reg_albedo_k": 800.0,
            "dark_water_min_b11_dn": 600.0,
        },
        "candidate_count": 1,
        "institutional_note": "Candidato morfológico para revisión humana y cruce catastral.",
    }


def test_t4_candidate_dossier_has_9_sections_4_levels_and_no_exaggerated_claims(tmp_path: Path) -> None:
    """T4: verifica 9 secciones fijas, 4 niveles epistémicos y ausencia de certezas exageradas."""
    md = generate_candidate_dossier(
        feature=_sample_feature(),
        detection_metadata=_sample_metadata(),
        aoi_name="Paraná Medio (Santa Fe - Paraná)",
        period_label="2023-11-07..2025-11-13 -> 2026-11-08",
    )

    for sec in [
        "## 1. Ficha de identificación",
        "## 2. Resumen ejecutivo",
        "## 3. Evidencia territorial y mapas",
        "## 4. Evolución multitemporal",
        "## 5. Análisis de hipótesis alternativas",
        "## 6. Confiabilidad por dimensión",
        "## 7. Limitaciones y datos faltantes",
        "## 8. Recomendación de actuación",
        "## 9. Fuentes, parámetros y trazabilidad técnica",
    ]:
        assert sec in md

    for lvl in ["**Observación:**", "**Interpretación:**", "**Hipótesis:**", "**Verificación:**"]:
        assert lvl in md

    assert "3,097.80 m²" in md
    assert "0.593" in md
    assert "Alta confianza" not in md
    assert "Media-Alta" not in md
    assert "extracción confirmada" not in md.lower()
    assert "medianas espectrales por fecha" in md
    assert "medias de diferencias por píxel" in md

    out_file = write_dossier_markdown(md, tmp_path / "dossier_RIO-0184.md")
    assert out_file.exists()


def test_t4_negative_dossier_supports_zero_candidates() -> None:
    """T4: verifica generación de dossier limpio cuando candidate_count == 0."""
    meta = _sample_metadata()
    meta["candidate_count"] = 0
    md = generate_negative_or_area_dossier(
        detection_metadata=meta,
        aoi_name="Diamante (Validación independiente)",
        period_label="2023-08-29 -> 2025-08-28",
        bbox_wgs84=[-60.75, -32.12, -60.61, -32.02],
    )
    assert "**0**" in md
    assert "## 9. Fuentes, parámetros y trazabilidad técnica" in md
    assert "**Observación:**" in md
    assert "**Verificación:**" in md
