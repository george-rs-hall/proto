"""In-process span store: one trace per agent run, persisted to .wallet/traces/<run_id>.json.

Span: {"trace_id","span_id","parent_id","name","kind","start_ms","end_ms","status","attrs"} with ms relative to
the run's start. Context crosses HTTP boundaries as a W3C traceparent header ("00-<trace_id>-<span_id>-01").
"""
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path

log = logging.getLogger("tracing")

DIR = Path(__file__).resolve().parent.parent / ".wallet" / "traces"
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")

TRACES: dict[str, dict] = {}     # run_id -> {"trace_id","run_id","t0","spans":[...]}
BY_TRACE_ID: dict[str, str] = {}  # trace_id -> run_id


def new_trace(run_id: str) -> str:
    trace_id = secrets.token_hex(16)
    TRACES[run_id] = {"trace_id": trace_id, "run_id": run_id, "t0": time.time(), "spans": []}
    BY_TRACE_ID[trace_id] = run_id
    return trace_id


def _trace(trace_id: str) -> dict | None:
    run_id = BY_TRACE_ID.get(trace_id)
    return TRACES.get(run_id) if run_id else None


def now_ms(trace_id: str) -> float:
    t = _trace(trace_id)
    return round((time.time() - t["t0"]) * 1000, 1) if t else 0.0


def ms_at(trace_id: str, epoch_s: float) -> float:
    t = _trace(trace_id)
    return round((epoch_s - t["t0"]) * 1000, 1) if t else 0.0


def start(trace_id: str | None, name: str, kind: str, parent_id: str | None = None, attrs: dict | None = None,
          start_ms: float | None = None) -> dict | None:
    t = _trace(trace_id) if trace_id else None
    if t is None:
        return None
    span = {"trace_id": trace_id, "span_id": secrets.token_hex(8), "parent_id": parent_id, "name": name,
            "kind": kind, "start_ms": now_ms(trace_id) if start_ms is None else start_ms, "end_ms": None,
            "status": "ok", "attrs": dict(attrs or {})}
    t["spans"].append(span)
    return span


def end(span: dict | None, status: str = "ok", end_ms: float | None = None, **attrs) -> None:
    if not span:
        return
    span["end_ms"] = now_ms(span["trace_id"]) if end_ms is None else end_ms
    span["status"] = status
    span["attrs"].update(attrs)


def traceparent(span: dict | None) -> str | None:
    return f"00-{span['trace_id']}-{span['span_id']}-01" if span else None


def parse_traceparent(header: str | None) -> tuple[str, str] | None:
    m = TRACEPARENT.match((header or "").strip().lower())
    return (m.group(1), m.group(2)) if m else None


def known(trace_id: str) -> bool:
    return _trace(trace_id) is not None


def public(run_id: str) -> dict | None:
    t = TRACES.get(run_id)
    if t is None:
        try:
            return json.loads((DIR / f"{run_id}.json").read_text())
        except (OSError, ValueError):
            return None
    return {"trace_id": t["trace_id"], "run_id": run_id, "started_at": t["t0"], "spans": t["spans"]}


def save(run_id: str) -> None:
    t = public(run_id)
    if not t:
        return
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = DIR / f"{run_id}.tmp"
        tmp.write_text(json.dumps(t, default=str))
        tmp.replace(DIR / f"{run_id}.json")
    except Exception:
        log.exception("save trace %s failed", run_id)


def reset() -> None:
    TRACES.clear()
    BY_TRACE_ID.clear()
    shutil.rmtree(DIR, ignore_errors=True)
