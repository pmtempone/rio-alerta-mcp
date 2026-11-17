"""Tests Fase 3.2: figuras de análisis automáticas en expedientes sin map_artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from src.core.figure_generator import generate_report_figures
from src.core.report_generator import generate_enforcement_report
from src.server import tool_generate_enforcement_report
from tests.test_phase3_1_audit_hardening import _make_anomaly
from tests.test_review_improvements import _write_local_s2_pair

_FOREST_SIG = (350.0, 600.0, 400.0, 3800.0, 1400.0, 700.0)
_SOIL_SIG = (900.0, 500.0, 700.0, 450.0, 1600.0, 800.0)


def _local_rasters(tmp_path: Path) -> Dict[str, str]:
    """Par VNIR T0/T1 sintético con un cambio localizado (desmonte en T1)."""
    rr, cc = np.meshgrid(np.arange(60), np.arange(60), indexing="ij")
    c0 = np.zeros((6, 60, 60), dtype=np.float32)
    for b_idx, val in enumerate(_FOREST_SIG):
        c0[b_idx] = val
    c1 = c0.copy()
    patch = (rr >= 30) & (rr < 38) & (cc >= 30) & (cc < 38)
    for b_idx, val in enumerate(_SOIL_SIG):
        c1[b_idx][patch] = val

    t0_v, _ = _write_local_s2_pair(tmp_path / "fig_t0", "t0", c0, np.full((60, 60), 4, dtype=np.int32), 5440500.0, 6356300.0)
    t1_v, _ = _write_local_s2_pair(tmp_path / "fig_t1", "t1", c1, np.full((60, 60), 4, dtype=np.int32), 5440500.0, 6356300.0)
    return {"t0": str(t0_v), "t1": str(t1_v)}


def _anomaly_over_rasters() -> Any:
    """Anomalía sintética ubicada dentro de la extensión de los rasters sintéticos."""
    return _make_anomaly(anomaly_id="RIO-FIG-01", minx=5440600.0, miny=6355700.0, width_m=200.0, height_m=200.0)


def test_figures_context_only_without_rasters(tmp_path: Path) -> None:
    anom = _anomaly_over_rasters()
    figures = generate_report_figures(anom, out_dir=tmp_path / "figs")
    assert len(figures) == 1
    assert figures[0].name.startswith("mapa_contexto_")
    assert figures[0].stat().st_size > 1000  # PNG válido no vacío


def test_figures_full_set_with_rasters(tmp_path: Path) -> None:
    rasters = _local_rasters(tmp_path)
    anom = _anomaly_over_rasters()
    figures = generate_report_figures(
        anom,
        out_dir=tmp_path / "figs",
        raster_paths=rasters,
        scene_labels={"t0": "2024-11-06", "t1": "2026-09-17"},
    )
    names = sorted(p.name for p in figures)
    assert names == [
        "chip_rgb_t0_RIO-FIG-01.png",
        "chip_rgb_t1_RIO-FIG-01.png",
        "comparacion_t0_t1_RIO-FIG-01.png",
        "mapa_contexto_RIO-FIG-01.png",
    ]
    for fig in figures:
        assert fig.stat().st_size > 1000
        assert fig.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_report_without_map_artifacts_embeds_generated_figures(tmp_path: Path) -> None:
    rasters = _local_rasters(tmp_path)
    anom = _anomaly_over_rasters()
    out = tmp_path / "reports"
    result = generate_enforcement_report(
        anomaly=anom,
        t0_scene_id="S2X_T0",
        t0_date="2024-11-06",
        t1_scene_id="S2X_T1",
        t1_date="2026-09-17",
        output_dir=out,
        raster_paths=rasters,
    )
    gen_dir = Path(result.generation_dir_path)
    figures_dir = gen_dir / "figures"
    assert figures_dir.is_dir()
    pngs = sorted(figures_dir.glob("*.png"))
    assert len(pngs) >= 3

    # El resultado declara las figuras y existen dentro de la generación publicada
    assert len(result.figure_paths) == len(pngs)
    for fig_path in result.figure_paths:
        assert Path(fig_path).is_file() and str(gen_dir) in fig_path

    # El dossier embebe las figuras con referencias relativas portables
    md = (gen_dir / "report.md").read_text(encoding="utf-8")
    assert "![Figura 1" in md
    for rel in result.figure_paths:
        assert f"figures/{Path(rel).name}" in md

    # El manifest registra cada figura con su SHA-256
    manifest = json.loads((gen_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest.get("figures", {})) == len(pngs)
    for rel, rec in manifest["figures"].items():
        assert len(rec["sha256"]) == 64 and rec["bytes"] > 0


def test_tool_generate_enforcement_report_includes_figures(tmp_path: Path) -> None:
    rasters = _local_rasters(tmp_path)
    anom = _anomaly_over_rasters()
    res = tool_generate_enforcement_report(
        anomaly_dict=anom.model_dump(),
        t0_scene_id="S2X_T0",
        t0_date="2024-11-06",
        t1_scene_id="S2X_T1",
        t1_date="2026-09-17",
        output_dir=str(tmp_path / "reports_tool"),
        t0_vnir_10m_path=rasters["t0"],
        t1_vnir_10m_path=rasters["t1"],
    )
    assert res["status"] == "success"
    assert len(res["figure_paths"]) >= 3
    assert all(Path(p).is_file() for p in res["figure_paths"])

    # Raster inexistente -> validación de entrada
    with pytest.raises(Exception, match="raster VNIR t0"):
        tool_generate_enforcement_report(
            anomaly_dict=anom.model_dump(),
            t0_scene_id="S2X_T0",
            t0_date="2024-11-06",
            t1_scene_id="S2X_T1",
            t1_date="2026-09-17",
            output_dir=str(tmp_path / "reports_tool"),
            t0_vnir_10m_path=str(tmp_path / "no_existe.tif"),
        )
