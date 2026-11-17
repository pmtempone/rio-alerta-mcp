"""Cola de revisión ciega del Protocolo de Validación Independiente (Fase 3.2, P3).

Genera el paquete de revisión con ciego obligatorio (protocolo §4.3): el revisor recibe
únicamente polígono, escenas, fechas, resolución y chips opcionales — **nunca** los campos
interpretativos del detector (tipología, score, zona, prioridad, estado de descarte).
La correspondencia ciega↔real queda en un manifiesto privado que no se entrega al revisor.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

QUEUE_SCHEMA_VERSION = "phase3.2.blind.v1"

REVIEW_ACT_TEMPLATE = """# Acta de Revisión Independiente en Ciego — {blind_id}

> Protocolo de Validación Independiente (Fase 3.2) §4.3–§4.7. Completar ANTES de acceder a la
> salida del detector, al catastro o a revisiones de terceros. Un acta por caso.

| Campo | Valor |
| :--- | :--- |
| **ID ciego** | `{blind_id}` |
| **Revisor (evaluator_id)** | |
| **Fecha de revisión (UTC)** | |
| **Declaración de independencia** | (sin participación en desarrollo/calibración del detector; sin acceso a catastro antes del etiquetado) |

## 1. Observación (A — cambio territorial)

- ¿Existe cambio real entre las escenas? (Sí / No / Indeterminado):
- ¿Persistente entre fechas? (Sí / No / Indeterminado):
- Calidad de observación (nubes, bordes, resolución):

## 2. Hipótesis (B — interpretación, ranking con plausibilidad)

| Hipótesis | Plausibilidad (ALTA/MEDIA/BAJA/DESCARTADA) | Fundamento |
| :--- | :--- | :--- |
| H1. Extracción o acopio de áridos | | |
| H2. Obra civil / infraestructura | | |
| H3. Dinámica fluvial / erosión / inundación | | |
| H4. Artefacto atmosférico | | |
| H5. Otra (especificar) | | |

## 3. Etiqueta de referencia propuesta (§4.7)

- `CAMBIO_ANTROPICO_CONFIRMADO` / `DINAMICA_NATURAL_CONFIRMADA` /
  `CAMBIO_REAL_CAUSA_INDETERMINADA` / `SIN_CAMBIO_REAL` / `INDETERMINADO_REVISION_IMPOSIBLE`:

## 4. Nivel de evidencia disponible (§4.5)

- E0 (solo-satelital ≥10 m) / E1 (contexto público verificado) / E2 (VHR) / E3 (campo/catastro):

## 5. Limitaciones

-

## 6. Firma

| | |
| :--- | :--- |
| **Firma del revisor** | |
| **Aclaración** | |
| **Registrada en workflow (HUMAN_EXPERT)** | (evaluator_id; la acreditación la valida el organismo) |
"""


def build_blind_queue_from_geojson(
    geojson: Dict[str, Any],
    discarded_log: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Adaptador para un `ChangeDetectionResult` serializado (features + metadata)."""
    features = list(geojson.get("features", []))
    return build_blind_queue(features=features, discarded_records=discarded_log, **kwargs)


# Campos que el revisor ve por caso (protocolo §4.3: contexto mínimo).
_BLIND_ENTRY_KEYS = (
    "blind_id",
    "poligono_wgs84",
    "centroide_wgs84",
    "escenas",
    "resolucion_m",
    "chips",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _blind_id(candidate_uid: str, salt: str) -> str:
    """Identificador ciego derivado: no revela `candidate_uid` ni su componente tipológico."""
    digest = hashlib.sha256(f"{salt}:{candidate_uid}".encode("utf-8")).hexdigest()[:10].upper()
    return f"REV-{digest}"


def _stratum_of(props: Dict[str, Any]) -> Tuple[str, str]:
    """Estrato de muestreo (protocolo §4.2): tipología de detección × zona geomorfológica."""
    return (str(props.get("anomaly_type", "SIN_TIPO")), str(props.get("geomorphological_zone", "NO_CLASIFICADA")))


def sample_stratified(
    features: Sequence[Dict[str, Any]],
    min_per_stratum: int = 20,
    seed: int = 2026,
) -> List[Dict[str, Any]]:
    """Muestreo estratificado con cuotas mínimas por estrato (§4.2), determinista por semilla.

    Toma hasta `min_per_stratum` casos por estrato, o todos si el estrato es menor. Los casos
    elegibles son las *features* de candidatos retenidos; los descartados se mezclan aguas
    abajo por el llamador con el mismo procedimiento.
    """
    if min_per_stratum < 1:
        raise ValueError("min_per_stratum debe ser mayor o igual a 1.")
    strata: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for feat in features:
        props = feat.get("properties", feat)
        strata.setdefault(_stratum_of(props), []).append(feat)

    rng = random.Random(seed)
    selected: List[Dict[str, Any]] = []
    for _, members in sorted(strata.items()):
        if len(members) <= min_per_stratum:
            selected.extend(members)
        else:
            selected.extend(rng.sample(members, min_per_stratum))
    return selected


def _extract_blind_entry(
    candidate_uid: str,
    salt: str,
    props: Dict[str, Any],
    geometry: Optional[Dict[str, Any]],
    resolution_m: Optional[float],
    chips: List[str],
    scenes: Optional[Sequence[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Construye la entrada ciega del revisor, sin campos interpretativos (A/B según §2)."""
    centroid = props.get("centroid_wgs84")
    return {
        "blind_id": _blind_id(candidate_uid, salt),
        "poligono_wgs84": geometry,
        "centroide_wgs84": list(centroid) if centroid else None,
        "escenas": [
            {"scene_id": str(s.get("scene_id", "")), "fecha": str(s.get("fecha", s.get("date", "")))}
            for s in (scenes or [])
        ],
        "resolucion_m": resolution_m,
        "chips": list(chips),
    }


def build_blind_queue(
    features: Sequence[Dict[str, Any]],
    discarded_records: Optional[Sequence[Dict[str, Any]]] = None,
    min_per_stratum: int = 20,
    seed: int = 2026,
    chips_by_candidate: Optional[Dict[str, List[str]]] = None,
    scenes_by_candidate: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    resolution_m: Optional[float] = None,
    source_label: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Construye el paquete ciego (hoja de trabajo del revisor) y el manifiesto privado.

    Args:
        features: Features GeoJSON de candidatos retenidos (salida del detector).
        discarded_records: Registros de la bitácora de descartes (también muestreables; el
            revisor no debe saber que fueron descartados, por lo que se tratan igual).
        min_per_stratum: Cuota mínima por estrato (§4.2).
        seed: Semilla determinista del muestreo.
        chips_by_candidate: Mapa candidate_uid → rutas de chips RGB (opcional).
        scenes_by_candidate: Mapa candidate_uid → escenas (opcional; si falta, entradas sin escenas).
        resolution_m: Resolución efectiva a declarar al revisor (opcional).
        source_label: Etiqueta de provenance de la corrida de origen.

    Returns:
        Tupla `(worksheet, private_manifest)`; el worksheet es lo único que recibe el revisor.
    """
    salt = uuid4().hex
    chips_map = chips_by_candidate or {}
    scenes_map = scenes_by_candidate or {}

    sampled_candidates = sample_stratified(list(features), min_per_stratum=min_per_stratum, seed=seed)

    entries: List[Dict[str, Any]] = []
    private: List[Dict[str, Any]] = []

    def _register(uid: str, props: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> None:
        entry = _extract_blind_entry(
            candidate_uid=uid,
            salt=salt,
            props=props,
            geometry=geometry,
            resolution_m=resolution_m,
            chips=chips_map.get(uid, []),
            scenes=scenes_map.get(uid),
        )
        entries.append({key: entry[key] for key in _BLIND_ENTRY_KEYS})
        private.append(
            {
                "blind_id": entry["blind_id"],
                "candidate_uid": uid,
                "stratum": list(_stratum_of(props)),
                "kind": "candidate",
                "anomaly_type": props.get("anomaly_type"),
                "signal_score": props.get("confidence"),
                "geomorphological_zone": props.get("geomorphological_zone"),
            }
        )

    for feat in sampled_candidates:
        props = feat.get("properties", feat)
        uid = str(props.get("candidate_uid") or props.get("id") or "")
        if not uid:
            continue
        _register(uid, props, feat.get("geometry"))

    for record in list(discarded_records or []):
        uid = str(record.get("candidate_uid") or "")
        if not uid:
            continue
        private.append(
            {
                "blind_id": _blind_id(uid, salt),
                "candidate_uid": uid,
                "stratum": ["DESCARTADO", record.get("filter_status", "SIN_MOTIVO")],
                "kind": "discarded",
                "anomaly_type": record.get("anomaly_type"),
                "filter_status": record.get("filter_status"),
            }
        )
        entries.append(
            {
                "blind_id": _blind_id(uid, salt),
                "poligono_wgs84": None,
                "centroide_wgs84": list(record.get("centroid_wgs84", (None, None))) or None,
                "escenas": [],
                "resolucion_m": resolution_m,
                "chips": chips_map.get(uid, []),
            }
        )

    worksheet = {
        "queue_schema_version": QUEUE_SCHEMA_VERSION,
        "blind_review": True,
        "instrucciones": (
            "Etiquete cada caso ANTES de cualquier acceso a la salida del detector, al catastro o a "
            "revisiones de terceros (protocolo §4.3): física del cambio y ranking de hipótesis con "
            "plausibilidad. Complete la plantilla de acta por caso. El identificador ciego no "
            "revela la tipología del detector."
        ),
        "source_label": source_label,
        "muestreo": {"min_per_stratum": min_per_stratum, "seed": seed, "n_casos": len(entries)},
        "casos": entries,
    }
    private_manifest = {
        "queue_schema_version": QUEUE_SCHEMA_VERSION,
        "salt": salt,
        "generated_at_utc": _utc_now_iso(),
        "source_label": source_label,
        "muestreo": {
            "min_per_stratum": min_per_stratum,
            "seed": seed,
            "n_candidatos_marcos": len(features),
            "n_descartados_marcos": len(discarded_records or []),
        },
        "cruce": private,
        "nota": (
            "MANIFIESTO PRIVADO: no entregar al revisor hasta completar el etiquetado en ciego. "
            "Contiene la correspondencia blind_id ↔ candidate_uid y los campos interpretativos."
        ),
    }
    return worksheet, private_manifest


def write_blind_queue(
    worksheet: Dict[str, Any],
    private_manifest: Dict[str, Any],
    output_dir: Path,
    chips_by_candidate: Optional[Dict[str, List[str]]] = None,
) -> Tuple[Path, Path, Path]:
    """Publica el paquete ciego: hoja de trabajo, manifiesto privado y plantilla de acta.

    Copia los chips referenciados al interior del directorio del paquete (si se indican),
    de modo que el revisor trabaje solo con el paquete autocontenido.
    """
    run_id = f"blind_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:6]}"
    queue_dir = Path(output_dir) / run_id
    queue_dir.mkdir(parents=True, exist_ok=True)

    worksheet_path = queue_dir / "review_worksheet.json"
    worksheet_path.write_text(json.dumps(worksheet, indent=2, ensure_ascii=False), encoding="utf-8")

    private_path = queue_dir / "private_manifest.json"
    private_path.write_text(json.dumps(private_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    act_path = queue_dir / "REVIEW_ACT_TEMPLATE.md"
    act_path.write_text(REVIEW_ACT_TEMPLATE, encoding="utf-8")

    for chips in (chips_by_candidate or {}).values():
        for chip in chips:
            src = Path(chip)
            if src.is_file():
                shutil.copy2(src, queue_dir / src.name)
    return queue_dir, worksheet_path, private_path
