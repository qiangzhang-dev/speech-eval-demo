"""Small, offline-only HTTP API and dashboard for evaluation results.

The server intentionally depends on the Python standard library only.  A new
repository connection is opened for every request because ``ThreadingHTTPServer``
dispatches requests on different threads and SQLite connections are
thread-affine by default.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .models import EvaluationResult
from .repository import EvaluationRepository
from .revisions import (
    EDITABLE_FIELDS,
    LABEL_FIELD_ALIASES,
    LABEL_FIELDS,
    apply_human_revision,
    apply_label_correction,
)

MAX_REQUEST_BODY = 1_048_576
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _metadata(result: EvaluationResult) -> dict[str, Any]:
    """Return persistence metadata without changing the 14-field public result."""

    return {
        "run_id": result.run_id,
        "batch_id": result.batch_id,
        "status": result.status,
        "created_at": result.created_at,
        "metric_version": result.metric_version,
        "prompt_version": result.prompt_version,
        "subscene_type": result.subscene_type,
    }


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _page_value(
    query: dict[str, list[str]],
    name: str,
    default: int,
    *,
    maximum: int | None = None,
) -> int:
    raw = _first(query, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0 or (name == "limit" and value == 0):
        raise ValueError(f"{name} is out of range")
    if maximum is not None:
        value = min(value, maximum)
    return value


class SpeechEvalHTTPServer(ThreadingHTTPServer):
    """Threaded local server carrying only immutable connection settings."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        database_path: Path,
        static_dir: Path,
    ) -> None:
        self.database_path = database_path
        self.static_dir = static_dir
        super().__init__(server_address, SpeechEvalRequestHandler)


class SpeechEvalRequestHandler(BaseHTTPRequestHandler):
    """Serve the JSON API and local dashboard assets."""

    server: SpeechEvalHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        # Keep library/test use quiet. Integrators can subclass to add logging.
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {"status": "ok", "service": "speech-eval-demo", "offline": True},
                )
            elif parsed.path == "/api/stats":
                self._get_stats()
            elif parsed.path == "/api/results":
                self._get_results(query)
            elif parsed.path in {"/api/export", "/api/download", "/api/results/download"}:
                self._download_results(query)
            elif parsed.path.startswith("/api/results/"):
                result_path = parsed.path[len("/api/results/") :]
                is_download = result_path.endswith("/download")
                if is_download:
                    result_path = result_path[: -len("/download")]
                sample_id = unquote(result_path).strip()
                if not sample_id or "/" in sample_id or "\\" in sample_id:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "Result was not found")
                elif is_download:
                    self._download_result(sample_id, query)
                else:
                    self._get_result(sample_id, _first(query, "run_id"))
            elif parsed.path == "/api/logs":
                self._get_logs(query)
            elif parsed.path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "not_found", "API endpoint was not found")
            else:
                self._serve_static(parsed.path)
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The request could not be completed",
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            path = urlsplit(self.path).path
            if path == "/api/revisions":
                self._post_revision()
            elif path == "/api/label-corrections":
                self._post_label_correction()
            elif path in {
                "/api/health",
                "/api/stats",
                "/api/results",
                "/api/logs",
                "/api/export",
                "/api/download",
                "/api/results/download",
                "/api/label-corrections",
            } or path.startswith("/api/results/"):
                self._method_not_allowed("GET")
            elif path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "not_found", "API endpoint was not found")
            else:
                self._method_not_allowed("GET")
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except _ResponseSent:
            return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The request could not be completed",
            )

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._method_not_allowed("GET, POST")

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def _repository(self) -> EvaluationRepository:
        return EvaluationRepository(self.server.database_path)

    def _get_stats(self) -> None:
        with self._repository() as repository:
            results = repository.list_results()
            status_counts = Counter(result.status for result in results)
            scene_counts = Counter(result.scene_type for result in results)
            batch_counts = Counter(result.batch_id for result in results)
            connection = repository.connection
            total_batches = int(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0])
            total_runs = int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
            total_revisions = int(connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0])
            total_logs = int(connection.execute("SELECT COUNT(*) FROM logs").fetchone()[0])
        self._json(
            HTTPStatus.OK,
            {
                "total_results": len(results),
                "status_counts": dict(sorted(status_counts.items())),
                "scene_counts": dict(sorted(scene_counts.items())),
                "batch_counts": dict(sorted(batch_counts.items())),
                "total_batches": total_batches,
                "total_runs": total_runs,
                "total_revisions": total_revisions,
                "total_logs": total_logs,
            },
        )

    def _get_results(self, query: dict[str, list[str]]) -> None:
        limit = _page_value(query, "limit", DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE)
        offset = _page_value(query, "offset", 0)
        results = self._filtered_results(query)

        total = len(results)
        items = [
            result.to_dict(chinese_fields=True) for result in results[offset : offset + limit]
        ]
        self._json(
            HTTPStatus.OK,
            {"items": items, "total": total, "limit": limit, "offset": offset},
        )

    def _filtered_results(self, query: dict[str, list[str]]) -> list[EvaluationResult]:
        batch_id = _first(query, "batch_id")
        run_id = _first(query, "run_id")
        status = _first(query, "status")
        scene_type = _first(query, "scene_type")
        task_type = _first(query, "task_type")
        conclusion = _first(query, "conclusion")
        search = _first(query, "q")

        with self._repository() as repository:
            results = repository.list_results(batch_id=batch_id, run_id=run_id, status=status)

        if scene_type:
            results = [result for result in results if result.scene_type == scene_type]
        if task_type:
            results = [result for result in results if task_type in result.task_types]
        if conclusion:
            results = [result for result in results if self._conclusion_level(result) == conclusion]
        if search:
            needle = search.casefold()
            results = [
                result
                for result in results
                if needle
                in json.dumps(
                    result.to_dict(chinese_fields=True),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).casefold()
            ]

        return results

    def _download_results(self, query: dict[str, list[str]]) -> None:
        results = self._filtered_results(query)
        self._send_export(results, _first(query, "format") or "json", "evaluation-results")

    def _download_result(self, sample_id: str, query: dict[str, list[str]]) -> None:
        run_id = _first(query, "run_id")
        with self._repository() as repository:
            result = repository.get_result(sample_id, run_id=run_id)
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Result was not found")
            return
        safe_stem = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in sample_id
        ).strip("-") or "result"
        self._send_export([result], _first(query, "format") or "json", safe_stem)

    def _send_export(
        self,
        results: list[EvaluationResult],
        export_format: str,
        filename_stem: str,
    ) -> None:
        normalized_format = export_format.casefold()
        fields = list(EvaluationResult.FIELD_MAP.values())
        items = [result.to_dict(chinese_fields=True) for result in results]
        if normalized_format == "json":
            body = json.dumps(
                {"count": len(items), "items": items},
                ensure_ascii=False,
                indent=2,
                default=str,
            ).encode("utf-8")
            content_type = "application/json; charset=utf-8"
            extension = "json"
        elif normalized_format == "csv":
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                writer.writerow(
                    {
                        field: (
                            value
                            if isinstance(value, str)
                            else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
                        )
                        for field, value in item.items()
                    }
                )
            # UTF-8 BOM lets spreadsheet applications recognize Chinese reliably.
            body = ("\ufeff" + stream.getvalue()).encode("utf-8")
            content_type = "text/csv; charset=utf-8"
            extension = "csv"
        else:
            raise ValueError("format must be json or csv")
        self._bytes(
            HTTPStatus.OK,
            body,
            content_type,
            {"Content-Disposition": f'attachment; filename="{filename_stem}.{extension}"'},
        )

    @staticmethod
    def _conclusion_level(result: EvaluationResult) -> str:
        if isinstance(result.final_conclusion, dict):
            return str(result.final_conclusion.get("level", ""))
        return str(result.final_conclusion)

    def _get_result(self, sample_id: str, run_id: str | None) -> None:
        with self._repository() as repository:
            result = repository.get_result(sample_id, run_id=run_id)
            if result is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Result was not found")
                return
            revisions = [revision.to_dict() for revision in repository.list_revisions(sample_id)]
            logs = repository.list_logs(sample_id=sample_id, run_id=result.run_id)
        self._json(
            HTTPStatus.OK,
            {
                "result": result.to_dict(chinese_fields=True),
                "metadata": _metadata(result),
                "revisions": revisions,
                "logs": logs,
            },
        )

    def _get_logs(self, query: dict[str, list[str]]) -> None:
        limit = _page_value(query, "limit", DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE)
        offset = _page_value(query, "offset", 0)
        sample_id = _first(query, "sample_id")
        run_id = _first(query, "run_id")
        batch_id = _first(query, "batch_id")
        level = _first(query, "level")
        status = _first(query, "status")
        with self._repository() as repository:
            logs = repository.list_logs(sample_id=sample_id, run_id=run_id)
        if batch_id:
            logs = [entry for entry in logs if entry.get("batch_id") == batch_id]
        if level:
            logs = [entry for entry in logs if entry.get("level") == level]
        if status:
            logs = [entry for entry in logs if entry.get("status") == status]
        total = len(logs)
        self._json(
            HTTPStatus.OK,
            {"items": logs[offset : offset + limit], "total": total, "limit": limit, "offset": offset},
        )

    def _post_revision(self) -> None:
        payload = self._read_json_body()
        sample_id = self._required_text(payload, "sample_id")
        editor = self._required_text(payload, "editor")
        reason = self._required_text(payload, "reason")
        run_id_value = payload.get("run_id")
        if run_id_value is not None and not isinstance(run_id_value, str):
            raise ValueError("run_id must be a string")
        run_id = run_id_value.strip() if isinstance(run_id_value, str) else None
        run_id = run_id or None

        raw_changes = payload.get("changes")
        if not isinstance(raw_changes, dict) or not raw_changes:
            raise ValueError("changes must be a non-empty object")
        reverse_fields = {value: key for key, value in EvaluationResult.FIELD_MAP.items()}
        changes: dict[str, Any] = {}
        for supplied_name, value in raw_changes.items():
            if not isinstance(supplied_name, str):
                raise ValueError("change field names must be strings")
            field_name = reverse_fields.get(supplied_name, supplied_name)
            if field_name not in EDITABLE_FIELDS:
                raise ValueError(f"field is not editable: {supplied_name}")
            if field_name in changes:
                raise ValueError(f"field was supplied more than once: {supplied_name}")
            changes[field_name] = value

        with self._repository() as repository:
            result = repository.get_result(sample_id, run_id=run_id)
            if result is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Result was not found")
                return
            updated, revisions = apply_human_revision(
                result,
                changes,
                editor=editor,
                reason=reason,
                run_id=run_id,
                repository=repository,
            )
            if revisions:
                repository.append_log(
                    level="INFO",
                    stage="HUMAN_REVISION",
                    status="COMPLETED",
                    message=f"Applied {len(revisions)} human revision(s)",
                    batch_id=updated.batch_id,
                    sample_id=updated.sample_id,
                    run_id=updated.run_id,
                    version="web-v1",
                )
        self._json(
            HTTPStatus.OK,
            {
                "result": updated.to_dict(chinese_fields=True),
                "metadata": _metadata(updated),
                "revisions": [revision.to_dict() for revision in revisions],
                "revision_count": len(revisions),
            },
        )

    def _post_label_correction(self) -> None:
        """Apply a controlled label correction through a separate audit path."""

        payload = self._read_json_body()
        sample_id = self._required_text(payload, "sample_id")
        editor = self._required_text(payload, "editor")
        reason = self._required_text(payload, "reason")
        run_id_value = payload.get("run_id")
        if run_id_value is not None and not isinstance(run_id_value, str):
            raise ValueError("run_id must be a string")
        run_id = run_id_value.strip() if isinstance(run_id_value, str) else None
        run_id = run_id or None

        raw_changes = payload.get("changes")
        if not isinstance(raw_changes, dict) or not raw_changes:
            raise ValueError("changes must be a non-empty object")
        changes: dict[str, Any] = {}
        for supplied_name, value in raw_changes.items():
            if not isinstance(supplied_name, str):
                raise ValueError("label field names must be strings")
            field_name = LABEL_FIELD_ALIASES.get(supplied_name, supplied_name)
            if field_name not in LABEL_FIELDS:
                raise ValueError(f"label field is not editable: {supplied_name}")
            if field_name in changes:
                raise ValueError(f"label field was supplied more than once: {supplied_name}")
            changes[field_name] = value

        with self._repository() as repository:
            result = repository.get_result(sample_id, run_id=run_id)
            if result is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Result was not found")
                return
            updated, revisions = apply_label_correction(
                result,
                changes,
                editor=editor,
                reason=reason,
                run_id=run_id,
                repository=repository,
            )
            if revisions:
                repository.append_log(
                    level="INFO",
                    stage="LABEL_CORRECTION",
                    status="COMPLETED",
                    message=(
                        f"Applied {len(revisions)} label correction(s); "
                        "derived metrics and conclusions invalidated"
                    ),
                    batch_id=updated.batch_id,
                    sample_id=updated.sample_id,
                    run_id=updated.run_id,
                    version="web-label-v1",
                )
        self._json(
            HTTPStatus.OK,
            {
                "result": updated.to_dict(chinese_fields=True),
                "metadata": _metadata(updated),
                "revisions": [revision.to_dict() for revision in revisions],
                "revision_count": len(revisions),
                "label_correction": True,
                "derived_invalidated": bool(revisions),
            },
        )

    def _read_json_body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length is invalid") from exc
        if length < 0:
            raise ValueError("Content-Length is invalid")
        if length > MAX_REQUEST_BODY:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", "Request body is too large")
            raise _ResponseSent
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    @staticmethod
    def _required_text(payload: dict[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        return value.strip()

    def _serve_static(self, request_path: str) -> None:
        relative_text = unquote(request_path).lstrip("/") or "index.html"
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts or "\\" in relative_text:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Static asset was not found")
            return
        candidate = (self.server.static_dir / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(self.server.static_dir)
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Static asset was not found")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Static asset was not found")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._bytes(HTTPStatus.OK, candidate.read_bytes(), content_type)

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self._bytes(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _method_not_allowed(self, allow: str) -> None:
        # Consume a bounded body before closing the connection; otherwise
        # Windows clients may observe a reset when rejected request bytes remain.
        raw_length = self.headers.get("Content-Length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                length = -1
            if 0 < length <= MAX_REQUEST_BODY:
                self.rfile.read(length)
        self.close_connection = True
        body = json.dumps(
            {"error": {"code": "method_not_allowed", "message": "Method is not allowed"}},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", allow)
        self.send_header("Connection", "close")
        self._common_headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self._common_headers(content_type, len(body))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _common_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )


class _ResponseSent(Exception):
    """Internal control flow used after a complete response has been emitted."""


def create_server(
    database_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 0,
    static_dir: str | Path | None = None,
) -> SpeechEvalHTTPServer:
    """Create a local threaded server without starting its request loop."""

    if str(database_path) == ":memory:":
        raise ValueError("The threaded Web server requires a file-backed SQLite database")
    resolved_database = Path(database_path).expanduser().resolve()
    resolved_static = (
        Path(static_dir).expanduser().resolve()
        if static_dir is not None
        else Path(__file__).resolve().parents[2] / "web"
    )
    return SpeechEvalHTTPServer((host, port), resolved_database, resolved_static.resolve())


def serve(
    database_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    static_dir: str | Path | None = None,
) -> None:
    """Run the dashboard until interrupted by the caller."""

    server = create_server(database_path, host=host, port=port, static_dir=static_dir)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["SpeechEvalHTTPServer", "create_server", "serve"]
