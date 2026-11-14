"""Tests unitarios de Fase 1: configuración, CRS POSGAR 2007, esquemas Pydantic v2, FastMCP e higiene de dominio."""

import os
from pathlib import Path
from typing import Dict

import numpy as np
import pytest
import rasterio
from pydantic import ValidationError
from rasterio.transform import from_origin

from src.config import POSGAR_2007_EPSG, PROJECT_ROOT, Settings, settings
from src.core.crs_manager import (
    bbox_wgs84_to_posgar,
    ensure_metric_crs,
    get_posgar_faja,
    is_metric_crs,
    reproject_geometry,
)
from src.schemas import (
    ChangeDetectionResult,
    EnforcementReportResult,
    ExtractionAnomalyFeature,
    MultitemporalScenePair,
    PermitMatchInfo,
    PermitValidationResult,
    SceneMetadata,
    SpectralChangeResult,
    VLMFluvialVerdict,
)
from src.server import mcp, tool_resolve_posgar_crs, tool_server_status


def test_config_project_root_and_vsicurl_env() -> None:
    """Verifica PROJECT_ROOT CWD-safe, banderas GDAL/VSICURL y valores por defecto de Settings."""
    assert PROJECT_ROOT.is_dir()
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert (PROJECT_ROOT / "src" / "config.py").is_file()

    assert os.environ.get("GDAL_DISABLE_READDIR_ON_OPEN") == "EMPTY_DIR"
    assert os.environ.get("CPL_VSIL_CURL_ALLOWED_EXTENSIONS") == ".tif,.tiff,.jp2"
    assert os.environ.get("VSI_CACHE") == "TRUE"
    assert os.environ.get("VSI_CACHE_SIZE") == "25000000"

    assert len(POSGAR_2007_EPSG) == 7
    assert settings.permits_gpkg_path == PROJECT_ROOT / "data" / "permits.gpkg"
    assert settings.max_master_side_px == 4096
    assert settings.max_optical_chip_px == 512
    assert settings.min_anomaly_area_m2 == 500.0
    assert settings.excess_tolerance_pct == 5.0


def test_settings_ensure_directories_tmp(tmp_path: Path) -> None:
    """Verifica que ensure_directories crea las carpetas configuradas sin depender del CWD."""
    custom = Settings(
        DATA_DIR=tmp_path / "raw",
        PROCESSED_DIR=tmp_path / "proc",
        REPORTS_DIR=tmp_path / "rep",
    )
    custom.ensure_directories()
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "proc").is_dir()
    assert (tmp_path / "rep").is_dir()


@pytest.mark.parametrize(
    ("lon", "expected_epsg"),
    [
        (-71.5, 5343),  # Faja 1 (-72°) - Cordillera Austral / Santa Cruz Oeste
        (-68.9, 5344),  # Faja 2 (-69°) - Neuquén / Mendoza
        (-65.2, 5345),  # Faja 3 (-66°) - Tucumán / Salta
        (-63.0, 5346),  # Faja 4 (-63°) - Córdoba
        (-60.6, 5347),  # Faja 5 (-60°) - Río Paraná / Rosario / Santa Fe
        (-57.8, 5348),  # Faja 6 (-57°) - Río Uruguay / Corrientes
        (-54.5, 5349),  # Faja 7 (-54°) - Misiones Este
    ],
)
def test_get_posgar_faja_all_zones(lon: float, expected_epsg: int) -> None:
    """Verifica la selección automática de las 7 fajas POSGAR 2007 (EPSG:5343-5349)."""
    assert get_posgar_faja(lon) == expected_epsg


def test_is_metric_crs_and_reprojection_helpers(tmp_path: Path) -> None:
    """Valida detección de CRS métrico, reproyección de rasters WGS84, geometrías y bboxes."""
    assert is_metric_crs("EPSG:5347") is True
    assert is_metric_crs(5344) is True
    assert is_metric_crs("EPSG:4326") is False

    # Reproyección de un GeoJSON Polygon en EPSG:5347 -> EPSG:4326 y retorno idéntico si coincide
    poly_posgar = {
        "type": "Polygon",
        "coordinates": [
            [
                [5441000.0, 6355700.0],
                [5441100.0, 6355700.0],
                [5441100.0, 6355800.0],
                [5441000.0, 6355800.0],
                [5441000.0, 6355700.0],
            ]
        ],
    }
    assert reproject_geometry(poly_posgar, "EPSG:5347", "EPSG:5347") == poly_posgar
    poly_wgs = reproject_geometry(poly_posgar, "EPSG:5347", "EPSG:4326")
    lon0, lat0 = poly_wgs["coordinates"][0][0]
    assert -61.5 < lon0 < -59.5
    assert -34.0 < lat0 < -32.0

    # Conversión de bbox WGS84 a POSGAR 2007
    bbox_posgar, epsg = bbox_wgs84_to_posgar((-60.65, -32.96, -60.60, -32.92))
    assert epsg == 5347
    assert bbox_posgar[2] > bbox_posgar[0]
    assert bbox_posgar[3] > bbox_posgar[1]
    assert (bbox_posgar[2] - bbox_posgar[0]) > 3000.0

    with pytest.raises(ValueError):
        bbox_wgs84_to_posgar((-60.60, -32.92, -60.65, -32.96))

    # ensure_metric_crs: si es métrico retorna el original; si es WGS84 reproyecta a POSGAR 2007
    wgs_tif = tmp_path / "wgs_sample.tif"
    out_metric_tif = tmp_path / "posgar_sample.tif"
    arr = np.ones((16, 16), dtype=np.float32) * 0.42
    arr[0, 0] = -9999.0
    with rasterio.open(
        wgs_tif,
        "w",
        driver="GTiff",
        height=16,
        width=16,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-60.65, -32.92, 0.0005, 0.0005),
        nodata=-9999.0,
    ) as dst:
        dst.write(arr, 1)

    reprojected_path = ensure_metric_crs(wgs_tif, out_metric_tif)
    assert reprojected_path == str(out_metric_tif)
    with rasterio.open(out_metric_tif) as src:
        assert src.crs.to_epsg() == 5347
        assert is_metric_crs(src.crs) is True

    # Si ya está en sistema métrico devuelve el mismo path
    assert ensure_metric_crs(out_metric_tif, tmp_path / "unused.tif") == str(out_metric_tif)


def test_schemas_strict_validation_and_classifications() -> None:
    """Valida todos los esquemas Pydantic v2 de RíoAlerta y verifica extra='forbid'."""
    t0 = SceneMetadata(
        scene_id="S2A_MSIL2A_20240315T135711_N0510_R067_T20HNH_20240315T180000",
        provider="sentinel2_stac",
        acquisition_date="2024-03-15",
        cloud_cover_pct=4.5,
        crs_epsg=5347,
        resolution_m=10.0,
        bands_available=["B02", "B03", "B04", "B08", "B11", "B12"],
    )
    t1 = SceneMetadata(
        scene_id="S2B_MSIL2A_20260315T135709_N0510_R067_T20HNH_20260315T181000",
        provider="sentinel2_stac",
        acquisition_date="2026-03-15",
        cloud_cover_pct=2.1,
        crs_epsg=5347,
        resolution_m=10.0,
        bands_available=["B02", "B03", "B04", "B08", "B11", "B12"],
    )

    pair = MultitemporalScenePair(
        status="success",
        t0_scene=t0,
        t1_scene=t1,
        t0_raster_path="data/raw/t0.tif",
        t1_raster_path="data/raw/t1.tif",
        crs_epsg=5347,
        bounds_posgar=(5440500.0, 6355100.0, 5441700.0, 6356300.0),
        resolution_m=10.0,
    )
    assert pair.crs_epsg == 5347

    spec = SpectralChangeResult(
        status="success",
        delta_mndwi_path="data/processed/dmndwi.tif",
        delta_bsi_path="data/processed/dbsi.tif",
        delta_ndvi_path="data/processed/dndvi.tif",
        crs_epsg=5347,
        resolution_m=10.0,
        bounds_posgar=(5440500.0, 6355100.0, 5441700.0, 6356300.0),
        stats={"delta_mndwi_max": 0.89},
    )
    assert spec.stats["delta_mndwi_max"] == pytest.approx(0.89)

    feat = ExtractionAnomalyFeature(
        id="RIO-0001",
        anomaly_type="frente_excavacion_inundado",
        confidence=0.91,
        area_m2=22500.0,
        area_ha=2.25,
        rectangularity=0.88,
        elongation=1.2,
        delta_mndwi_mean=0.65,
        delta_bsi_mean=0.12,
        delta_ndvi_mean=-0.35,
        bbox_posgar=(5441050.0, 6355700.0, 5441200.0, 6355850.0),
        centroid_posgar=(5441125.0, 6355775.0),
        centroid_wgs84=(-60.63, -32.94),
        crs_epsg=5347,
        geometry_geojson={"type": "Polygon", "coordinates": []},
    )
    fc = ChangeDetectionResult(
        crs={"type": "name", "properties": {"name": "OGC:CRS84"}},
        metadata={"t0_date": "2024-03-15", "t1_date": "2026-03-15"},
        features=[feat.model_dump()],
    )
    assert len(fc.features) == 1

    permit_info = PermitMatchInfo(
        permit_id="PERM-SF-2025-014",
        titular="Arenera del Litoral S.A.",
        expediente="EXP-00412/2025",
        fecha_desde="2025-01-01",
        fecha_hasta="2026-12-31",
        vigente_en_t1=True,
        overlap_area_m2=18000.0,
        overlap_pct=80.0,
        excess_area_m2=4500.0,
        excess_pct=20.0,
        distance_m=0.0,
    )

    for cls_label in (
        "EXTRACCION_AUTORIZADA",
        "EXCESO_DE_POLIGONO",
        "PERMISO_VENCIDO",
        "EXTRACCION_SIN_PERMISO",
    ):
        val = PermitValidationResult(
            anomaly_id="RIO-0001",
            classification=cls_label,  # type: ignore[arg-type]
            matched_permits=[permit_info],
            excess_area_m2=4500.0 if cls_label != "EXTRACCION_AUTORIZADA" else 0.0,
            excess_pct=20.0 if cls_label != "EXTRACCION_AUTORIZADA" else 0.0,
            distance_to_water_m=15.0,
            watercourse_name="Río Paraná",
            priority="ALTA_FISCALIZACION_INMEDIATA" if cls_label != "EXTRACCION_AUTORIZADA" else "BAJA_ARCHIVO",
        )
        assert val.classification == cls_label

    vlm = VLMFluvialVerdict(
        verdict="EXTRACCION_ANTROPICA",
        certainty_pct=93,
        extraction_evidence="Frente rectangular neto sobre banco de arena con camino de acceso.",
        fluvial_dynamics_assessment="Descartada migración natural de meandro por geometría rectilínea.",
        field_inspection_recommended=True,
        backend="mcp_host",
    )
    assert vlm.certainty_pct == 93

    rep = EnforcementReportResult(
        status="success",
        anomaly_id="RIO-0001",
        report_md_path="data/reports/ACTA_RIO-0001.md",
        report_json_path="data/reports/ACTA_RIO-0001.json",
        classification="EXCESO_DE_POLIGONO",
        priority="ALTA_FISCALIZACION_INMEDIATA",
        area_m2=22500.0,
        area_ha=2.25,
        crs_epsg=5347,
        centroid_posgar=(5441125.0, 6355775.0),
        centroid_wgs84=(-60.63, -32.94),
        t0_scene_id=t0.scene_id,
        t0_date=t0.acquisition_date,
        t1_scene_id=t1.scene_id,
        t1_date=t1.acquisition_date,
    )
    assert rep.area_ha == 2.25

    # Rechazo estricto de campos extra (extra='forbid')
    with pytest.raises(ValidationError):
        SceneMetadata(**{**t0.model_dump(), "campo_inventado": 123})
    with pytest.raises(ValidationError):
        ChangeDetectionResult(
            crs={"type": "name", "properties": {"name": "OGC:CRS84"}},
            metadata={},
            features=[],
            campo_extra="no_permitido",
        )


def test_fastmcp_server_and_phase1_tools() -> None:
    """Verifica el registro del servidor FastMCP y la ejecución directa de las herramientas base."""
    assert mcp.name == "RioAlerta-MCP"

    status_payload = tool_server_status()
    assert status_payload["status"] == "ok"
    assert status_payload["server"] == "RioAlerta-MCP"
    assert status_payload["posgar_2007_epsg"][5] == 5347

    crs_payload = tool_resolve_posgar_crs(lon=-60.63, lat=-32.94)
    assert crs_payload["posgar_faja"] == 5
    assert crs_payload["crs_epsg"] == 5347
    assert 5400000.0 < crs_payload["x_posgar_m"] < 5500000.0
    assert 6300000.0 < crs_payload["y_posgar_m"] < 6400000.0


def test_synthetic_multitemporal_s2_pair_fixture(synthetic_multitemporal_s2_pair: Dict[str, Path]) -> None:
    """Verifica el par sintético T0/T1 en EPSG:5347 y los diferenciales ΔMNDWI, ΔBSI y ΔNDVI."""
    t0_path = synthetic_multitemporal_s2_pair["t0_path"]
    t1_path = synthetic_multitemporal_s2_pair["t1_path"]
    assert t0_path.exists()
    assert t1_path.exists()

    with rasterio.open(t0_path) as s0, rasterio.open(t1_path) as s1:
        assert s0.crs.to_epsg() == 5347
        assert s1.crs.to_epsg() == 5347
        assert s0.count == 6
        assert s1.count == 6
        assert s0.descriptions == ("B02", "B03", "B04", "B08", "B11", "B12")
        a0 = s0.read().astype(np.float64)
        a1 = s1.read().astype(np.float64)

    def _mndwi(arr: np.ndarray) -> np.ndarray:
        b03, b11 = arr[1], arr[4]
        return (b03 - b11) / (b03 + b11 + 1e-6)

    def _bsi(arr: np.ndarray) -> np.ndarray:
        b02, b04, b08, b11 = arr[0], arr[2], arr[3], arr[4]
        return ((b11 + b04) - (b08 + b02)) / ((b11 + b04) + (b08 + b02) + 1e-6)

    def _ndvi(arr: np.ndarray) -> np.ndarray:
        b04, b08 = arr[2], arr[3]
        return (b08 - b04) / (b08 + b04 + 1e-6)

    d_mndwi = _mndwi(a1) - _mndwi(a0)
    d_bsi = _bsi(a1) - _bsi(a0)
    d_ndvi = _ndvi(a1) - _ndvi(a0)

    # Zona de excavación inundada (rows 45..60, cols 55..70) -> ΔMNDWI alto positivo
    assert np.mean(d_mndwi[45:60, 55:70]) > 0.70
    # Zona de desmonte/acopio (rows 30..45, cols 85..100) -> ΔBSI positivo y ΔNDVI negativo
    assert np.mean(d_bsi[30:45, 85:100]) > 0.45
    assert np.mean(d_ndvi[30:45, 85:100]) < -0.70
    # Cauce inalterado -> cambio nulo
    assert np.allclose(d_mndwi[0:20, 20:50], 0.0, atol=1e-5)


def test_domain_hygiene_no_legacy_archaeology_references() -> None:
    """Garantiza que ningún archivo del repositorio arrastre términos heredados de arqueología."""
    # Tokens construidos dinámicamente para mantener el repositorio 100% libre de vocabulario ajeno
    forbidden_tokens = [
        "ina" + "pl",
        "25." + "743",
        "hua" + "queo",
        "gua" + "queo",
        "pir" + "ca",
        "chen" + "que",
        "known_" + "sites",
        "archaeo" + "-lidar",
        "sl" + "rm",
        "pd" + "al",
        "blen" + "der",
    ]
    skip_dirs = {".git", ".pytest_cache", "__pycache__", "venv", ".venv"}
    text_suffixes = {".py", ".toml", ".yml", ".yaml", ".md", ".example", ".gitignore", ""}

    violations = []
    for path in PROJECT_ROOT.rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in text_suffixes and path.name not in {"Dockerfile", ".gitignore", ".env.example"}:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore").lower()
        for token in forbidden_tokens:
            if token in content:
                violations.append((str(path.relative_to(PROJECT_ROOT)), token))

    assert violations == [], f"Se detectaron referencias heredadas prohibidas: {violations}"
