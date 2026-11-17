"""Tests de la meta-auditoría P0 (Fase 3.2): script de verificación de inventarios de auditoría."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from scripts.verify_audit_inventory import (
    _check_inventory_hashes,
    _check_spectral_provenance_schema,
    _check_stac_scenes,
    _derive_scene_ids_from_inventory,
    _record,
    verify_inventory,
)


def _write_mini_audit(tmp_path: Path) -> Path:
    """Construye un directorio de auditoría sintético con hashes correctos."""
    audit_dir = tmp_path / "audit_mini"
    audit_dir.mkdir(parents=True)
    artifacts: List[Dict[str, Any]] = []
    for name, content in (("chip_t0.png", b"png-bytes-0"), ("chip_t1.png", b"png-bytes-1")):
        art = audit_dir / name
        art.write_bytes(content)
        artifacts.append({"role": name, "path": str(art), "sha256": hashlib.sha256(content).hexdigest()})

    inv = {
        "audit_target": "RIO-MINI",
        "report_generation_id": "GEN-TEST-0001",
        "local_artifacts": artifacts,
        "external_stac_scenes_consulted": [
            {"scene_id": "S2X_20HQK_20240101_0_L2A", "date": "2024-01-01", "cloud_cover_pct": 1.5}
        ],
    }
    (audit_dir / "evidence_inventory.json").write_text(json.dumps(inv), encoding="utf-8")
    return audit_dir


def test_m1_hash_verification_pass_and_fail_detection(tmp_path: Path) -> None:
    audit_dir = _write_mini_audit(tmp_path)
    results = _check_inventory_hashes(audit_dir)
    assert all(r["status"] == "PASS" for r in results if r["target"].endswith((".png",)))
    # Sin manifest accesible en el entorno sintético -> WARN (no FAIL) del cruzado de generación
    gen_checks = [r for r in results if r["target"].endswith("manifest.json")]
    assert len(gen_checks) == 1 and gen_checks[0]["status"] == "WARN"

    # Adulterar 1 byte del artefacto debe producir FAIL en M1
    chip = audit_dir / "chip_t1.png"
    chip.write_bytes(b"png-bytes-TAMPERED")
    results = _check_inventory_hashes(audit_dir)
    failed = [r for r in results if r["status"] == "FAIL"]
    assert len(failed) == 1 and "chip_t1.png" in failed[0]["target"]


def test_r5_provenance_schema_warns_when_method_metadata_absent(tmp_path: Path) -> None:
    audit_dir = _write_mini_audit(tmp_path)
    metrics_dir = audit_dir / "spectral_analysis"
    metrics_dir.mkdir()
    metrics = [
        {
            "date": "2024-01-01",
            "scene_id": "S2X_20HQK_20240101_0_L2A",
            "ndvi_median": 0.5,
            "scl_10m_counts": {"4": 3, "5": 5},
        }
    ]
    (metrics_dir / "spectral_audit_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    results = _check_spectral_provenance_schema(audit_dir)
    assert len(results) == 1 and results[0]["status"] == "WARN"
    assert "compute_method" in results[0]["detail"]

    # Con el esquema R5 completo, el check pasa
    for entry in metrics:
        entry.update(
            {
                "compute_method": "reimplementacion_independiente@tool_v1",
                "resampling": "nearest(SCL)/bilinear(bandas)",
                "count_domain": "interior_poligono_10m",
                "source_sha256": "0" * 64,
            }
        )
    (metrics_dir / "spectral_audit_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    results = _check_spectral_provenance_schema(audit_dir)
    assert results[0]["status"] == "PASS"


def test_m2_scene_derivation_and_stac_date_match(tmp_path: Path) -> None:
    audit_dir = _write_mini_audit(tmp_path)
    scenes = _derive_scene_ids_from_inventory(audit_dir)
    by_id = {s["scene_id"]: s for s in scenes}
    assert "S2X_20HQK_20240101_0_L2A" in by_id
    assert by_id["S2X_20HQK_20240101_0_L2A"]["date"] == "2024-01-01"

    stac_items = {
        "S2X_20HQK_20240101_0_L2A": {
            "properties": {"datetime": "2024-01-01T14:00:00Z", "eo:cloud_cover": 1.51}
        }
    }

    class _FakeResponse:
        def __init__(self, item: Dict[str, Any]) -> None:
            self._item = item
            self.status_code = 200

        def json(self) -> Dict[str, Any]:
            return self._item

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def get(self, url: str) -> _FakeResponse:
            scene_id = url.rsplit("/", 1)[-1]
            return _FakeResponse(stac_items[scene_id])

    with patch("scripts.verify_audit_inventory.httpx.Client", _FakeClient):
        results = _check_stac_scenes(scenes)
    assert len(results) == 1
    assert results[0]["status"] == "PASS"
    assert "2024-01-01" in results[0]["detail"]
    assert "Nubosidad consistente" in results[0]["detail"]

    # Fecha divergente en STAC -> FAIL
    stac_items["S2X_20HQK_20240101_0_L2A"]["properties"]["datetime"] = "2023-12-30T14:00:00Z"
    with patch("scripts.verify_audit_inventory.httpx.Client", _FakeClient):
        results = _check_stac_scenes(scenes)
    assert results[0]["status"] == "FAIL" and "Fecha divergente" in results[0]["detail"]


def test_verify_inventory_offline_summary_and_record_shape(tmp_path: Path) -> None:
    audit_dir = _write_mini_audit(tmp_path)
    report = verify_inventory(audit_dir, check_stac=False)
    assert report["meta_audit_version"] == "phase3.2.p0.v1"
    statuses = {r["status"] for r in report["results"]}
    assert statuses.issubset({"PASS", "FAIL", "WARN", "MANUAL"})
    # M5/M6 siempre se reportan como manuales
    manual = [r["check"] for r in report["results"] if r["status"] == "MANUAL"]
    assert set(manual) == {"M5", "M6"}
    # Sin metrics file, R5 no aparece (no hay análisis espectral que auditar)
    assert "R5" not in {r["check"] for r in report["results"]}

    rec = _record("X", "t", "PASS", "d")
    assert set(rec.keys()) == {"check", "target", "status", "detail"}
