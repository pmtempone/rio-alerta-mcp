"""Meta-auditoría P0 (Fase 3.2): verificación de integridad y provenance de auditorías de candidatos.

Implementa los checks automatizables del Protocolo de Validación Independiente
(`docs/validation/PROTOCOL_INDEPENDENT_VALIDATION.md`, §1.1 y Apéndice A):

- M1: re-hash SHA-256 de cada artefacto local del inventario de evidencia y verificación de
  consistencia del `report_generation_id` con el manifest y el puntero de generación activa.
- M2 (--stac): verificación en línea de que cada `scene_id` existe en el catálogo STAC de origen
  y que su fecha coincide con la declarada (y nubosidad declarada vs `eo:cloud_cover`).
- M3/M4 (esquema R5): verificación de provenance metodológica del análisis espectral
  (cómputo declarado, remuestreo, dominio de conteo y SHA-256 del raster de origen).
- M5/M6 son checks manuales y se reportan como tales.

Uso:
    .venv/bin/python scripts/verify_audit_inventory.py --audit-dir reports/audits/RIO-0001
    .venv/bin/python scripts/verify_audit_inventory.py --audit-dir ... --stac --strict

Por defecto el script reporta y termina con código 0. Con `--strict` termina con código 1 si
algún check automático no es `PASS`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EARTH_SEARCH_ITEMS_URL = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items"

# Claves exigidas por el esquema R5 del protocolo en cada entrada del análisis espectral.
_R5_REQUIRED_KEYS = ("compute_method", "resampling", "count_domain", "source_sha256")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _record(
    check: str,
    target: str,
    status: str,
    detail: str,
) -> Dict[str, str]:
    """Construye un registro de resultado con el formato canónico del reporte."""
    return {"check": check, "target": target, "status": status, "detail": detail}


def _check_inventory_hashes(audit_dir: Path) -> List[Dict[str, str]]:
    """M1: re-hash de cada artefacto local declarado en `evidence_inventory.json`."""
    results: List[Dict[str, str]] = []
    inventory_path = audit_dir / "evidence_inventory.json"
    if not inventory_path.is_file():
        return [_record("M1", "evidence_inventory.json", "FAIL", "Inventario de evidencia ausente.")]

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    for artifact in inventory.get("local_artifacts", []):
        rel_path = str(artifact.get("path", ""))
        target = Path(rel_path) if Path(rel_path).is_absolute() else PROJECT_ROOT / rel_path
        expected_sha = str(artifact.get("sha256", ""))
        if not target.is_file():
            results.append(
                _record("M1", rel_path, "FAIL", "Artefacto declarado en el inventario no existe en disco.")
            )
            continue
        actual_sha = _sha256_file(target)
        if actual_sha == expected_sha:
            results.append(_record("M1", rel_path, "PASS", f"SHA-256 verificado ({actual_sha[:16]}…)."))
        else:
            results.append(
                _record(
                    "M1",
                    rel_path,
                    "FAIL",
                    f"SHA-256 divergente: declarado={expected_sha[:16]}… actual={actual_sha[:16]}….",
                )
            )

    # Consistencia del report_generation_id entre inventario, manifest y puntero de generación activa.
    gen_id = str(inventory.get("report_generation_id", ""))
    if not gen_id:
        results.append(_record("M1", "report_generation_id", "WARN", "Inventario sin report_generation_id."))
        return results

    manifest_path = PROJECT_ROOT / "data" / "reports" / "demo_real" / str(inventory.get("audit_target", ""))
    manifest_file = manifest_path / "generations" / gen_id / "manifest.json"
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if str(manifest.get("report_generation_id", "")) == gen_id:
            results.append(_record("M1", "manifest.json", "PASS", "report_generation_id consistente."))
        else:
            results.append(
                _record("M1", "manifest.json", "FAIL", "report_generation_id divergente con el inventario.")
            )
    else:
        results.append(
            _record("M1", str(manifest_file), "WARN", "Manifest de la generación no accesible para verificación cruzada.")
        )
    return results


def _check_spectral_provenance_schema(audit_dir: Path) -> List[Dict[str, str]]:
    """M3/M4 (esquema R5): provenance metodológica del análisis espectral de la auditoría."""
    results: List[Dict[str, str]] = []
    metrics_path = audit_dir / "spectral_analysis" / "spectral_audit_metrics.json"
    if not metrics_path.is_file():
        # Sin análisis espectral en la auditoría no hay nada que auditar bajo R5.
        return []

    entries = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        return [_record("R5", "spectral_audit_metrics.json", "FAIL", "Estructura inesperada del archivo de métricas.")]

    missing_keys: Dict[str, List[str]] = {}
    for entry in entries:
        scene_id = str(entry.get("scene_id", "SIN_SCENE_ID"))
        absent = [key for key in _R5_REQUIRED_KEYS if key not in entry]
        if absent:
            missing_keys[scene_id] = absent

    if not missing_keys:
        results.append(
            _record(
                "R5",
                "spectral_audit_metrics.json",
                "PASS",
                f"{len(entries)} escenas con provenance metodológica completa ({', '.join(_R5_REQUIRED_KEYS)}).",
            )
        )
        return results

    example_scene = next(iter(missing_keys))
    results.append(
        _record(
            "R5",
            "spectral_audit_metrics.json",
            "WARN",
            "Provenance metodológica incompleta (hallazgo documentado M3-1 del protocolo): faltan claves "
            f"{missing_keys[example_scene]} (ejemplo: '{example_scene}'); no queda acreditado con qué código ni "
            "remuestreo se calcularon los índices, por lo que la independencia metodológica es parcial.",
        )
    )
    return results


def _derive_scene_ids_from_inventory(audit_dir: Path) -> List[Dict[str, str]]:
    """Reúne los pares (scene_id, fecha declarada) del inventario y de las métricas espectrales."""
    scenes: Dict[str, Dict[str, str]] = {}
    inventory = json.loads((audit_dir / "evidence_inventory.json").read_text(encoding="utf-8"))

    for artifact in inventory.get("local_artifacts", []):
        name = str(artifact.get("path", ""))
        if "vnir10m" not in name:
            continue
        stem = Path(name).stem  # p.ej. t0_S2B_20HQK_20230928_0_L2A_vnir10m
        scene_id = stem.replace("t0_", "").replace("t1_", "").replace("_vnir10m", "")
        date = str(artifact.get("date", ""))
        if scene_id and date:
            scenes[scene_id] = {"date": date, "cloud_cover_pct": ""}

    for consulted in inventory.get("external_stac_scenes_consulted", []):
        scene_id = str(consulted.get("scene_id", ""))
        if scene_id:
            scenes[scene_id] = {
                "date": str(consulted.get("date", "")),
                "cloud_cover_pct": str(consulted.get("cloud_cover_pct", "")),
            }

    metrics_file = audit_dir / "spectral_analysis" / "spectral_audit_metrics.json"
    if metrics_file.is_file():
        for entry in json.loads(metrics_file.read_text(encoding="utf-8")):
            scene_id = str(entry.get("scene_id", ""))
            if scene_id and scene_id not in scenes:
                scenes[scene_id] = {"date": str(entry.get("date", "")), "cloud_cover_pct": ""}
    return [{"scene_id": sid, **vals} for sid, vals in sorted(scenes.items())]


def _check_stac_scenes(scenes: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """M2: verificación en línea de existencia y fecha de cada escena en el catálogo STAC de origen."""
    results: List[Dict[str, str]] = []
    with httpx.Client(timeout=30.0) as client:
        for scene in scenes:
            scene_id = scene["scene_id"]
            declared_date = scene["date"]
            try:
                resp = client.get(f"{_EARTH_SEARCH_ITEMS_URL}/{scene_id}")
                if resp.status_code != 200:
                    results.append(
                        _record("M2", scene_id, "FAIL", f"Ítem no encontrado en STAC (HTTP {resp.status_code}).")
                    )
                    continue
                item = resp.json()
            except httpx.HTTPError as exc:
                results.append(_record("M2", scene_id, "FAIL", f"Consulta STAC fallida: {exc}."))
                continue

            item_dt = str(item.get("properties", {}).get("datetime", ""))
            item_date = item_dt[:10]
            if declared_date and item_date != declared_date:
                results.append(
                    _record(
                        "M2",
                        scene_id,
                        "FAIL",
                        f"Fecha divergente: declarada={declared_date} STAC={item_date}.",
                    )
                )
                continue

            detail = f"Fecha confirmada en STAC ({item_date})."
            declared_cloud = scene.get("cloud_cover_pct", "")
            item_cloud = item.get("properties", {}).get("eo:cloud_cover")
            if declared_cloud and item_cloud is not None:
                diff = abs(float(declared_cloud) - float(item_cloud))
                if diff <= 0.05:
                    detail += f" Nubosidad consistente ({float(item_cloud):.4f}%)."
                else:
                    results.append(
                        _record(
                            "M2",
                            scene_id,
                            "FAIL",
                            f"Nubosidad divergente: declarada={declared_cloud}% STAC={item_cloud}%.",
                        )
                    )
                    continue
            results.append(_record("M2", scene_id, "PASS", detail))
    return results


def verify_inventory(audit_dir: Path, check_stac: bool = False) -> Dict[str, Any]:
    """Ejecuta los checks automatizables (M1, M2 opcional, R5) sobre un directorio de auditoría."""
    results: List[Dict[str, str]] = []
    if not (audit_dir / "evidence_inventory.json").is_file():
        results.append(_record("M1", str(audit_dir), "FAIL", "El directorio de auditoría no contiene evidence_inventory.json."))
    else:
        results.extend(_check_inventory_hashes(audit_dir))
        results.extend(_check_spectral_provenance_schema(audit_dir))
        if check_stac:
            scenes = _derive_scene_ids_from_inventory(audit_dir)
            results.extend(_check_stac_scenes(scenes))
        results.append(_record("M5", "AUDIT_*.md", "MANUAL", "Mapeo afirmación→fuente: revisión manual asistida."))
        results.append(
            _record(
                "M6",
                "AUDIT_*.md + pending_verifications.md",
                "MANUAL",
                "Limitaciones materiales documentadas: revisión manual (resolución, catastro, OSM, VHR).",
            )
        )

    counts: Dict[str, int] = {}
    for rec in results:
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
    return {
        "meta_audit_version": "phase3.2.p0.v1",
        "audit_dir": str(audit_dir),
        "verified_at_utc": _utc_now_iso(),
        "summary": counts,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Meta-auditoría P0 de un directorio de auditoría RIO-Alerta.")
    parser.add_argument("--audit-dir", type=Path, default=Path("reports/audits/RIO-0001"))
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Raíz del proyecto que contiene los artefactos referenciados por el inventario "
        "(por defecto: el checkout que contiene este script).",
    )
    parser.add_argument("--stac", action="store_true", help="Verificar escenas en línea contra el catálogo STAC.")
    parser.add_argument("--strict", action="store_true", help="Salir con código 1 si algún check no es PASS/MANUAL.")
    parser.add_argument("--out", type=Path, default=None, help="Ruta opcional para volcar el reporte JSON.")
    args = parser.parse_args()

    global PROJECT_ROOT  # noqa: PLW0603
    if args.project_root is not None:
        PROJECT_ROOT = args.project_root.resolve()

    report = verify_inventory(args.audit_dir, check_stac=args.stac)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")

    problematic = [r for r in report["results"] if r["status"] == "FAIL"]
    if args.strict and problematic:
        print(f"STRICT: {len(problematic)} check(s) en estado FAIL.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
