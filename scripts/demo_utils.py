from __future__ import annotations

import json
import time
from typing import Any
from urllib import request

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.http_client import HttpJsonClientError, post_json


TERMINAL_TASK_STATES = {"completed", "failed", "partial"}


def submit_task_and_wait(question: str, *, timeout: float) -> dict[str, Any]:
    url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
    response = post_json(url, {"question": question, "timeout": timeout, "async": True}, timeout=timeout + 5.0)
    if not response.ok or not isinstance(response.data, dict):
        raise RuntimeError(f"submit_task failed: HTTP {response.status_code} {response.data}")
    task = response.data.get("task", {})
    task_id = task.get("task_id")
    if not task_id:
        raise RuntimeError(f"submit_task response did not include task_id: {response.data}")

    deadline = time.monotonic() + timeout + 5.0
    last_task = task if isinstance(task, dict) else {}
    while time.monotonic() < deadline:
        last_task = get_task(task_id)
        if last_task.get("status") in TERMINAL_TASK_STATES:
            return last_task
        time.sleep(0.5)
    raise TimeoutError(f"task {task_id} did not finish within {timeout:g}s; last status={last_task.get('status')}")


def get_task(task_id: str) -> dict[str, Any]:
    url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/tasks?task_id={task_id}"
    http_request = request.Request(url, method="GET", headers={"Accept": "application/json"})
    with request.urlopen(http_request, timeout=5.0) as response:
        data = json.loads(response.read().decode("utf-8"))
    task = data.get("task", {}) if isinstance(data, dict) else {}
    if not isinstance(task, dict):
        raise ValueError(f"invalid task response: {data}")
    return task


def print_task_summary(task: dict[str, Any]) -> None:
    print(f"\nTask Status: {task.get('status')}")
    print("\nFinal Answer:\n")
    print(task.get("final_answer", ""))

    print("\nAnswers of Agents:")
    results = task.get("results", {})
    errors = task.get("dispatch_errors", {})

    if isinstance(results, dict):
        for agent, result in results.items():
            if not isinstance(result, dict):
                print(f"- {agent}: {result}")
                continue
            content = result.get("error") or result.get("result")
            if isinstance(content, (dict, list)):
                content_text = json.dumps(content, ensure_ascii=False)
            else:
                content_text = str(content or "")
            print(f"- {agent}: {result.get('status')}\n{content_text[:240]}{'...' if len(content_text) > 240 else ''}")

    if isinstance(errors, dict):
        for agent, err in errors.items():
            print(f"- {agent} [DISPATCH_ERROR]: {err}")


def run_task_demo(question: str, *, timeout: float) -> dict[str, Any]:
    try:
        started = time.perf_counter()
        task = submit_task_and_wait(question, timeout=timeout)
        print(f"\n====== Get Final Task (Time elapsed: {(time.perf_counter() - started) * 1000:.2f}ms) ======")
        print_task_summary(task)
        return task
    except HttpJsonClientError as exc:
        print(f"HTTP Request Error: {exc}")
        raise
