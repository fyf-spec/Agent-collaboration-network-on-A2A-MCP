"""Console and JSONL network logging for the local A2A demo."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

from common.config import LOG_FILE


_LOG_LOCK = threading.Lock()


def log_network_event(
    *,
    event: str,
    direction: str,
    source: str,
    target: str,
    method: str | None = None,
    url: str | None = None,
    task_id: str | None = None,
    payload: Any = None,
    status_code: int | None = None,
    elapsed_ms: float | None = None,
    error: str | None = None,
    log_file: Path = LOG_FILE,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "direction": direction,
        "source": source,
        "target": target,
    }
    if method:
        record["method"] = method
    if url:
        record["url"] = url
    if task_id:
        record["task_id"] = task_id
    if payload is not None:
        record["payload"] = payload
    if status_code is not None:
        record["status_code"] = status_code
    if elapsed_ms is not None:
        record["elapsed_ms"] = round(elapsed_ms, 2)
    if error:
        record["error"] = error

    line = json.dumps(record, ensure_ascii=False, default=str)
    with _LOG_LOCK:
        print(_format_console_line(record), flush=True)
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print(f"[logger] failed to write {log_file}: {exc}", flush=True)
    return record


def _format_console_line(record: dict[str, Any]) -> str:
    status = f" status={record['status_code']}" if "status_code" in record else ""
    elapsed = f" elapsed={record['elapsed_ms']}ms" if "elapsed_ms" in record else ""
    task = f" task={record['task_id']}" if "task_id" in record else ""
    url = f" {record['method']} {record['url']}" if record.get("method") and record.get("url") else ""
    error = f" error={record['error']}" if record.get("error") else ""
    payload = ""
    if "payload" in record:
        payload = " payload=" + json.dumps(record["payload"], ensure_ascii=False, default=str)
    return (
        f"[{record['ts']}] {record['event']} {record['direction']} "
        f"{record['source']} -> {record['target']}{url}{task}{status}{elapsed}{error}{payload}"
    )
