from __future__ import annotations

import argparse
import os
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def add_runtime_args(
    parser: argparse.ArgumentParser,
    *,
    default_mode: str = "llm",
    default_realtime: bool = True,
) -> None:
    parser.add_argument(
        "--mode",
        choices=("llm", "no-llm"),
        default=_default_mode(default_mode),
        help="Runtime mode. Default uses LLM calls.",
    )
    realtime_group = parser.add_mutually_exclusive_group()
    realtime_group.add_argument(
        "--realtime",
        dest="realtime",
        action="store_true",
        help="Use realtime MCP data sources.",
    )
    realtime_group.add_argument(
        "--mock-data",
        dest="realtime",
        action="store_false",
        help="Use local deterministic data sources.",
    )
    parser.set_defaults(realtime=_default_realtime(default_realtime))


def apply_runtime_args(args: argparse.Namespace) -> None:
    os.environ["A2A_USE_LLM"] = "1" if getattr(args, "mode", "llm") == "llm" else "0"
    os.environ["A2A_REALTIME_MCP_ENABLED"] = "1" if bool(getattr(args, "realtime", True)) else "0"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def runtime_summary(args: argparse.Namespace) -> str:
    data_source = "realtime" if bool(getattr(args, "realtime", True)) else "local-data"
    return f"mode={getattr(args, 'mode', 'llm')} data={data_source}"


def _default_mode(default: str) -> str:
    explicit = os.getenv("A2A_DEMO_MODE") or os.getenv("A2A_RUNTIME_MODE")
    if explicit:
        normalized = explicit.strip().lower().replace("_", "-")
        if normalized in {"llm", "no-llm"}:
            return normalized

    use_llm = _optional_env_bool("A2A_USE_LLM")
    if use_llm is not None:
        return "llm" if use_llm else "no-llm"
    return default


def _default_realtime(default: bool) -> bool:
    explicit = _optional_env_bool("A2A_REALTIME_MCP_ENABLED")
    return default if explicit is None else explicit


def _optional_env_bool(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None

