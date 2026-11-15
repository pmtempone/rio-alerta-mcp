"""Experimento 002 — Validación científica congelada de Fase 2.1 (P0.3).

Ejecuta con parámetros congelados (sin ajuste posterior sobre AOI_2):
1. Regresión `exp_001_recalibrated` sobre rasters cacheados de Exp 001 (sin modificar exp_001_parana_medio/).
2. Pares comparables en estiaje sobre `AOI_1` (Paraná Medio 2024->2025 y 2023->2026).
3. Validación independiente fuera de muestra sobre `AOI_2` (Diamante, 2023-08-29 -> 2025-08-28).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pystac_client import Client  # noqa: E402

from src.config import settings  # noqa: E402
from src.core.change_detector import detect_extraction_anomalies  # noqa: E402
from src.core.crs_manager import bbox_wgs84_to_posgar  # noqa: E402
from src.core.hydrological_control import fetch_ina_stage_height_with_provenance  # noqa: E402
from src.core.optical_providers import _export_stac_item_to_posgar_geotiffs  # noqa: E402

AOI_1_BBOX = (-60.66, -31.74, -60.53, -31.64)
AOI_2_BBOX = (-60.75, -32.12, -60.61, -32.02)

EXP1_RASTERS = PROJECT_ROOT / "data" / "experiments" / "exp_001_parana_medio" / "rasters"
OUT_RECAL = PROJECT_ROOT / "data" / "experiments" / "exp_001_recalibrated"
OUT_EXP2 = PROJECT_ROOT / "data" / "experiments" / "exp_002_validation"


def _fetch_item_by_id(
    client: Client,
    bbox: Tuple[float, float, float, float],
    scene_id: str,
    date_range: str,
) -> Any:
    search = client.search(
        collections=["sentinel-2-l2a"],
        bbox=list(bbox),
        datetime=date_range,
        max_items=25,
    )
    for item in search.items():
        if item.id == scene_id:
            return item
    raise RuntimeError(f"Escena STAC '{scene_id}' no encontrada en rango {date_range}.")


def _ensure_stac_scene(
    client: Client,
    bbox: Tuple[float, float, float, float],
    scene_id: str,
    date_range: str,
    out_dir: Path,
    prefix: str,
) -> Tuple[Path, Path]:
    vnir_path = out_dir / f"{prefix}_{scene_id}_vnir10m.tif"
    swir_path = out_dir / f"{prefix}_{scene_id}_swir_scl20m.tif"
    if vnir_path.exists() and swir_path.exists():
        return vnir_path, swir_path

    bounds_posgar, crs_epsg = bbox_wgs84_to_posgar(bbox)
    item = _fetch_item_by_id(client, bbox, scene_id, date_range)
    v_p, s_p, _ = _export_stac_item_to_posgar_geotiffs(
        item=item,
        bounds_posgar=bounds_posgar,
        crs_epsg=crs_epsg,
        out_dir=out_dir,
        prefix=prefix,
        sign_planetary=False,
    )
    return v_p, s_p


def _save_geojson(res: Any, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _summarize_run(name: str, res: Any) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    by_zone: Dict[str, int] = {}
    feats_brief: List[Dict[str, Any]] = []
    for f in res.features:
        p = f["properties"]
        atype = p["anomaly_type"]
        ztype = p.get("geomorphological_zone", "NO_CLASIFICADA")
        by_type[atype] = by_type.get(atype, 0) + 1
        by_zone[ztype] = by_zone.get(ztype, 0) + 1
        feats_brief.append(
            {
                "id": f["id"],
                "type": atype,
                "zone": ztype,
                "area_m2": p["area_m2"],
                "confidence": p["confidence"],
                "centroid_wgs84": p["centroid_wgs84"],
                "t1_b11_median": p["spectral_stats"]["t1_b11_median"] if p.get("spectral_stats") else None,
                "t1_scl_counts": p["spectral_stats"]["t1_scl_counts"] if p.get("spectral_stats") else None,
            }
        )
    return {
        "run": name,
        "candidate_count": res.metadata["candidate_count"],
        "by_type": by_type,
        "by_zone": by_zone,
        "hydrological_controls": res.metadata["hydrological_controls"],
        "geomorphological_zone_fractions": res.metadata.get("geomorphological_zone_fractions"),
        "top_candidates": sorted(feats_brief, key=lambda x: x["area_m2"], reverse=True)[:10],
    }


def _safe_ina_stage(series_id: int, date_str: str, fallback: Optional[float]) -> Tuple[Optional[float], str]:
    val, src = fetch_ina_stage_height_with_provenance(series_id=series_id, target_date_iso=date_str)
    if val is not None:
        return val, src
    if fallback is not None:
        return fallback, "FALLBACK_MANUAL"
    return None, "NO_DISPONIBLE"


def main() -> None:
    OUT_RECAL.mkdir(parents=True, exist_ok=True)
    OUT_EXP2.mkdir(parents=True, exist_ok=True)
    rasters_exp2 = OUT_EXP2 / "rasters"
    rasters_exp2.mkdir(parents=True, exist_ok=True)

    summary_runs: List[Dict[str, Any]] = []

    # 1. Regresión Exp 001 recalibrada (T0 = 2023+2024+2025 -> T1 = 2026-11-08)
    print("1/4 Ejecutando exp_001_recalibrated...")
    t0_v_exp1 = [
        EXP1_RASTERS / "t0_S2B_20HQK_20231107_0_L2A_vnir10m.tif",
        EXP1_RASTERS / "t0_S2A_20HQK_20241106_0_L2A_vnir10m.tif",
        EXP1_RASTERS / "t0_S2A_20HQK_20251113_1_L2A_vnir10m.tif",
    ]
    t0_s_exp1 = [
        EXP1_RASTERS / "t0_S2B_20HQK_20231107_0_L2A_swir_scl20m.tif",
        EXP1_RASTERS / "t0_S2A_20HQK_20241106_0_L2A_swir_scl20m.tif",
        EXP1_RASTERS / "t0_S2A_20HQK_20251113_1_L2A_swir_scl20m.tif",
    ]
    t1_v_exp1 = EXP1_RASTERS / "t1_S2A_20HQK_20261108_1_L2A_vnir10m.tif"
    t1_s_exp1 = EXP1_RASTERS / "t1_S2A_20HQK_20261108_1_L2A_swir_scl20m.tif"

    h_paran_2025, s_paran_2025 = _safe_ina_stage(30, "2025-11-13", 2.64)
    h_paran_2026, s_paran_2026 = _safe_ina_stage(30, "2026-11-08", 4.85)
    res_recal = detect_extraction_anomalies(
        t0_vnir_paths=t0_v_exp1,
        t0_swir_scl_paths=t0_s_exp1,
        t1_vnir_path=t1_v_exp1,
        t1_swir_scl_path=t1_s_exp1,
        work_dir=OUT_RECAL / "work",
        stage_t0_m=h_paran_2025,
        stage_t1_m=h_paran_2026,
        stage_t0_source=s_paran_2025,
        stage_t1_source=s_paran_2026,
    )
    _save_geojson(res_recal, OUT_RECAL / "exp_001_recalibrated.geojson")
    summary_runs.append(_summarize_run("exp_001_recalibrated", res_recal))

    # 2. AOI_1 Estiaje 2024-11-06 -> 2025-11-13 (cacheados en exp_001)
    print("2/4 Ejecutando AOI_1 estiaje 2024-11-06 -> 2025-11-13...")
    h_paran_2024, s_paran_2024 = _safe_ina_stage(30, "2024-11-06", 0.67)
    res_aoi1_2425 = detect_extraction_anomalies(
        t0_vnir_paths=EXP1_RASTERS / "t0_S2A_20HQK_20241106_0_L2A_vnir10m.tif",
        t0_swir_scl_paths=EXP1_RASTERS / "t0_S2A_20HQK_20241106_0_L2A_swir_scl20m.tif",
        t1_vnir_path=EXP1_RASTERS / "t0_S2A_20HQK_20251113_1_L2A_vnir10m.tif",
        t1_swir_scl_path=EXP1_RASTERS / "t0_S2A_20HQK_20251113_1_L2A_swir_scl20m.tif",
        work_dir=OUT_EXP2 / "work_aoi1_2425",
        stage_t0_m=h_paran_2024,
        stage_t1_m=h_paran_2025,
        stage_t0_source=s_paran_2024,
        stage_t1_source=s_paran_2025,
    )
    _save_geojson(res_aoi1_2425, OUT_EXP2 / "aoi1_2024_2025.geojson")
    summary_runs.append(_summarize_run("aoi1_2024_2025", res_aoi1_2425))

    # 3. AOI_1 Estiaje comparable 2023-09-28 -> 2026-09-17 (STAC)
    print("3/4 Descargando/ejecutando AOI_1 estiaje comparable 2023-09-28 -> 2026-09-17...")
    client = Client.open(settings.earth_search_stac_url)
    a1_t0_v, a1_t0_s = _ensure_stac_scene(
        client, AOI_1_BBOX, "S2B_20HQK_20230928_0_L2A", "2023-09-25/2023-10-01", rasters_exp2 / "aoi1", "t0"
    )
    a1_t1_v, a1_t1_s = _ensure_stac_scene(
        client, AOI_1_BBOX, "S2C_20HQK_20260917_0_L2A", "2026-09-15/2026-09-20", rasters_exp2 / "aoi1", "t1"
    )
    h_paran_2309, s_paran_2309 = _safe_ina_stage(30, "2023-09-28", 2.30)
    h_paran_2609, s_paran_2609 = _safe_ina_stage(30, "2026-09-17", 2.44)
    res_aoi1_2326 = detect_extraction_anomalies(
        t0_vnir_paths=a1_t0_v,
        t0_swir_scl_paths=a1_t0_s,
        t1_vnir_path=a1_t1_v,
        t1_swir_scl_path=a1_t1_s,
        work_dir=OUT_EXP2 / "work_aoi1_2326",
        stage_t0_m=h_paran_2309,
        stage_t1_m=h_paran_2609,
        stage_t0_source=s_paran_2309,
        stage_t1_source=s_paran_2609,
    )
    _save_geojson(res_aoi1_2326, OUT_EXP2 / "aoi1_2023_2026.geojson")
    summary_runs.append(_summarize_run("aoi1_2023_2026", res_aoi1_2326))

    # 4. AOI_2 Diamante independiente 2023-08-29 -> 2025-08-28 (STAC)
    print("4/4 Descargando/ejecutando AOI_2 Diamante 2023-08-29 -> 2025-08-28...")
    a2_t0_v, a2_t0_s = _ensure_stac_scene(
        client, AOI_2_BBOX, "S2B_20HQK_20230829_0_L2A", "2023-08-26/2023-09-01", rasters_exp2 / "aoi2", "t0"
    )
    a2_t1_v, a2_t1_s = _ensure_stac_scene(
        client, AOI_2_BBOX, "S2B_20HQK_20250828_0_L2A", "2025-08-25/2025-08-31", rasters_exp2 / "aoi2", "t1"
    )
    h_diam_2308, s_diam_2308 = _safe_ina_stage(31, "2023-08-29", 1.85)
    h_diam_2508, s_diam_2508 = _safe_ina_stage(31, "2025-08-28", 1.75)
    res_aoi2 = detect_extraction_anomalies(
        t0_vnir_paths=a2_t0_v,
        t0_swir_scl_paths=a2_t0_s,
        t1_vnir_path=a2_t1_v,
        t1_swir_scl_path=a2_t1_s,
        work_dir=OUT_EXP2 / "work_aoi2_diamante",
        stage_t0_m=h_diam_2308,
        stage_t1_m=h_diam_2508,
        stage_t0_source=s_diam_2308,
        stage_t1_source=s_diam_2508,
    )
    _save_geojson(res_aoi2, OUT_EXP2 / "aoi2_diamante_2023_2025.geojson")
    summary_runs.append(_summarize_run("aoi2_diamante_2023_2025", res_aoi2))

    summary_path = OUT_EXP2 / "exp_002_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "frozen_calibration_parameters": res_recal.metadata["calibration_parameters"],
                "runs": summary_runs,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Completado. Resumen guardado en {summary_path}")


if __name__ == "__main__":
    main()
