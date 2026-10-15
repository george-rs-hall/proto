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
from pydantic import BaseModel, SkipValidation

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


class _Relevant(BaseModel):
    answer: str
    relevant_relations: list[str]


_relevant: dict[str, dict] = {}
_relevant_order: list[str] = []


def _question(request: str) -> str:
    return (
        f"George is buying: {request}. Which of George's facts matter for this purchase? "
        "Include every fact that would rule a product in or out for this exact purchase: "
        "sizes, fits, and materials or ingredients George wants or avoids that apply to this kind of product. "
        "Leave out everything else, including general tastes and facts about other kinds of products. "
        "Set relevant_relations to the exact relation names (like waist_size) of those facts, "
        "and answer in one short plain-English sentence."
    )


def _evidence_edges(a) -> list[dict]:
    evidence = (getattr(a, "metadata", None) or {}).get("evidence", []) or []
    labels = {e["artifact_id"]: e.get("label") for e in evidence if e.get("kind") == "graph_node"}
    return [
        {
            "relationship_name": e.get("relationship_name"),
            "source": labels.get(e.get("source_node_id"), e.get("source_node_id")),
            "target": labels.get(e.get("target_node_id"), e.get("target_node_id")),
            "edge_id": e.get("artifact_id"),
        }
        for e in evidence
        if e.get("kind") == "graph_edge"
    ]


async def relevant_facts(request: str) -> dict:
    """Ask Cognee which of George's signed facts matter for this request; return only those, verified.

    {"facts": [fact payloads], "total": N verified, "method": "cognee"|"fallback-all", "answer", "cited": [edges]}
    """
    if request in _relevant:
        return _relevant[request]
    verified = await load_verified_facts()
    by_relation = {f["relation"]: f for f in verified}
    try:
        answers = await cognee.recall(
            _question(request),
            query_type=SearchType.GRAPH_COMPLETION,
            datasets=[FACTS_DATASET],
            include_references=True,
            top_k=40,
            response_model=_Relevant,
        )
        a = answers[0]
        structured = getattr(a, "structured", None) or {}
        if not structured:
            text = getattr(a, "text", None) or str(a)
            try:
                structured = json.loads(text)
            except (ValueError, TypeError):
                structured = {"answer": text, "relevant_relations": []}
        wanted = {r.strip() for r in structured.get("relevant_relations") or []}
        answer = structured.get("answer") or getattr(a, "text", None) or ""
        cited = [e for e in _evidence_edges(a) if e["relationship_name"] in wanted and e["relationship_name"] in by_relation]
        facts = []
        for e in cited:
            f = by_relation[e["relationship_name"]]
            if f not in facts:
                facts.append(f)
        if facts:
            result = {"facts": facts, "total": len(verified), "method": "cognee", "answer": answer, "cited": cited}
        else:
            print(f"[memory] relevant_facts: nothing mapped (wanted={sorted(wanted)}), using all facts")
            result = {"facts": verified, "total": len(verified), "method": "fallback-all", "answer": answer, "cited": []}
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] relevant_facts failed: {exc!r}")
        return {"facts": verified, "total": len(verified), "method": "fallback-all", "answer": "(Cognee unavailable; using all signed facts)", "cited": []}
    _relevant[request] = result
    if request in _relevant_order:
        _relevant_order.remove(request)
    _relevant_order.append(request)
    return result


async def explain(request: str) -> dict:
    r = await relevant_facts(request)
    return {"answer": r["answer"], "cited": r["cited"]}


def _cell(v):
    return v.get("value") if isinstance(v, dict) and "value" in v else v


async def graph(request: str | None = None) -> dict:
    """George's memory graph: nodes, signed edges (verified flag), and edge ids relevant per request."""
    rows = await cognee.recall(
        "MATCH (a:Node)-[r:EDGE]->(b:Node) WHERE a.name = 'George' RETURN a.id, a.name, b.id, b.name, r",
        query_type=SearchType.CYPHER,
        datasets=[FACTS_DATASET],
    )
    nodes: dict[str, dict] = {}
    edges = []
    for row in rows or []:
        raw = getattr(row, "raw", row)
        cells = raw.get("value") if isinstance(raw, dict) else raw
        if not isinstance(cells, list) or len(cells) < 5:
            continue
        aid, aname, bid, bname, r = (_cell(c) for c in cells[:5])
        props = r.get("properties") if isinstance(r, dict) else None
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except ValueError:
                props = {}
        props = props or {}
        fact = props.get("properties") or {}
        if "signature" not in fact:
            continue
        nodes.setdefault(str(aid), {"id": str(aid), "label": aname, "kind": "person"})
        nodes.setdefault(str(bid), {"id": str(bid), "label": bname, "kind": "value"})
        edges.append(
            {
                "id": props.get("edge_object_id") or f"{aid}-{fact.get('relation')}-{bid}",
                "source": str(aid),
                "target": str(bid),
                "relation": fact.get("relation"),
                "object": fact.get("object"),
                "signed_at": fact.get("signed_at"),
                "signature": fact.get("signature"),
                "verified": verify_fact(fact),
            }
        )
    edges.sort(key=lambda e: e["relation"] or "")
    by_relation = {e["relation"]: e["id"] for e in edges}
    if request is not None:
        await relevant_facts(request)
        requests = [request]
    else:
        requests = _relevant_order[-1:]
    relevant = {
        q: [by_relation[f["relation"]] for f in _relevant[q]["facts"] if f["relation"] in by_relation]
        for q in requests
        if q in _relevant and _relevant[q]["method"] == "cognee"
    }
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "relevant": relevant,
        "latest_request": _relevant_order[-1] if _relevant_order else None,
    }


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
                    _blocklist[mid] = {"merchant_id": mid, "p_injection": p.get("p_injection"), "signed_cart": signed_cart, "at": p.get("at"), "prepared": bool(p.get("prepared"))}
                elif kind == "undo_outcome":
                    recs = _undo_outcomes.setdefault(mid, [])
                    if not any(r["order_id"] == p.get("order_id") for r in recs):
                        recs.append({"order_id": p.get("order_id"), "outcome": p.get("outcome"), "at": p.get("at")})
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] provider history load skipped: {exc!r}")
        _loaded = True


async def flag_injection(merchant_id: str, p_injection: float, signed_cart: dict, prepared: bool = False) -> None:
    """prepared=True marks a flag recorded while preparing the demo; reset_provider_history keeps only those."""
    await _load_provider_history()
    at = _now()
    evidence = {"merchant_id": merchant_id, "p_injection": p_injection, "signed_cart": signed_cart, "at": at,
                "prepared": prepared}
    _blocklist[merchant_id] = evidence
    cart_hash = signing.sha256_hex(signed_cart)
    props = {
        "kind": "flagged_injection",
        "merchant_id": merchant_id,
        "p_injection": p_injection,
        "signed_cart": json.dumps(signed_cart, sort_keys=True),
        "cart_hash": cart_hash,
        "at": at,
        "prepared": prepared,
    }
    _mirror(merchant_id, "flagged_injection", cart_hash, props)


async def reset_provider_history() -> list[str]:
    """Drop runtime provider history (flags and undo outcomes not recorded while preparing the demo) from the
    in-process view and from Cognee. Returns the merchant ids still flagged."""
    await _load_provider_history()
    await wait_for_mirrors()
    for mid in [m for m, ev in _blocklist.items() if not (ev or {}).get("prepared")]:
        del _blocklist[mid]
    _undo_outcomes.clear()
    async with _write_lock:
        try:
            await cognee.recall(
                "MATCH (a:Node)-[r:EDGE]->(b:Node) WHERE NOT r.properties CONTAINS '\"prepared\": true' DELETE r",
                query_type=SearchType.CYPHER, datasets=[PROVIDER_DATASET])
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] reset provider history in Cognee failed: {exc!r}")
    return sorted(_blocklist)


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
