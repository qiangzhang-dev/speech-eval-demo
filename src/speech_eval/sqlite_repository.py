"""SQLite persistence used by the offline pipeline.

This standalone implementation avoids network services and records batches,
runs, samples, terminal results, revisions, and structured event logs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .models import EvaluationResult, EvaluationRun, Revision, Sample, utc_now
from .serialization import result_from_dict


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SQLiteEvaluationRepository:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES batches(batch_id),started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,total INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS samples(batch_id TEXT NOT NULL REFERENCES batches(batch_id),sample_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(batch_id,sample_id));
        CREATE TABLE IF NOT EXISTS results(run_id TEXT NOT NULL REFERENCES runs(run_id),batch_id TEXT NOT NULL REFERENCES batches(batch_id),sample_id TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(run_id,sample_id));
        CREATE TABLE IF NOT EXISTS revisions(revision_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,sample_id TEXT NOT NULL,field_name TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,editor TEXT NOT NULL,edited_at TEXT NOT NULL,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,level TEXT NOT NULL,batch_id TEXT,sample_id TEXT,run_id TEXT,stage TEXT NOT NULL,status TEXT NOT NULL,error_code TEXT,message TEXT NOT NULL,retry_count INTEGER NOT NULL DEFAULT 0,version TEXT);
        """)
        self.connection.commit()

    def close(self) -> None: self.connection.close()
    def __enter__(self) -> "SQLiteEvaluationRepository": return self
    def __exit__(self, *_: Any) -> None: self.close()

    def create_batch(self, batch_id: str | None = None) -> str:
        batch_id = batch_id or f"batch-{uuid.uuid4().hex[:12]}"
        self.connection.execute("INSERT INTO batches VALUES (?,?,?)", (batch_id, utc_now(), "CREATED")); self.connection.commit(); return batch_id

    def ensure_batch(self, batch_id: str) -> str:
        return batch_id if self.connection.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)).fetchone() else self.create_batch(batch_id)

    def create_run(self, batch_id: str, *, run_id: str | None = None, total: int = 0) -> EvaluationRun:
        self.ensure_batch(batch_id); run = EvaluationRun(run_id or f"run-{uuid.uuid4().hex[:12]}", batch_id, utc_now(), total=total)
        self.connection.execute("INSERT INTO runs(run_id,batch_id,started_at,status,total) VALUES (?,?,?,?,?)", (run.run_id, batch_id, run.started_at, run.status, total)); self.connection.commit(); return run

    def update_run(self, run: EvaluationRun) -> None:
        self.connection.execute("UPDATE runs SET finished_at=?,status=?,total=?,completed=?,failed=? WHERE run_id=?", (run.finished_at, run.status, run.total, run.completed, run.failed, run.run_id)); self.connection.execute("UPDATE batches SET status=? WHERE batch_id=?", (run.status, run.batch_id)); self.connection.commit()

    def upsert_sample(self, batch_id: str, sample: Sample) -> None:
        self.ensure_batch(batch_id); self.connection.execute("INSERT OR REPLACE INTO samples VALUES (?,?,?,?)", (batch_id, sample.sample_id, _json(sample.to_dict()), utc_now())); self.connection.commit()

    def save_result(self, result: EvaluationResult) -> None:
        self.connection.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)", (result.run_id, result.batch_id, result.sample_id, result.status, _json(result.to_dict(chinese_fields=False)), result.created_at)); self.connection.commit()

    def get_result(self, sample_id: str, *, run_id: str | None = None) -> EvaluationResult | None:
        row = self.connection.execute("SELECT payload_json FROM results WHERE sample_id=? AND run_id=?" if run_id else "SELECT payload_json FROM results WHERE sample_id=? ORDER BY created_at DESC LIMIT 1", (sample_id, run_id) if run_id else (sample_id,)).fetchone(); return result_from_dict(json.loads(row[0])) if row else None

    def list_results(self, *, batch_id: str | None = None, run_id: str | None = None, status: str | None = None) -> list[EvaluationResult]:
        clauses: list[str] = []; args: list[str] = []
        for key, value in (("batch_id", batch_id), ("run_id", run_id), ("status", status)):
            if value: clauses.append(f"{key}=?"); args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return [result_from_dict(json.loads(row[0])) for row in self.connection.execute(f"SELECT payload_json FROM results{where} ORDER BY sample_id", args).fetchall()]

    def count_results(self, *, batch_id: str | None = None, run_id: str | None = None) -> int:
        clauses: list[str] = []; args: list[str] = []
        for key, value in (("batch_id", batch_id), ("run_id", run_id)):
            if value: clauses.append(f"{key}=?"); args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""; return int(self.connection.execute(f"SELECT COUNT(*) FROM results{where}", args).fetchone()[0])

    def record_revision(self, revision: Revision) -> None:
        self.connection.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?)", (revision.revision_id, getattr(revision, "run_id", ""), revision.sample_id, revision.field_name, _json(revision.before), _json(revision.after), revision.editor, revision.edited_at, revision.reason)); self.connection.commit()

    def list_revisions(self, sample_id: str | None = None) -> list[Revision]:
        rows = self.connection.execute(
            "SELECT * FROM revisions WHERE sample_id=? ORDER BY edited_at"
            if sample_id
            else "SELECT * FROM revisions ORDER BY edited_at",
            (sample_id,) if sample_id else (),
        ).fetchall()
        return [
            Revision(
                revision_id=row["revision_id"],
                sample_id=row["sample_id"],
                field_name=row["field_name"],
                before=json.loads(row["before_json"]),
                after=json.loads(row["after_json"]),
                editor=row["editor"],
                edited_at=row["edited_at"],
                reason=row["reason"],
                run_id=row["run_id"],
            )
            for row in rows
        ]

    def append_log(self, *, level: str, stage: str, status: str, message: str, batch_id: str | None = None, sample_id: str | None = None, run_id: str | None = None, error_code: str | None = None, retry_count: int = 0, version: str | None = None) -> None:
        self.connection.execute("INSERT INTO logs(timestamp,level,batch_id,sample_id,run_id,stage,status,error_code,message,retry_count,version) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (utc_now(), level, batch_id, sample_id, run_id, stage, status, error_code, message, retry_count, version)); self.connection.commit()

    def list_logs(self, *, sample_id: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []; args: list[str] = []
        for key, value in (("sample_id", sample_id), ("run_id", run_id)):
            if value: clauses.append(f"{key}=?"); args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""; return [dict(row) for row in self.connection.execute(f"SELECT * FROM logs{where} ORDER BY id", args).fetchall()]


EvaluationRepository = SQLiteEvaluationRepository
