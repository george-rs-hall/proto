import app.config  # noqa: F401  (must load before cognee)

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import cognee
from cognee import SearchType
from cognee.infrastructure.engine import DataPoint, Edge
from cognee.modules.pipelines import Task
from cognee.tasks.storage import add_data_points
from pydantic import SkipValidation

from app import signing

FACTS_DATASET = "george_facts"
PROVIDER_DATASET = "provider_history"
USER_KEY = "user-george"
FACT_FIELDS = ("subject", "relation", "object", "signed_at", "key_id")

_blocklist: dict[str, dict] = {}
_undo_outcomes: dict[str, list[dict]] = {}
_explanations: dict[str, dict] = {}
_loaded = False
_load_lock = asyncio.Lock()
_write_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


class Merchant(DataPoint):
    name: str
    flagged_injection: SkipValidation[Any] = None
    undo_outcome: SkipValidation[Any] = None
    metadata: dict = {"index_fields": ["name"]}


class Record(DataPoint):
    name: str
    metadata: dict = {"index_fields": ["name"]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_rows(rows) -> list[dict]:
    """Pull the edge dicts out of a Cypher recall result, whatever shape it comes back in."""
    out = []

    def walk(v):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (ValueError, TypeError):
                return
        if isinstance(v, dict):
            if isinstance(v.get("properties"), (dict, str)) and ("relationship_name" in v or "source_node_id" in v):
                props = v["properties"]
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except ValueError:
                        props = {}
                out.append({**v, "properties": props})
                return
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    for row in rows or []:
        raw = getattr(row, "raw", None)
        if raw is not None:
            walk(raw)
            continue
        walk(getattr(row, "text", None) if not isinstance(row, (dict, list, str)) else row)
    return out


async def _cypher(query: str, dataset: str) -> list[dict]:
    rows = await cognee.recall(query, query_type=SearchType.CYPHER, datasets=[dataset])
    return _parse_rows(rows)


async def raw_fact_rows() -> list[dict]:
    """George's signed edges straight from Cognee: fact fields + signature."""
    edges = await _cypher(
        "MATCH (a:Node)-[r:EDGE]->(b:Node) WHERE a.name = 'George' RETURN r.properties", FACTS_DATASET
    )
    return [e["properties"] for e in edges if "signature" in e["properties"]]


def verify_fact(row: dict) -> bool:
    signed = {"payload": {k: row.get(k) for k in FACT_FIELDS}, "signature": row.get("signature")}
    return signing.verify(signed, USER_KEY)


async def load_verified_facts() -> list[dict]:
    rows = await raw_fact_rows()
    return [{k: r[k] for k in FACT_FIELDS} for r in rows if verify_fact(r)]


async def explain(request: str) -> dict:
    if request in _explanations:
        return _explanations[request]
    try:
        answers = await cognee.recall(
            f"Which of George's facts matter for buying: {request}?",
            query_type=SearchType.GRAPH_COMPLETION,
            datasets=[FACTS_DATASET],
            include_references=True,
        )
        a = answers[0]
        evidence = (getattr(a, "metadata", None) or {}).get("evidence", [])
        labels = {e["artifact_id"]: e.get("label") for e in evidence if e.get("kind") == "graph_node"}
        cited = [
            {
                "relationship_name": e.get("relationship_name"),
                "source": labels.get(e.get("source_node_id"), e.get("source_node_id")),
                "target": labels.get(e.get("target_node_id"), e.get("target_node_id")),
                "edge_id": e.get("artifact_id"),
            }
            for e in evidence
            if e.get("kind") == "graph_edge"
        ]
        result = {"answer": getattr(a, "text", None) or str(a), "cited": cited}
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] explain failed: {exc!r}")
        return {"answer": "(explanation unavailable)", "cited": []}
    _explanations[request] = result
    return result


# --- provider history (blocklist, undo outcomes) ---


def _merchant_id(merchant_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"proto-merchant-{merchant_id}")


async def _write_edge(merchant_id: str, relation: str, target_name: str, properties: dict) -> None:
    merchant = Merchant(id=_merchant_id(merchant_id), name=f"merchant-{merchant_id}")
    target = Record(name=target_name)
    setattr(
        merchant,
        relation,
        [Edge(target=target, relationship_type=relation, edge_text=f"merchant {merchant_id} {relation.replace('_', ' ')} {target_name}", properties=properties)],
    )

    async def build(data):
        return [merchant, target]

    async with _write_lock:
        await cognee.run_custom_pipeline(
            tasks=[Task(build), Task(add_data_points)],
            data=[properties],
            dataset=PROVIDER_DATASET,
            pipeline_name=f"provider_{relation}",
        )


def _mirror(merchant_id: str, relation: str, target_name: str, properties: dict) -> None:
    async def run():
        try:
            await _write_edge(merchant_id, relation, target_name, properties)
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] mirror {relation} for {merchant_id} failed: {exc!r}")

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_for_mirrors() -> None:
    """Await pending background writes (for scripts that exit right after flagging)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _load_provider_history() -> None:
    global _loaded
    if _loaded:
        return
    async with _load_lock:
        if _loaded:
            return
        try:
            edges = await _cypher("MATCH (a:Node)-[r:EDGE]->(b:Node) RETURN r.properties", PROVIDER_DATASET)
            for e in edges:
                p = e["properties"]
                kind, mid = p.get("kind"), p.get("merchant_id")
                if not mid:
                    continue
                if kind == "flagged_injection" and mid not in _blocklist:
                    signed_cart = p.get("signed_cart")
                    if isinstance(signed_cart, str):
                        try:
                            signed_cart = json.loads(signed_cart)
                        except ValueError:
                            pass
                    _blocklist[mid] = {"merchant_id": mid, "p_injection": p.get("p_injection"), "signed_cart": signed_cart, "at": p.get("at")}
                elif kind == "undo_outcome":
                    recs = _undo_outcomes.setdefault(mid, [])
                    if not any(r["order_id"] == p.get("order_id") for r in recs):
                        recs.append({"order_id": p.get("order_id"), "outcome": p.get("outcome"), "at": p.get("at")})
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] provider history load skipped: {exc!r}")
        _loaded = True


async def flag_injection(merchant_id: str, p_injection: float, signed_cart: dict) -> None:
    await _load_provider_history()
    at = _now()
    evidence = {"merchant_id": merchant_id, "p_injection": p_injection, "signed_cart": signed_cart, "at": at}
    _blocklist[merchant_id] = evidence
    cart_hash = signing.sha256_hex(signed_cart)
    props = {
        "kind": "flagged_injection",
        "merchant_id": merchant_id,
        "p_injection": p_injection,
        "signed_cart": json.dumps(signed_cart, sort_keys=True),
        "cart_hash": cart_hash,
        "at": at,
    }
    _mirror(merchant_id, "flagged_injection", cart_hash, props)


async def blocklist() -> dict[str, dict]:
    await _load_provider_history()
    return dict(_blocklist)


async def record_undo_outcome(merchant_id: str, order_id: str, outcome: str) -> None:
    await _load_provider_history()
    at = _now()
    _undo_outcomes.setdefault(merchant_id, []).append({"order_id": order_id, "outcome": outcome, "at": at})
    props = {"kind": "undo_outcome", "merchant_id": merchant_id, "order_id": order_id, "outcome": outcome, "at": at}
    _mirror(merchant_id, "undo_outcome", f"order-{order_id}", props)


async def has_refused_undo(merchant_id: str) -> bool:
    await _load_provider_history()
    return any(r["outcome"] == "refused" for r in _undo_outcomes.get(merchant_id, []))
