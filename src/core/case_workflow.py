"""Gestión auditable de expedientes con SQLite transaccional (fuente de verdad) y exportación JSONL."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union
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

_GENESIS_HASH = "GENESIS"


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


def _compute_canonical_event_hash(event: CaseEventRecord, prev_hash: str) -> str:
    """Calcula SHA-256 canónico del evento encadenado con `prev_hash`."""
    payload = {
        "event_id": event.event_id,
        "case_id": event.case_id,
        "candidate_uid": event.candidate_uid,
        "anomaly_id": event.anomaly_id,
        "timestamp_utc": event.timestamp_utc,
        "action_type": event.action_type,
        "actor": event.actor,
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "summary": event.summary,
        "metadata": event.metadata,
        "idempotency_key": event.idempotency_key,
        "prev_event_hash": prev_hash,
        "human_review_required": event.human_review_required,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    """Escribe un archivo completo en temporal + fsync y lo publica atómicamente con os.replace."""
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid4().hex[:6]}")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


class CaseWorkflowManager:
    """Administrador de expedientes con SQLite append-only transaccional y exportación JSONL.

    Decisiones de integridad (Fase 3.1):
    - **Fuente de verdad transaccional:** `cases_registry.sqlite` con transacciones `BEGIN IMMEDIATE`,
      bloqueo por archivo (`cases.lock` vía `fcntl.flock`) y triggers append-only sobre `case_events`.
    - **Exportación reproducible:** `case_events.jsonl` y `case_events_checkpoint.json` se regeneran de
      forma atómica desde SQLite tras cada commit exitoso. Si una caída interrumpe la exportación JSONL,
      el estado SQLite permanece íntegro y `sync_jsonl_from_sqlite()` resincroniza la bitácora exportada.
    - **Limitación criptográfica explícita:** La cadena SHA-256 (`prev_event_hash -> event_hash`) detecta
      alteraciones incidentales o parciales, pero un actor con acceso de escritura total podría recalcular
      toda la cadena; para garantía fuerte se debe contrastar contra `expected_head_hash` o un
      checkpoint externo firmado.
    """

    def __init__(self, storage_dir: Optional[Union[str, Path]] = None) -> None:
        base_dir = Path(storage_dir) if storage_dir is not None else (settings.reports_dir / "cases")
        base_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir: Path = base_dir
        self.sqlite_path: Path = base_dir / "cases_registry.sqlite"
        self.jsonl_path: Path = base_dir / "case_events.jsonl"
        self.checkpoint_path: Path = base_dir / "case_events_checkpoint.json"
        self.lock_path: Path = base_dir / ".cases.lock"
        sqlite_existed = self.sqlite_path.exists()
        self._init_storage()
        if (
            not sqlite_existed
            and self.jsonl_path.exists()
            and self.jsonl_path.read_text(encoding="utf-8").strip()
        ):
            self.rebuild_sqlite_from_jsonl()
        else:
            self.sync_jsonl_from_sqlite()

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Serializa accesos concurrentes entre hilos y procesos mediante flock exclusivo."""
        with self.lock_path.open("a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path, timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _create_schema_on_conn(self, conn: sqlite3.Connection) -> None:
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
                event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                candidate_uid TEXT NOT NULL,
                anomaly_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                action_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                idempotency_key TEXT,
                prev_event_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                human_review_required INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_case_events_idempotency
            ON case_events(case_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_case_events_no_update
            BEFORE UPDATE ON case_events
            BEGIN
                SELECT RAISE(ABORT, 'case_events is append-only');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_case_events_no_delete
            BEFORE DELETE ON case_events
            BEGIN
                SELECT RAISE(ABORT, 'case_events is append-only');
            END
            """
        )

    def _init_storage(self) -> None:
        with self._file_lock():
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._create_schema_on_conn(conn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

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
            idempotency_key=str(row[11]) if row[11] is not None else None,
            prev_event_hash=str(row[12]) if row[12] is not None else None,
            event_hash=str(row[13]) if row[13] is not None else None,
            human_review_required=bool(row[14]),
        )

    def _get_head_event_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT event_hash FROM case_events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row is not None else _GENESIS_HASH

    def _export_jsonl_unlocked(self, conn: sqlite3.Connection) -> None:
        """Exporta atómicamente `case_events.jsonl` y checkpoint desde SQLite."""
        rows = conn.execute(
            """
            SELECT event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                   action_type, actor, previous_status, new_status, summary,
                   metadata_json, idempotency_key, prev_event_hash, event_hash,
                   human_review_required
            FROM case_events
            ORDER BY event_seq ASC
            """
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        lines = [json.dumps(evt.model_dump(), ensure_ascii=False) for evt in events]
        content = ("\n".join(lines) + "\n") if lines else ""
        _atomic_write_text(self.jsonl_path, content)

        head_hash = events[-1].event_hash if events else _GENESIS_HASH
        case_rows = conn.execute(
            "SELECT case_id, priority, crs_epsg, centroid_lon, centroid_lat, area_m2 FROM cases"
        ).fetchall()
        cases_snapshot = {
            str(r[0]): {
                "priority": str(r[1]),
                "crs_epsg": int(r[2]),
                "centroid_lon": float(r[3]),
                "centroid_lat": float(r[4]),
                "area_m2": float(r[5]),
            }
            for r in case_rows
        }
        checkpoint_payload = {
            "schema_version": "case_workflow.v2",
            "events_count": len(events),
            "head_event_hash": head_hash,
            "exported_at_utc": _utc_now_iso(),
            "source_of_truth": "sqlite",
            "cases_snapshot": cases_snapshot,
        }
        _atomic_write_text(
            self.checkpoint_path,
            json.dumps(checkpoint_payload, indent=2, ensure_ascii=False),
        )

    def sync_jsonl_from_sqlite(self) -> int:
        """Regenera `case_events.jsonl` desde SQLite si falta o quedó desfasado tras una caída."""
        with self._file_lock():
            conn = self._connect()
            try:
                sql_count_row = conn.execute("SELECT COUNT(*) FROM case_events").fetchone()
                sql_count = int(sql_count_row[0]) if sql_count_row else 0
                sql_head = self._get_head_event_hash(conn)

                existing_lines: List[str] = []
                if self.jsonl_path.exists():
                    existing_lines = [
                        line for line in self.jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()
                    ]
                if sql_count == 0 and existing_lines:
                    raise RuntimeError(
                        "La base SQLite está vacía mientras existe una bitácora JSONL con eventos; "
                        "no se sobrescribirá JSONL vacío. Invoque rebuild_sqlite_from_jsonl() para recuperar."
                    )

                needs_sync = not self.jsonl_path.exists() or not self.checkpoint_path.exists()
                if not needs_sync:
                    if len(existing_lines) != sql_count:
                        needs_sync = True
                    elif existing_lines:
                        last_parsed = json.loads(existing_lines[-1])
                        if last_parsed.get("event_hash") != sql_head:
                            needs_sync = True

                if needs_sync:
                    self._export_jsonl_unlocked(conn)
                return sql_count
            finally:
                conn.close()

    def open_or_get_case(
        self,
        anomaly: Union[ExtractionAnomalyFeature, Dict[str, Any]],
        actor: str = "rio-alerta-mcp",
        summary: str = "Apertura inicial de expediente de monitoreo.",
        initial_status: CaseStatusLiteral = "PENDIENTE_VERIFICACION_TERRENO_Y_CATASTRO",
        priority: InspectionPriorityLiteral = "MEDIA_VERIFICACION",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CaseRecord:
        """Abre un caso nuevo con evento `APERTURA_CASO` o recupera el existente atómicamente."""
        feat = coerce_anomaly_feature(anomaly)
        candidate_uid = _derive_candidate_uid(feat)

        with self._file_lock():
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
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
                    conn.execute("COMMIT")
                    return self._row_to_case(existing)

                now = _utc_now_iso()
                case_id = _derive_case_id(candidate_uid)
                event_id = f"EVT-{uuid4().hex[:12]}"
                prev_hash = self._get_head_event_hash(conn)

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
                draft_event = CaseEventRecord(
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
                    idempotency_key=f"open:{candidate_uid}",
                    prev_event_hash=prev_hash,
                    event_hash=None,
                    human_review_required=True,
                )
                ev_hash = _compute_canonical_event_hash(draft_event, prev_hash)

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
                conn.execute(
                    """
                    INSERT INTO case_events (
                        event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                        action_type, actor, previous_status, new_status, summary,
                        metadata_json, idempotency_key, prev_event_hash, event_hash,
                        human_review_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft_event.event_id,
                        draft_event.case_id,
                        draft_event.candidate_uid,
                        draft_event.anomaly_id,
                        draft_event.timestamp_utc,
                        draft_event.action_type,
                        draft_event.actor,
                        draft_event.previous_status,
                        draft_event.new_status,
                        draft_event.summary,
                        json.dumps(draft_event.metadata, ensure_ascii=False),
                        draft_event.idempotency_key,
                        prev_hash,
                        ev_hash,
                        1,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                conn.close()
                raise

            try:
                self._export_jsonl_unlocked(conn)
            finally:
                conn.close()
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
        idempotency_key: Optional[str] = None,
    ) -> CaseRecord:
        """Agrega un evento encadenado SHA-256 en SQLite de forma atómica e idempotente."""
        cleaned_summary = summary.strip()
        if not cleaned_summary:
            raise ValueError("summary no puede estar vacío al registrar un evento de expediente.")

        cleaned_idem = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else None

        with self._file_lock():
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
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

                current_case = self._row_to_case(case_row)

                if cleaned_idem is not None:
                    dup = conn.execute(
                        "SELECT 1 FROM case_events WHERE case_id = ? AND idempotency_key = ?",
                        (current_case.case_id, cleaned_idem),
                    ).fetchone()
                    if dup is not None:
                        conn.execute("COMMIT")
                        return current_case

                now = _utc_now_iso()
                resolved_status: CaseStatusLiteral = (
                    new_status if new_status is not None else current_case.status
                )
                resolved_priority: InspectionPriorityLiteral = (
                    priority if priority is not None else current_case.priority
                )
                prev_hash = self._get_head_event_hash(conn)
                draft_event = CaseEventRecord(
                    event_id=f"EVT-{uuid4().hex[:12]}",
                    case_id=current_case.case_id,
                    candidate_uid=current_case.candidate_uid,
                    anomaly_id=current_case.anomaly_id,
                    timestamp_utc=now,
                    action_type=action_type,
                    actor=actor,
                    previous_status=current_case.status,
                    new_status=resolved_status,
                    summary=cleaned_summary,
                    metadata=metadata or {},
                    idempotency_key=cleaned_idem,
                    prev_event_hash=prev_hash,
                    event_hash=None,
                    human_review_required=True,
                )
                ev_hash = _compute_canonical_event_hash(draft_event, prev_hash)

                conn.execute(
                    """
                    INSERT INTO case_events (
                        event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                        action_type, actor, previous_status, new_status, summary,
                        metadata_json, idempotency_key, prev_event_hash, event_hash,
                        human_review_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft_event.event_id,
                        draft_event.case_id,
                        draft_event.candidate_uid,
                        draft_event.anomaly_id,
                        draft_event.timestamp_utc,
                        draft_event.action_type,
                        draft_event.actor,
                        draft_event.previous_status,
                        draft_event.new_status,
                        draft_event.summary,
                        json.dumps(draft_event.metadata, ensure_ascii=False),
                        cleaned_idem,
                        prev_hash,
                        ev_hash,
                        1,
                    ),
                )

                updated_count = current_case.events_count + 1
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
                        cleaned_summary,
                        current_case.case_id,
                    ),
                )
                conn.execute("COMMIT")
                updated_record = current_case.model_copy(
                    update={
                        "status": resolved_status,
                        "priority": resolved_priority,
                        "updated_at_utc": now,
                        "events_count": updated_count,
                        "latest_summary": cleaned_summary,
                        "human_review_required": True,
                    }
                )
            except Exception:
                conn.execute("ROLLBACK")
                conn.close()
                raise

            try:
                self._export_jsonl_unlocked(conn)
            finally:
                conn.close()
            return updated_record

    def _verify_events_chain(
        self,
        events: List[CaseEventRecord],
        label: str,
        expected_head_hash: Optional[str] = None,
    ) -> str:
        prev = _GENESIS_HASH
        for idx, evt in enumerate(events):
            if evt.prev_event_hash != prev:
                raise ValueError(
                    f"Alteración detectada en cadena de hashes ({label}) en evento #{idx} "
                    f"('{evt.event_id}'): prev_event_hash='{evt.prev_event_hash}' != '{prev}'."
                )
            recomputed = _compute_canonical_event_hash(evt, prev)
            if evt.event_hash != recomputed:
                raise ValueError(
                    f"Alteración detectada en evento #{idx} ('{evt.event_id}') de {label}: "
                    f"digest '{evt.event_hash}' no coincide con '{recomputed}'."
                )
            prev = recomputed
        if expected_head_hash is not None and prev != expected_head_hash:
            raise ValueError(
                f"Head hash de {label} ('{prev}') no coincide con el checkpoint esperado ('{expected_head_hash}')."
            )
        return prev

    def verify_log_integrity(
        self,
        expected_head_hash: Optional[str] = None,
        check_jsonl_parity: bool = True,
    ) -> Dict[str, Any]:
        """Verifica la cadena SHA-256 en SQLite y su consistencia con `case_events.jsonl`."""
        with self._file_lock():
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                           action_type, actor, previous_status, new_status, summary,
                           metadata_json, idempotency_key, prev_event_hash, event_hash,
                           human_review_required
                    FROM case_events
                    ORDER BY event_seq ASC
                    """
                ).fetchall()
                sql_events = [self._row_to_event(r) for r in rows]

                # Verificar que no existan casos sin evento de apertura APERTURA_CASO
                case_rows = conn.execute("SELECT case_id FROM cases").fetchall()
                for (cid,) in case_rows:
                    open_evt = conn.execute(
                        "SELECT 1 FROM case_events WHERE case_id = ? AND action_type = 'APERTURA_CASO' LIMIT 1",
                        (cid,),
                    ).fetchone()
                    if open_evt is None:
                        raise ValueError(f"Inconsistencia crítica: el caso '{cid}' carece de evento APERTURA_CASO.")

                sql_head = self._verify_events_chain(
                    sql_events, label="sqlite.case_events", expected_head_hash=expected_head_hash
                )
            finally:
                conn.close()

            jsonl_head = _GENESIS_HASH
            jsonl_count = 0
            if check_jsonl_parity:
                if not self.jsonl_path.exists():
                    raise ValueError(f"Falta archivo exportado JSONL '{self.jsonl_path}'.")
                raw_lines = [
                    line for line in self.jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()
                ]
                jsonl_events = [CaseEventRecord(**json.loads(line)) for line in raw_lines]
                jsonl_count = len(jsonl_events)
                jsonl_head = self._verify_events_chain(
                    jsonl_events, label="case_events.jsonl", expected_head_hash=expected_head_hash
                )
                if jsonl_count != len(sql_events) or jsonl_head != sql_head:
                    raise ValueError(
                        f"Desincronización entre SQLite (count={len(sql_events)}, head={sql_head}) y "
                        f"JSONL (count={jsonl_count}, head={jsonl_head})."
                    )

            return {
                "valid": True,
                "sqlite_events_count": len(sql_events),
                "jsonl_events_count": jsonl_count,
                "head_event_hash": sql_head,
                "tamper_model_note": (
                    "La cadena SHA-256 detecta alteraciones parciales y desincronizaciones; "
                    "para garantía contra reescritura completa del almacenamiento contrastar contra expected_head_hash."
                ),
            }

    def rebuild_sqlite_from_jsonl(self, expected_head_hash: Optional[str] = None) -> int:
        """Reconstruye `cases_registry.sqlite` desde `case_events.jsonl` tras validar la cadena de hashes."""
        with self._file_lock():
            if not self.jsonl_path.exists():
                raise FileNotFoundError(f"No existe bitácora JSONL para reconstrucción: '{self.jsonl_path}'.")
            raw_lines = [
                line for line in self.jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            events = [CaseEventRecord(**json.loads(line)) for line in raw_lines]
            self._verify_events_chain(
                events, label="case_events.jsonl", expected_head_hash=expected_head_hash
            )

            # Verificar que ningún evento posterior aparezca sin apertura previa
            snapshot_by_case: Dict[str, Dict[str, Any]] = {}
            if self.checkpoint_path.exists():
                try:
                    cp = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
                    if isinstance(cp, dict) and isinstance(cp.get("cases_snapshot"), dict):
                        snapshot_by_case = cp["cases_snapshot"]
                except Exception:  # noqa: BLE001
                    snapshot_by_case = {}

            reconstructed_cases: Dict[str, Dict[str, Any]] = {}
            for evt in events:
                if evt.action_type == "APERTURA_CASO":
                    snap = snapshot_by_case.get(evt.case_id, {})
                    reconstructed_cases[evt.case_id] = {
                        "case_id": evt.case_id,
                        "candidate_uid": evt.candidate_uid,
                        "anomaly_id": evt.anomaly_id,
                        "status": evt.new_status,
                        "priority": evt.metadata.get("priority", snap.get("priority", "MEDIA_VERIFICACION")),
                        "crs_epsg": int(evt.metadata.get("crs_epsg", snap.get("crs_epsg", 5347))),
                        "centroid_lon": float(evt.metadata.get("centroid_lon", snap.get("centroid_lon", -60.6))),
                        "centroid_lat": float(evt.metadata.get("centroid_lat", snap.get("centroid_lat", -31.7))),
                        "area_m2": float(evt.metadata.get("area_m2", snap.get("area_m2", 1600.0))),
                        "created_at_utc": evt.timestamp_utc,
                        "updated_at_utc": evt.timestamp_utc,
                        "events_count": 1,
                        "latest_summary": evt.summary,
                    }
                else:
                    if evt.case_id not in reconstructed_cases:
                        raise ValueError(
                            f"No puede reconstruirse '{evt.case_id}': evento '{evt.event_id}' sin APERTURA_CASO previa."
                        )
                    c = reconstructed_cases[evt.case_id]
                    c["status"] = evt.new_status
                    c["updated_at_utc"] = evt.timestamp_utc
                    c["events_count"] = int(c["events_count"]) + 1
                    c["latest_summary"] = evt.summary
                    if "priority" in evt.metadata:
                        c["priority"] = evt.metadata["priority"]

            tmp_db = self.sqlite_path.with_name(f".rebuild_{os.getpid()}_{uuid4().hex[:6]}.sqlite")
            conn = sqlite3.connect(tmp_db, timeout=30.0, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._create_schema_on_conn(conn)
                for c in reconstructed_cases.values():
                    conn.execute(
                        """
                        INSERT INTO cases (
                            case_id, candidate_uid, anomaly_id, status, priority, crs_epsg,
                            centroid_lon, centroid_lat, area_m2, created_at_utc, updated_at_utc,
                            events_count, latest_summary, human_review_required
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c["case_id"],
                            c["candidate_uid"],
                            c["anomaly_id"],
                            c["status"],
                            c["priority"],
                            c["crs_epsg"],
                            c["centroid_lon"],
                            c["centroid_lat"],
                            c["area_m2"],
                            c["created_at_utc"],
                            c["updated_at_utc"],
                            c["events_count"],
                            c["latest_summary"],
                            1,
                        ),
                    )
                for evt in events:
                    conn.execute(
                        """
                        INSERT INTO case_events (
                            event_id, case_id, candidate_uid, anomaly_id, timestamp_utc,
                            action_type, actor, previous_status, new_status, summary,
                            metadata_json, idempotency_key, prev_event_hash, event_hash,
                            human_review_required
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evt.event_id,
                            evt.case_id,
                            evt.candidate_uid,
                            evt.anomaly_id,
                            evt.timestamp_utc,
                            evt.action_type,
                            evt.actor,
                            evt.previous_status,
                            evt.new_status,
                            evt.summary,
                            json.dumps(evt.metadata, ensure_ascii=False),
                            evt.idempotency_key,
                            evt.prev_event_hash,
                            evt.event_hash,
                            1 if evt.human_review_required else 0,
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                conn.close()
                if tmp_db.exists():
                    tmp_db.unlink(missing_ok=True)
                raise
            conn.close()
            os.replace(tmp_db, self.sqlite_path)
            for wal_suffix in ("-wal", "-shm"):
                stale = Path(str(self.sqlite_path) + wal_suffix)
                if stale.exists():
                    stale.unlink(missing_ok=True)
            return len(reconstructed_cases)

    def get_case(self, case_id: str) -> Tuple[CaseRecord, List[CaseEventRecord]]:
        """Recupera el estado consolidado actual y la bitácora ordenada de eventos de un caso."""
        conn = self._connect()
        try:
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
                       metadata_json, idempotency_key, prev_event_hash, event_hash,
                       human_review_required
                FROM case_events
                WHERE case_id = ?
                ORDER BY event_seq ASC
                """,
                (case_rec.case_id,),
            ).fetchall()
            return case_rec, [self._row_to_event(r) for r in event_rows]
        finally:
            conn.close()

    def list_cases(self, status: Optional[CaseStatusLiteral] = None) -> List[CaseRecord]:
        """Lista todos los expedientes registrados, opcionalmente filtrados por estado."""
        conn = self._connect()
        try:
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
        finally:
            conn.close()
