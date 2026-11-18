"""Generador de figuras de análisis para expedientes sin map_artifacts (Fase 3.2).

Produce, sin red y de forma determinista:
- Mapa de contexto del polígono (siempre disponible: solo geometría POSGAR 2007).
- Chips RGB verdadero-color T0/T1 recortados sobre los rasters VNIR nativos (si se proveen rutas),
  con la geometría del candidato superpuesta y estiramiento percentílico 2–98.
- Comparación lado a lado T0 vs T1 cuando existen ambas fechas.

Los artefactos se generan en el directorio que indique el llamador (el `staging` del expediente),
de modo que se publican atómicamente junto al resto de la generación.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")  # backend sin display, seguro en CI y servidores
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from src.config import settings  # noqa: E402
from src.core.crs_manager import reproject_geometry  # noqa: E402
from src.schemas import coerce_anomaly_feature  # noqa: E402


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _polygon_posgar(feature: Any) -> Any:
    """Geometría del candidato en su CRS métrico POSGAR 2007."""
    feat = coerce_anomaly_feature(feature)
    geom = shape(reproject_geometry(feat.geometry_geojson, "EPSG:4326", f"EPSG:{feat.crs_epsg}"))
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def _read_rgb_window(
    raster_path: Path,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    buffer_m: float,
) -> tuple[np.ndarray, Any]:
    """Lee la ventana VNIR alrededor del bbox+buffer y devuelve RGB estirado (B04,B03,B02)."""
    pad_x = buffer_m + 60.0
    pad_y = buffer_m + 60.0
    with rasterio.open(raster_path) as src:
        win = from_bounds(
            minx - pad_x, miny - pad_y, maxx + pad_x, maxy + pad_y, transform=src.transform
        ).round_offsets().round_lengths()
        n_bands = src.count
        # Orden estándar de la grilla VNIR exportada: (B02, B03, B04, B08) — RGB usa B04, B03, B02.
        red_idx, green_idx, blue_idx = (3, 2, 1) if n_bands >= 3 else (1, 1, 1)
        red = src.read(red_idx, window=win, boundless=True, fill_value=0).astype(np.float32)
        green = src.read(green_idx, window=win, boundless=True, fill_value=0).astype(np.float32)
        blue = src.read(blue_idx, window=win, boundless=True, fill_value=0).astype(np.float32)
        win_transform = src.window_transform(win)

    rgb = np.dstack([red, green, blue])
    valid = rgb[np.any(rgb > 0, axis=2)]
    if valid.size > 0:
        low = np.percentile(valid, 2.0)
        high = max(np.percentile(valid, 98.0), low + 1.0)
        rgb = np.clip((rgb - low) / (high - low), 0.0, 1.0)
    rgb = np.nan_to_num(rgb, nan=0.0)
    return np.clip(rgb, 0.0, 1.0), win_transform


def _draw_polygon_overlay(ax: Any, poly: Any, win_transform: Any) -> None:
    """Superpone el contorno del candidato sobre un eje con transform de ventana raster."""
    inv = ~win_transform
    xs, ys = poly.exterior.xy
    cols, rows = [], []
    for x_val, y_val in zip(xs, ys):
        col_f, row_f = inv * (x_val, y_val)
        cols.append(col_f)
        rows.append(row_f)
    ax.plot(cols, rows, color="#ff3b30", linewidth=2.0)
    ax.plot(
        [cols[-1], cols[0]],
        [rows[-1], rows[0]],
        color="#ff3b30",
        linewidth=2.0,
    )


def _render_context_map(poly: Any, crs_epsg: int, out_png: Path, fid: str, title: str) -> Path:
    """Mapa de contexto a partir de la geometría sola (siempre disponible, sin rasters)."""
    minx, miny, maxx, maxy = poly.bounds
    width = max(1.0, maxx - minx)
    height = max(1.0, maxy - miny)
    pad = 0.35 * max(width, height)
    centroid = poly.centroid

    fig, ax = plt.subplots(figsize=(7, 6), dpi=140)
    ax.add_patch(
        MplPolygon(
            list(poly.exterior.coords),
            closed=True,
            facecolor="#ffb84d",
            edgecolor="#c0392b",
            linewidth=2.0,
            alpha=0.85,
            label="Polígono del candidato",
        )
    )
    ax.plot(centroid.x, centroid.y, marker="+", color="#1f4e79", markersize=14, mew=2.0, label="Centroide")
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect("equal")
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)
    ax.set_xlabel(f"X POSGAR 2007 (m) — EPSG:{crs_epsg}")
    ax.set_ylabel(f"Y POSGAR 2007 (m) — EPSG:{crs_epsg}")
    ax.set_title(title, fontsize=11)
    # Barra de escala
    scale_len = 10 ** len(str(int(width))) / 5.0 if width > 0 else 100.0
    if scale_len > width:
        scale_len = width / 2.0
    x0, y0 = minx - pad * 0.6, miny - pad * 0.75
    ax.annotate(
        "",
        xy=(x0 + scale_len, y0),
        xytext=(x0, y0),
        arrowprops=dict(arrowstyle="-", color="black", linewidth=1.6),
    )
    ax.text(x0 + scale_len / 2.0, y0 + pad * 0.05, f"{scale_len:,.0f} m", ha="center", fontsize=8)
    ax.annotate("N", xy=(0.06, 0.96), xycoords="axes fraction", fontsize=12, fontweight="bold")
    ax.annotate("↑", xy=(0.06, 0.90), xycoords="axes fraction", fontsize=12)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, format="png")
    plt.close(fig)
    return out_png


def generate_report_figures(
    feature: Any,
    out_dir: Path,
    raster_paths: Optional[Dict[str, Optional[str]]] = None,
    scene_labels: Optional[Dict[str, str]] = None,
    buffer_m: Optional[float] = None,
) -> List[Path]:
    """Genera las figuras de análisis del expediente y devuelve sus rutas.

    Args:
        feature: `ExtractionAnomalyFeature`, dict plano o Feature GeoJSON del candidato.
        out_dir: Directorio destino de los PNG (típicamente `<staging>/figures`).
        raster_paths: Rutas VNIR 10 m por fecha (`"t0"`, `"t1"`); ausente o None por fecha => sin chip.
        scene_labels: Etiquetas de fecha por clave (`{"t0": "2023-09-28", "t1": "2026-09-17"}`).
        buffer_m: Buffer métrico alrededor del bbox (default `settings.optical_buffer_m`).

    Returns:
        Lista de rutas absolutas de los PNG generados (contexto siempre; chips si hay rasters).
    """
    feat = coerce_anomaly_feature(feature)
    fid = feat.id
    eff_buffer = float(buffer_m if buffer_m is not None else settings.optical_buffer_m)
    labels = scene_labels or {}
    out_dir.mkdir(parents=True, exist_ok=True)

    poly = _polygon_posgar(feat)
    generated: List[Path] = []

    context_png = out_dir / f"mapa_contexto_{fid}.png"
    _render_context_map(
        poly,
        feat.crs_epsg,
        context_png,
        fid,
        f"Contexto espacial del candidato {fid} (POSGAR 2007 EPSG:{feat.crs_epsg})",
    )
    generated.append(context_png)

    # Lectura única por fecha: RGB estirado + transform de ventana para el overlay
    rgb_data: Dict[str, tuple[np.ndarray, Any]] = {}
    for key in ("t0", "t1"):
        raster = (raster_paths or {}).get(key)
        if not raster:
            continue
        raster_path = Path(raster)
        if not raster_path.is_file():
            continue
        rgb_data[key] = _read_rgb_window(
            raster_path,
            poly.bounds[0],
            poly.bounds[1],
            poly.bounds[2],
            poly.bounds[3],
            eff_buffer,
        )

    rgb_pngs: Dict[str, Path] = {}
    for key, (rgb, win_transform) in rgb_data.items():
        label = labels.get(key, key.upper())
        fig, ax = plt.subplots(figsize=(7, 6), dpi=140)
        h, w = rgb.shape[:2]
        ax.imshow(rgb, extent=(0.0, float(w), float(h), 0.0), interpolation="nearest")
        _draw_polygon_overlay(ax, poly, win_transform)
        ax.set_title(f"Chip RGB verdadero-color — {key.upper()} ({label}) — candidato {fid}", fontsize=11)
        ax.set_xlabel("Columna (píxeles de 10 m)")
        ax.set_ylabel("Fila (píxeles de 10 m)")
        fig.tight_layout()
        chip_png = out_dir / f"chip_rgb_{key}_{fid}.png"
        fig.savefig(chip_png, format="png")
        plt.close(fig)
        rgb_pngs[key] = chip_png
        generated.append(chip_png)

    if "t0" in rgb_data and "t1" in rgb_data:
        fig, axes = plt.subplots(1, 2, figsize=(12, 6), dpi=140)
        for ax, key in zip(axes, ("t0", "t1")):
            rgb, win_transform = rgb_data[key]
            h, w = rgb.shape[:2]
            ax.imshow(rgb, extent=(0.0, float(w), float(h), 0.0), interpolation="nearest")
            _draw_polygon_overlay(ax, poly, win_transform)
            ax.set_title(f"{key.upper()} ({labels.get(key, key.upper())})", fontsize=11)
            ax.set_xlabel("Columna (10 m)")
            ax.set_ylabel("Fila (10 m)")
        fig.suptitle(f"Comparación multitemporal T0 vs T1 — candidato {fid}", fontsize=12)
        fig.tight_layout()
        comparison_png = out_dir / f"comparacion_t0_t1_{fid}.png"
        fig.savefig(comparison_png, format="png")
        plt.close(fig)
        generated.append(comparison_png)

    return generated
