"""Console and JSONL network logging for the local A2A demo."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

from common.config import LOG_FILE


import logging


# 全局logging格式
def setup_system_logger():
    # 配置全局 logging 基本格式与级别
    logging.basicConfig(
        level=logging.INFO,
        format="\033[0m[%(asctime)s]\033[0m \033[36m[%(levelname)s]\033[0m [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z"
    )

setup_system_logger()

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
    payload_size: int | None = None,
    status_code: int | None = None,
    latency_ms: float | None = None,
    elapsed_ms: float | None = None,
    error: str | None = None,
    error_type: str | None = None,
    log_file: Path = LOG_FILE,
) -> dict[str, Any]:
    # 记录网络事件到控制台和 JSONL 日志文件
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
        if payload_size is None:
            try:
                payload_str = json.dumps(payload, ensure_ascii=False, default=str)
                payload_size = len(payload_str.encode("utf-8"))
            except (TypeError, ValueError):
                print(f"failed to calculate payload size for event {event} with payload: {payload}", flush=True)
                pass
    if payload_size is not None:
        record["payload_size"] = payload_size
    if status_code is not None:
        record["status_code"] = status_code

    final_elapsed = latency_ms if latency_ms is not None else elapsed_ms
    if final_elapsed is not None:
        record["elapsed_ms"] = round(final_elapsed, 2)

    if error:
        record["error"] = error
    if error_type:
        record["error_type"] = error_type

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
    # 将日志记录格式化为带 ANSI 颜色的控制台输出行
    RESET = "\033[0m"
    DIM = "\033[2m" # 灰色
    CYAN = "\033[36m" # 青色
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    ts = record.get("ts", "")
    event = record.get("event", "?")
    direction = record.get("direction", "")
    source = record.get("source", "")
    target = record.get("target", "")

    payload_size = record.get("payload_size", "N/A")
    latency = f"{record['elapsed_ms']} ms" if "elapsed_ms" in record else "N/A"

    status_code = record.get("status_code", "N/A")
    if isinstance(status_code, int):
        status_color = GREEN if 200 <= status_code < 300 else YELLOW if 300 <= status_code < 400 else RED
    else:
        status_color = YELLOW if status_code == "N/A" else RED

    error = record.get("error")
    err_type = record.get("error_type")
    err_str = ""
    if err_type:
        err_str += f" | ErrType: {err_type}"
    if error:
        err_str += f" | Err: {RED}{error}{DIM}"

    source_color = RED if error or err_type or (isinstance(status_code, int) and status_code >= 400) or record.get("payload", {}).get("status") == "error" else GREEN

    ui_block = (
        f"{DIM}[Size: {payload_size}{' bytes' if payload_size != 'N/A' else ''} | "
        f"Latency: {latency} | "
        f"Status: {status_color}{status_code}{DIM}{err_str}]{RESET}"
    )

    task_info = f" {CYAN}task={record['task_id']}{RESET}" if "task_id" in record else ""
    url_info = f" {BLUE}{record['method']} {record['url']}{RESET}" if record.get("method") and record.get("url") else ""

    payload_str = ""
    if "payload" in record:
        raw_payload = json.dumps(record["payload"], ensure_ascii=False, default=str)
        if len(raw_payload) > 500:
            raw_payload = raw_payload[:500] + "..."
        payload_str = f"\n  {DIM}payload={raw_payload}{RESET}"

    return (
        f"{RESET}[{ts}]{RESET} {CYAN}{event}{RESET} {YELLOW}{direction}{RESET} "
        f"{source_color}{source} -> {target}{RESET}{url_info}{task_info} "
        f"{ui_block}{payload_str}"
    )
