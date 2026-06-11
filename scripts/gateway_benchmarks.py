from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any
from urllib import request

import matplotlib.pyplot as plt


logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("fontTools").setLevel(logging.WARNING)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import MCP_GATEWAY, MCP_SERVERS  # noqa: E402
from common.http_client import HttpJsonClientError, post_json  # noqa: E402
from scripts.start_all import run_services, service_names  # noqa: E402


OUT_DIR = PROJECT_ROOT / "results" / "gateway_benchmarks"
WEATHER_MCP_URL = (
    f"http://{MCP_SERVERS['weather']['host']}:{MCP_SERVERS['weather']['port']}"
    f"{MCP_SERVERS['weather'].get('path', '/')}"
)
GATEWAY_URL = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
METRICS_URL = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}/metrics"
CACHE_CLEAR_URL = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}/cache/clear"


def main() -> None:
    args = _parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw_samples.json"
    summary_path = out_dir / "summary.csv"

    if args.plot_only:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        raw = run_benchmarks(repeats=args.repeats, quick=args.quick)
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = summarize(raw)
    write_summary_csv(rows, summary_path)
    plot_all(raw, rows, out_dir)
    print(f"{'Read' if args.plot_only else 'Wrote'} {raw_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote figures under {out_dir}")


def run_benchmarks(*, repeats: int, quick: bool) -> list[dict[str, Any]]:
    cache_counts = [10, 30, 60] if quick else [10, 30, 60, 100]
    concurrencies = [1, 2, 5, 10, 20] if quick else [1, 2, 5, 10, 20, 40]
    backpressure_concurrency = [2, 5, 10, 20] if quick else [2, 5, 10, 20, 40]
    circuit_requests = 8

    samples: list[dict[str, Any]] = []
    samples.extend(run_cache_reuse(repeats=repeats, counts=cache_counts))
    samples.extend(run_coalescing(repeats=repeats, concurrencies=concurrencies))
    samples.extend(run_backpressure(repeats=repeats, concurrencies=backpressure_concurrency))
    samples.extend(run_circuit_breaker(repeats=repeats, request_count=circuit_requests))
    return samples


def run_cache_reuse(*, repeats: int, counts: list[int]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    payload_base = {"jsonrpc": "2.0", "method": "get_weather", "params": {"date": "2026-06-11"}}

    with _temporary_env({"MCP_GATEWAY_ENABLED": "0", "A2A_REALTIME_MCP_ENABLED": "0"}):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server"}), extra_args={"weather_mcp_server": ["--delay", "0.05"]}):
            _wait_http("http://127.0.0.1:8001/health")
            for repeat in range(repeats):
                for count in counts:
                    city = f"CacheCity-{repeat}-{count}"
                    payload = {**payload_base, "id": "direct-cache", "params": {**payload_base["params"], "city": city}}
                    calls = [_post_sample(WEATHER_MCP_URL, {**payload, "id": f"direct-cache-{repeat}-{count}-{i}"}, timeout=5.0) for i in range(count)]
                    samples.append(_sample_row("cache_reuse", "direct", repeat, count, calls, upstream_calls=count))

    with _temporary_env({"MCP_GATEWAY_ENABLED": "1", "A2A_REALTIME_MCP_ENABLED": "0"}):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server", "mcp_gateway"}), extra_args={"weather_mcp_server": ["--delay", "0.05"]}):
            _wait_http("http://127.0.0.1:8001/health")
            _wait_http("http://127.0.0.1:8100/health")
            for repeat in range(repeats):
                for count in counts:
                    _clear_gateway_cache()
                    before = _gateway_metrics()
                    city = f"CacheCity-{repeat}-{count}"
                    payload = {**payload_base, "id": "gateway-cache", "params": {**payload_base["params"], "city": city}}
                    calls = [_post_sample(GATEWAY_URL, {**payload, "id": f"gateway-cache-{repeat}-{count}-{i}"}, timeout=5.0) for i in range(count)]
                    delta = _metrics_delta(before, _gateway_metrics())
                    samples.append(_sample_row("cache_reuse", "gateway", repeat, count, calls, metrics=delta))
    return samples


def run_coalescing(*, repeats: int, concurrencies: list[int]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    payload_base = {"jsonrpc": "2.0", "method": "get_weather", "params": {"date": "2026-06-11"}}

    with _temporary_env({"MCP_GATEWAY_ENABLED": "0", "A2A_REALTIME_MCP_ENABLED": "0"}):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server"}), extra_args={"weather_mcp_server": ["--delay", "0.20"]}):
            _wait_http("http://127.0.0.1:8001/health")
            for repeat in range(repeats):
                for concurrency in concurrencies:
                    payload = {**payload_base, "params": {**payload_base["params"], "city": f"BurstCity-{repeat}-{concurrency}"}}
                    calls = _parallel_posts(WEATHER_MCP_URL, payload, concurrency, timeout=8.0, id_prefix=f"direct-coal-{repeat}-{concurrency}")
                    samples.append(_sample_row("coalescing", "direct", repeat, concurrency, calls, upstream_calls=concurrency))

    with _temporary_env({"MCP_GATEWAY_ENABLED": "1", "A2A_REALTIME_MCP_ENABLED": "0"}):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server", "mcp_gateway"}), extra_args={"weather_mcp_server": ["--delay", "0.20"]}):
            _wait_http("http://127.0.0.1:8001/health")
            _wait_http("http://127.0.0.1:8100/health")
            for repeat in range(repeats):
                for concurrency in concurrencies:
                    _clear_gateway_cache()
                    before = _gateway_metrics()
                    payload = {**payload_base, "params": {**payload_base["params"], "city": f"BurstCity-{repeat}-{concurrency}"}}
                    calls = _parallel_posts(GATEWAY_URL, payload, concurrency, timeout=8.0, id_prefix=f"gateway-coal-{repeat}-{concurrency}")
                    delta = _metrics_delta(before, _gateway_metrics())
                    samples.append(_sample_row("coalescing", "gateway", repeat, concurrency, calls, metrics=delta))
    return samples


def run_backpressure(*, repeats: int, concurrencies: list[int]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    payload_base = {"jsonrpc": "2.0", "method": "get_weather", "params": {"date": "2026-06-11"}}

    with _temporary_env({"MCP_GATEWAY_ENABLED": "0", "A2A_REALTIME_MCP_ENABLED": "0"}):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server"}), extra_args={"weather_mcp_server": ["--delay", "0.25"]}):
            _wait_http("http://127.0.0.1:8001/health")
            for repeat in range(repeats):
                for concurrency in concurrencies:
                    calls = _parallel_unique_posts(WEATHER_MCP_URL, payload_base, concurrency, timeout=8.0, id_prefix=f"direct-bp-{repeat}-{concurrency}")
                    samples.append(_sample_row("backpressure", "direct", repeat, concurrency, calls, upstream_calls=concurrency))

    with _temporary_env(
        {
            "MCP_GATEWAY_ENABLED": "1",
            "A2A_REALTIME_MCP_ENABLED": "0",
            "MCP_GATEWAY_MAX_CONCURRENT_PER_METHOD": "2",
            "MCP_GATEWAY_RATE_LIMIT_WAIT_SECONDS": "0.02",
        }
    ):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server", "mcp_gateway"}), extra_args={"weather_mcp_server": ["--delay", "0.25"]}):
            _wait_http("http://127.0.0.1:8001/health")
            _wait_http("http://127.0.0.1:8100/health")
            for repeat in range(repeats):
                for concurrency in concurrencies:
                    _clear_gateway_cache()
                    before = _gateway_metrics()
                    calls = _parallel_unique_posts(GATEWAY_URL, payload_base, concurrency, timeout=8.0, id_prefix=f"gateway-bp-{repeat}-{concurrency}")
                    delta = _metrics_delta(before, _gateway_metrics())
                    samples.append(_sample_row("backpressure", "gateway", repeat, concurrency, calls, metrics=delta))
    return samples


def run_circuit_breaker(*, repeats: int, request_count: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    payload = {
        "jsonrpc": "2.0",
        "id": "circuit",
        "method": "get_weather",
        "params": {"city": "CircuitCity", "date": "2026-06-11"},
    }

    with _temporary_env({"MCP_GATEWAY_ENABLED": "0", "A2A_REALTIME_MCP_ENABLED": "0"}):
        with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server"}), extra_args={"weather_mcp_server": ["--delay", "0.50"]}):
            _wait_http("http://127.0.0.1:8001/health")
            for repeat in range(repeats):
                calls = []
                for i in range(request_count):
                    calls.append(_post_sample(WEATHER_MCP_URL, {**payload, "id": f"direct-circuit-{repeat}-{i}"}, timeout=0.22, index=i + 1))
                samples.append(_sample_row("circuit_breaker", "direct", repeat, request_count, calls, upstream_calls=request_count))

    with _temporary_env(
        {
            "MCP_GATEWAY_ENABLED": "1",
            "A2A_REALTIME_MCP_ENABLED": "0",
            "MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS": "0.22",
            "MCP_GATEWAY_CIRCUIT_FAILURE_THRESHOLD": "3",
            "MCP_GATEWAY_CIRCUIT_COOLDOWN_SECONDS": "5",
            "MCP_GATEWAY_RATE_LIMIT_WAIT_SECONDS": "0.20",
        }
    ):
        for repeat in range(repeats):
            with _benchmark_services(exclude=_exclude_all_except({"weather_mcp_server", "mcp_gateway"}), extra_args={"weather_mcp_server": ["--delay", "0.50"]}):
                _wait_http("http://127.0.0.1:8001/health")
                _wait_http("http://127.0.0.1:8100/health")
                _clear_gateway_cache()
                before = _gateway_metrics()
                calls = []
                for i in range(request_count):
                    calls.append(_post_sample(GATEWAY_URL, {**payload, "id": f"gateway-circuit-{repeat}-{i}"}, timeout=2.0, index=i + 1))
                delta = _metrics_delta(before, _gateway_metrics())
                samples.append(_sample_row("circuit_breaker", "gateway", repeat, request_count, calls, metrics=delta))
    return samples


def summarize(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in samples:
        latencies = [float(call["latency_ms"]) for call in sample["calls"]]
        outcomes = [str(call["outcome"]) for call in sample["calls"]]
        requests = len(sample["calls"])
        success_count = outcomes.count("success")
        row = {
            "experiment": sample["experiment"],
            "variant": sample["variant"],
            "repeat": sample["repeat"],
            "x": sample["x"],
            "requests": requests,
            "success_count": success_count,
            "error_count": requests - success_count,
            "busy_count": outcomes.count("busy"),
            "circuit_open_count": outcomes.count("circuit_open"),
            "timeout_count": outcomes.count("timeout"),
            "upstream_calls": sample.get("metrics", {}).get("upstream_calls", sample.get("upstream_calls", 0)),
            "cache_hits": sample.get("metrics", {}).get("cache_hits", 0),
            "cache_misses": sample.get("metrics", {}).get("cache_misses", 0),
            "coalesced_requests": sample.get("metrics", {}).get("coalesced_requests", 0),
            "rate_limited": sample.get("metrics", {}).get("rate_limited", 0),
            "circuit_open": sample.get("metrics", {}).get("circuit_open", 0),
            "latency_mean_ms": _mean(latencies),
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "latency_min_ms": min(latencies) if latencies else 0.0,
            "latency_max_ms": max(latencies) if latencies else 0.0,
        }
        rows.append(row)
    return rows


def write_summary_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_all(samples: list[dict[str, Any]], rows: list[dict[str, Any]], out_dir: Path) -> None:
    _set_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.2), constrained_layout=True)
    _plot_cache(rows, axes[0, 0])
    _plot_coalescing(rows, axes[0, 1])
    _plot_backpressure(rows, axes[1, 0])
    _plot_circuit(samples, axes[1, 1])
    fig.suptitle("MCP Gateway Governance Benchmarks", fontsize=15, fontweight="bold")
    _save_figure(fig, out_dir / "gateway_benchmark_summary")
    plt.close(fig)

    with plt.rc_context(
        {
            "axes.titlesize": 17,
            "axes.labelsize": 16,
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
            "legend.fontsize": 10.5,
        }
    ):
        fig, axes = plt.subplots(2, 2, figsize=(14.6, 5.4), constrained_layout=True)
        _plot_cache(rows, axes[0, 0])
        _plot_coalescing(rows, axes[0, 1])
        _plot_backpressure(rows, axes[1, 0])
        _plot_circuit(samples, axes[1, 1])
        _save_figure(fig, out_dir / "gateway_benchmark_summary_poster")
        plt.close(fig)

    for name, plotter in [
        ("cache_reuse", _plot_cache),
        ("coalescing", _plot_coalescing),
        ("backpressure", _plot_backpressure),
    ]:
        fig, ax = plt.subplots(figsize=(6.0, 4.0), constrained_layout=True)
        plotter(rows, ax)
        _save_figure(fig, out_dir / name)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.0, 4.0), constrained_layout=True)
    _plot_circuit(samples, ax)
    _save_figure(fig, out_dir / "circuit_breaker")
    plt.close(fig)


def _plot_cache(rows: list[dict[str, Any]], ax: Any) -> None:
    data = _aggregate(rows, "cache_reuse", "x", ["upstream_calls", "latency_p95_ms", "cache_hits"])
    xs = sorted({int(row["x"]) for row in data})
    for variant, color, marker in [("direct", "#C44E52", "o"), ("gateway", "#1F77B4", "s")]:
        ys = [_lookup(data, variant, x, "upstream_calls") for x in xs]
        ax.fill_between(xs, [0] * len(xs), ys, color=color, alpha=0.10, linewidth=0)
        ax.plot(xs, ys, marker=marker, color=color, linewidth=2.0, label=f"{variant}: upstream calls")
    ax.set_title("Cache reuse: upstream load")
    ax.set_xlabel("Repeated requests")
    ax.set_ylabel("Upstream MCP calls")
    ax.set_xticks(xs)
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False)


def _plot_coalescing(rows: list[dict[str, Any]], ax: Any) -> None:
    data = _aggregate(rows, "coalescing", "x", ["upstream_calls", "coalesced_requests", "latency_p95_ms"])
    xs = sorted({int(row["x"]) for row in data})
    for variant, color, marker in [("direct", "#C44E52", "o"), ("gateway", "#1F77B4", "s")]:
        ys = [_lookup(data, variant, x, "upstream_calls") for x in xs]
        ax.fill_between(xs, [0] * len(xs), ys, color=color, alpha=0.10, linewidth=0)
        ax.plot(xs, ys, marker=marker, color=color, linewidth=2.0, label=variant)
    ax.plot(xs, xs, color="#888888", linewidth=1.2, linestyle="--", label="linear load")
    ax.set_title("Duplicate burst: request coalescing")
    ax.set_xlabel("Concurrent duplicate requests")
    ax.set_ylabel("Upstream MCP calls")
    ax.set_xticks(xs)
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False)


def _plot_backpressure(rows: list[dict[str, Any]], ax: Any) -> None:
    data = _aggregate(rows, "backpressure", "x", ["success_count", "busy_count", "upstream_calls", "rate_limited"])
    xs = sorted({int(row["x"]) for row in data})
    width = 0.34
    direct_success = [_lookup(data, "direct", x, "success_count") for x in xs]
    gateway_success = [_lookup(data, "gateway", x, "success_count") for x in xs]
    gateway_busy = [_lookup(data, "gateway", x, "busy_count") for x in xs]
    positions = list(range(len(xs)))
    ax.bar([p - width / 2 for p in positions], direct_success, width, color="#C44E52", label="direct accepted")
    ax.bar([p + width / 2 for p in positions], gateway_success, width, color="#1F77B4", label="gateway accepted")
    ax.bar([p + width / 2 for p in positions], gateway_busy, width, bottom=gateway_success, color="#D8A31A", label="gateway rate-limited")
    ax.set_title("Backpressure: bounded acceptance")
    ax.set_xlabel("Concurrent unique requests")
    ax.set_ylabel("Requests")
    ax.set_xticks(positions, [str(x) for x in xs])
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False, ncols=1)


def _plot_circuit(samples: list[dict[str, Any]], ax: Any) -> None:
    circuit = [s for s in samples if s["experiment"] == "circuit_breaker"]
    if not circuit:
        return
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for sample in circuit:
        by_variant.setdefault(sample["variant"], []).append(sample)
    for variant, color, marker in [("direct", "#C44E52", "o"), ("gateway", "#1F77B4", "s")]:
        grouped: dict[int, list[float]] = {}
        for sample in by_variant.get(variant, []):
            for call in sample["calls"]:
                grouped.setdefault(int(call["index"]), []).append(float(call["latency_ms"]))
        xs = sorted(grouped)
        ys = [statistics.mean(grouped[x]) for x in xs]
        ax.plot(xs, ys, marker=marker, color=color, linewidth=2.0, label=variant)
    ax.axvspan(3.5, 8.5, color="#1F77B4", alpha=0.08, label="gateway circuit open")
    ax.set_title("Circuit breaker: fail-fast after threshold")
    ax.set_xlabel("Request index")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(sorted({int(call["index"]) for s in circuit for call in s["calls"]}))
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False)


def _aggregate(rows: list[dict[str, Any]], experiment: str, x_field: str, metric_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], dict[str, list[float]]] = {}
    for row in rows:
        if row["experiment"] != experiment:
            continue
        key = (str(row["variant"]), int(row[x_field]))
        bucket = groups.setdefault(key, {field: [] for field in metric_fields})
        for field in metric_fields:
            bucket[field].append(float(row[field]))
    out = []
    for (variant, x), values in groups.items():
        item: dict[str, Any] = {"variant": variant, "x": x}
        for field, field_values in values.items():
            item[field] = statistics.mean(field_values) if field_values else 0.0
        out.append(item)
    return out


def _lookup(data: list[dict[str, Any]], variant: str, x: int, field: str) -> float:
    for row in data:
        if row["variant"] == variant and int(row["x"]) == x:
            return float(row[field])
    return 0.0


def _sample_row(
    experiment: str,
    variant: str,
    repeat: int,
    x: int,
    calls: list[dict[str, Any]],
    *,
    metrics: dict[str, Any] | None = None,
    upstream_calls: int | None = None,
) -> dict[str, Any]:
    row = {"experiment": experiment, "variant": variant, "repeat": repeat, "x": x, "calls": calls}
    if metrics is not None:
        row["metrics"] = metrics
    if upstream_calls is not None:
        row["upstream_calls"] = upstream_calls
    return row


def _post_sample(url: str, payload: dict[str, Any], *, timeout: float, index: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = post_json(url, payload, timeout=timeout)
        elapsed_ms = response.elapsed_ms
        outcome = "success"
        error_code = None
        if isinstance(response.data, dict) and response.data.get("error"):
            error = response.data["error"]
            error_code = error.get("code") if isinstance(error, dict) else None
            if error_code == -32001:
                outcome = "circuit_open"
            elif error_code == -32002:
                outcome = "busy"
            else:
                outcome = "rpc_error"
        elif not response.ok:
            outcome = "http_error"
        return {
            "index": index,
            "latency_ms": round(elapsed_ms, 3),
            "status_code": response.status_code,
            "outcome": outcome,
            "error_code": error_code,
        }
    except HttpJsonClientError as exc:
        elapsed_ms = exc.elapsed_ms if exc.elapsed_ms is not None else (time.perf_counter() - started) * 1000
        outcome = "timeout" if "timed out" in str(exc).lower() else "client_error"
        return {
            "index": index,
            "latency_ms": round(elapsed_ms, 3),
            "status_code": 0,
            "outcome": outcome,
            "error_code": None,
        }


def _parallel_posts(url: str, payload: dict[str, Any], concurrency: int, *, timeout: float, id_prefix: str) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for i in range(concurrency):
            req = {**payload, "id": f"{id_prefix}-{i}"}
            futures.append(pool.submit(_post_sample, url, req, timeout=timeout, index=i + 1))
        return [future.result() for future in as_completed(futures)]


def _parallel_unique_posts(url: str, payload: dict[str, Any], concurrency: int, *, timeout: float, id_prefix: str) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for i in range(concurrency):
            params = {**payload.get("params", {}), "city": f"UniqueCity-{id_prefix}-{i}"}
            req = {**payload, "id": f"{id_prefix}-{i}", "params": params}
            futures.append(pool.submit(_post_sample, url, req, timeout=timeout, index=i + 1))
        return [future.result() for future in as_completed(futures)]


def _gateway_metrics() -> dict[str, Any]:
    req = request.Request(METRICS_URL, method="GET", headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=5.0) as response:
        body = json.loads(response.read().decode("utf-8"))
    metrics = body.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("gateway metrics response missing metrics object")
    return metrics


def _metrics_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "total_requests",
        "upstream_calls",
        "cache_hits",
        "cache_misses",
        "coalesced_requests",
        "rate_limited",
        "circuit_open",
        "error_count",
    ]
    return {field: int(after.get(field, 0)) - int(before.get(field, 0)) for field in fields}


def _clear_gateway_cache() -> None:
    req = request.Request(CACHE_CLEAR_URL, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=5.0) as response:
        response.read()


def _wait_http(url: str, *, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with request.urlopen(url, timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"service not ready: {url}: {last_error}")


def _exclude_all_except(keep: set[str]) -> list[str]:
    return [name for name in service_names() if name not in keep]


@contextmanager
def _benchmark_services(*, exclude: list[str], extra_args: dict[str, list[str]]):
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with run_services(
            exclude=exclude,
            extra_args=extra_args,
            mode="no-llm",
            startup_delay_seconds=0.05,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ):
            yield
    finally:
        logging.disable(previous_disable)


@contextmanager
def _temporary_env(updates: dict[str, str]):
    old = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return round(float(ordered[idx]), 3)


def _server_url(server: dict[str, Any]) -> str:
    return f"http://{server['host']}:{server['port']}{server.get('path', '/')}"


def _set_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 8.5,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def _save_figure(fig: Any, stem: Path) -> None:
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reproducible MCP Gateway governance benchmarks.")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help="Directory for raw data, CSV summaries, and figures.")
    parser.add_argument("--repeats", type=int, default=3, help="Number of repetitions per condition.")
    parser.add_argument("--quick", action="store_true", help="Use a smaller condition grid for fast local iteration.")
    parser.add_argument("--plot-only", action="store_true", help="Regenerate figures from an existing raw_samples.json.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
