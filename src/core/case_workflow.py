"""Gestión auditable de expedientes e investigaciones territoriales (JSONL append-only + SQLite)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from src.config import settings
from src.schemas import (
    CaseActionTypeLiteral,
    CaseEventRecord,
    CaseRecord,
    CaseStatusLiteral,
    ExtractionAnomalyFeature,
    InspectionPriorityLiteral,
    coerce_anomaly_feature,
)


def _utc_now_iso() -> str:
    """Devuelve la marca temporal UTC actual en formato ISO-8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _derive_candidate_uid(feat: ExtractionAnomalyFeature) -> str:
    """Obtiene o deriva un identificador determinista de candidato para vincular casos."""
    if feat.candidate_uid and feat.candidate_uid.strip():
        return feat.candidate_uid.strip()
    raw = (
        f"{feat.crs_epsg}:{round(feat.centroid_posgar[0], -1)}:"
        f"{round(feat.centroid_posgar[1], -1)}:{feat.anomaly_type}"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"CAND-{digest}"


def _derive_case_id(candidate_uid: str) -> str:
    """Construye un identificador estable de expediente a partir de `candidate_uid`."""
    sanitized = candidate_uid.replace(" ", "_")
    return f"CASE-{sanitized}"


class CaseWorkflowManager:
    """Administrador de expedientes con bitácora JSONL append-only y vista SQLite indexada."""

    def __init__(self, storage_dir: Optional[Union[str, Path]] = None) -> None:
        base_dir = Path(storage_dir) if storage_dir is not None else (settings.reports_dir / "cases")
        base_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir: Path = base_dir
        self.jsonl_path: Path = base_dir / "case_events.jsonl"
        self.sqlite_path: Path = base_dir / "cases_registry.sqlite"
        self._init_storage()

    def _init_storage(self) -> None:
        if not self.jsonl_path.exists():
            self.jsonl_path.touch()
        with sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    candidate_uid TEXT NOT NULL UNIQUE,
                    anomaly_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    crs_epsg INTEGER NOT NULL,
                    centroid_lon REAL NOT NULL,
                    centroid_lat REAL NOT NULL,
                    area_m2 REAL NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    events_count INTEGER NOT NULL,
                    latest_summary TEXT NOT NULL,
                    human_review_required INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS case_events (
                    event_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    candidate_uid TEXT NOT NULL,
                    anomaly_id TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    human_review_required INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.commit()

    def _persist_event(self, event: CaseEventRecord) -> None:
        """Registra de manera inmutable el evento al final del JSONL y lo inserta en SQLite."""
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")

        with sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT INTO case_events (
                    event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                    action_type, actor, previous_status, new_status, summary,
                    metadata_json, human_review_required
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.case_id,
                    event.candidate_uid,
                    event.anomaly_id,
                    event.timestamp_utc,
                    event.action_type,
                    event.actor,
                    event.previous_status,
                    event.new_status,
                    event.summary,
                    json.dumps(event.metadata, ensure_ascii=False),
                    1 if event.human_review_required else 0,
                ),
            )
            conn.commit()

    def _row_to_case(self, row: Tuple[Any, ...]) -> CaseRecord:
        return CaseRecord(
            case_id=str(row[0]),
            candidate_uid=str(row[1]),
            anomaly_id=str(row[2]),
            status=row[3],
            priority=row[4],
            crs_epsg=int(row[5]),
            centroid_wgs84=(float(row[6]), float(row[7])),
            area_m2=float(row[8]),
            created_at_utc=str(row[9]),
            updated_at_utc=str(row[10]),
            events_count=int(row[11]),
            latest_summary=str(row[12]),
            human_review_required=bool(row[13]),
        )

    def _row_to_event(self, row: Tuple[Any, ...]) -> CaseEventRecord:
        metadata_raw = json.loads(str(row[10])) if row[10] else {}
        return CaseEventRecord(
            event_id=str(row[0]),
            case_id=str(row[1]),
            candidate_uid=str(row[2]),
            anomaly_id=str(row[3]),
            timestamp_utc=str(row[4]),
            action_type=row[5],
            actor=str(row[6]),
            previous_status=row[7],
            new_status=row[8],
            summary=str(row[9]),
            metadata=metadata_raw if isinstance(metadata_raw, dict) else {},
            human_review_required=bool(row[11]),
        )

    def open_or_get_case(
        self,
        anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
        actor: str = "rio-alerta-mcp",
        summary: str = "Apertura inicial de expediente de monitoreo.",
        initial_status: CaseStatusLiteral = "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
        priority: InspectionPriorityLiteral = "MEDIA_VERIFICACION",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CaseRecord:
        """Abre un caso nuevo para el candidato o recupera el existente si ya fue registrado."""
        feat = coerce_anomaly_feature(anomaly)
        candidate_uid = _derive_candidate_uid(feat)

        with sqlite3.connect(self.sqlite_path) as conn:
            existing = conn.execute(
                """
                SELECT case_id, candidate_uid, anomaly_id, status, priority, crs_epsg,
                       centroid_lon, centroid_lat, area_m2, created_at_utc, updated_at_utc,
                       events_count, latest_summary, human_review_required
                FROM cases WHERE candidate_uid = ?
                """,
                (candidate_uid,),
            ).fetchone()
            if existing is not None:
                return self._row_to_case(existing)

        now = _utc_now_iso()
        case_id = _derive_case_id(candidate_uid)
        event_id = f"EVT-{uuid4().hex[:12]}"
        record = CaseRecord(
            case_id=case_id,
            candidate_uid=candidate_uid,
            anomaly_id=feat.id,
            status=initial_status,
            priority=priority,
            crs_epsg=feat.crs_epsg,
            centroid_wgs84=feat.centroid_wgs84,
            area_m2=feat.area_m2,
            created_at_utc=now,
            updated_at_utc=now,
            events_count=1,
            latest_summary=summary,
            human_review_required=True,
        )
        event = CaseEventRecord(
            event_id=event_id,
            case_id=case_id,
            candidate_uid=candidate_uid,
            anomaly_id=feat.id,
            timestamp_utc=now,
            action_type="APERTURA_CASO",
            actor=actor,
            previous_status=None,
            new_status=initial_status,
            summary=summary,
            metadata=metadata or {},
            human_review_required=True,
        )

        with sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, candidate_uid, anomaly_id, status, priority, crs_epsg,
                    centroid_lon, centroid_lat, area_m2, created_at_utc, updated_at_utc,
                    events_count, latest_summary, human_review_required
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.case_id,
                    record.candidate_uid,
                    record.anomaly_id,
                    record.status,
                    record.priority,
                    record.crs_epsg,
                    record.centroid_wgs84[0],
                    record.centroid_wgs84[1],
                    record.area_m2,
                    record.created_at_utc,
                    record.updated_at_utc,
                    record.events_count,
                    record.latest_summary,
                    1,
                ),
            )
            conn.commit()

        self._persist_event(event)
        return record

    def append_event(
        self,
        case_id: str,
        action_type: CaseActionTypeLiteral,
        summary: str,
        actor: str = "analista",
        new_status: Optional[CaseStatusLiteral] = None,
        priority: Optional[InspectionPriorityLiteral] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CaseRecord:
        """Agrega un evento auditable al historial del caso y actualiza su estado consolidado."""
        if not summary.strip():
            raise ValueError("summary no puede estar vacío al registrar un evento de expediente.")

        current_case, _ = self.get_case(case_id)
        now = _utc_now_iso()
        resolved_status: CaseStatusLiteral = new_status if new_status is not None else current_case.status
        resolved_priority: InspectionPriorityLiteral = (
            priority if priority is not None else current_case.priority
        )
        event = CaseEventRecord(
            event_id=f"EVT-{uuid4().hex[:12]}",
            case_id=current_case.case_id,
            candidate_uid=current_case.candidate_uid,
            anomaly_id=current_case.anomaly_id,
            timestamp_utc=now,
            action_type=action_type,
            actor=actor,
            previous_status=current_case.status,
            new_status=resolved_status,
            summary=summary.strip(),
            metadata=metadata or {},
            human_review_required=True,
        )
        self._persist_event(event)

        updated_count = current_case.events_count + 1
        with sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                UPDATE cases
                SET status = ?, priority = ?, updated_at_utc = ?, events_count = ?, latest_summary = ?
                WHERE case_id = ?
                """,
                (
                    resolved_status,
                    resolved_priority,
                    now,
                    updated_count,
                    summary.strip(),
                    current_case.case_id,
                ),
            )
            conn.commit()

        return current_case.model_copy(
            update={
                "status": resolved_status,
                "priority": resolved_priority,
                "updated_at_utc": now,
                "events_count": updated_count,
                "latest_summary": summary.strip(),
                "human_review_required": True,
            }
        )

    def get_case(self, case_id: str) -> Tuple[CaseRecord, List[CaseEventRecord]]:
        """Recupera el estado consolidado actual y la bitácora ordenada de eventos de un caso."""
        with sqlite3.connect(self.sqlite_path) as conn:
            case_row = conn.execute(
                """
                SELECT case_id, candidate_uid, anomaly_id, status, priority, crs_epsg,
                       centroid_lon, centroid_lat, area_m2, created_at_utc, updated_at_utc,
                       events_count, latest_summary, human_review_required
                FROM cases WHERE case_id = ? OR candidate_uid = ?
                """,
                (case_id, case_id),
            ).fetchone()
            if case_row is None:
                raise KeyError(f"No existe expediente registrado con identificador '{case_id}'.")

            case_rec = self._row_to_case(case_row)
            event_rows = conn.execute(
                """
                SELECT event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                       action_type, actor, previous_status, new_status, summary,
                       metadata_json, human_review_required
                FROM case_events
                WHERE case_id = ?
                ORDER BY timestamp_utc ASC, rowid ASC
                """,
                (case_rec.case_id,),
            ).fetchall()

        return case_rec, [self._row_to_event(r) for r in event_rows]

    def list_cases(self, status: Optional[CaseStatusLiteral] = None) -> List[CaseRecord]:
        """Lista todos los expedientes registrados, opcionalmente filtrados por estado."""
        with sqlite3.connect(self.sqlite_path) as conn:
            if status is not None:
                rows = conn.execute(
                    """
                    SELECT case_id, candidate_uid, anomaly_id, status, priority, crs_epsg,
                           centroid_lon, centroid_lat, area_m2, created_at_utc, updated_at_utc,
                           events_count, latest_summary, human_review_required
                    FROM cases WHERE status = ?
                    ORDER BY updated_at_utc DESC, case_id ASC
                    """,
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT case_id, candidate_uid, anomaly_id, status, priority, crs_epsg,
                           centroid_lon, centroid_lat, area_m2, created_at_utc, updated_at_utc,
                           events_count, latest_summary, human_review_required
                    FROM cases
                    ORDER BY updated_at_utc DESC, case_id ASC
                    """
                ).fetchall()
        return [self._row_to_case(r) for r in rows]
