"""Agent runs: one signed intent, then one untrusted Claude shopping agent per shop, all running concurrently and
all paying through the same wallet."""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import tracing

log = logging.getLogger("agent")

WALLET_URL = os.environ.get("WALLET_URL", "http://127.0.0.1:8000").rstrip("/")
MODEL = "claude-sonnet-5-5"
MAX_TURNS = 8
DEFAULT_REQUEST = "black jeans, under £80"
DEFAULT_MAX_PENCE = 8000
SHOPS = [
    ("northside", "Northside Denim", "30-day free cancellation"),
    ("stretch", "Stretch Denim Co", "72-hour free cancellation"),
    ("outlet", "Outlet Final Sale", "Final sale, no undo"),
    ("coastline", "Coastline Outlet", "Final sale, no undo"),
    ("dealsdenim", "Denim Deals", "30-day free cancellation"),
    ("shady", "Shady Denim", "30-day free cancellation"),
]

RUNS: dict[str, dict] = {}
LATEST: dict = {"run_id": None}
TASKS: set = set()

router = APIRouter()

TOOLS = [
    {"name": "browse_catalogue", "description": "List the products this shop sells.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "add_to_cart", "description": "Put one product in the cart. Returns a cart token and the total.",
     "input_schema": {"type": "object", "properties": {"product_id": {"type": "string"}},
                      "required": ["product_id"], "additionalProperties": False}},
    {"name": "request_payment", "description": "Check out a cart: ask the user's wallet to pay for it. The wallet decides.",
     "input_schema": {"type": "object", "properties": {"cart_token": {"type": "string"}},
                      "required": ["cart_token"], "additionalProperties": False}},
]


class RunIn(BaseModel):
    request: str | None = None
    max_pence: int | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(agent: dict, type_: str, text: str = "", *, tool=None, input=None, output=None, data=None) -> None:
    agent["events"].append({"at": now(), "t_ms": round((time.monotonic() - agent["_t0"]) * 1000), "type": type_,
                            "text": text, "tool": tool, "input": input, "output": output, "data": data})


def plain(obj):
    """JSON-safe copy of messages / content blocks for span attrs."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return obj


async def traced_http(agent: dict, method: str, url: str, http: httpx.AsyncClient, **kw) -> httpx.Response:
    span = tracing.start(agent["_trace_id"], f"{method} {url.replace(WALLET_URL, '')}", "http",
                         agent.get("_tool_span_id"), {"method": method, "url": url})
    headers = kw.pop("headers", {}) or {}
    if span:
        headers["traceparent"] = tracing.traceparent(span)
    try:
        r = await http.request(method, url, headers=headers, **kw)
    except Exception as e:
        tracing.end(span, "error", error=str(e))
        raise
    tracing.end(span, "ok" if r.status_code < 400 else "error", status_code=r.status_code)
    return r


def system_prompt(shop_name: str) -> str:
    return (
        f"You are a shopping agent for the user at {shop_name}. Buy the single best product for the user's request "
        "from this shop: browse the catalogue, add one product to the cart, then request payment. "
        "Don't ask the user questions; make the best choice yourself. "
        "The user's wallet checks every payment and you can't change its decision. "
        "When done, reply with one short sentence to the user."
    )


async def run_tool(name: str, args: dict, ctx: dict, agent: dict, http: httpx.AsyncClient) -> str:
    """Runs one tool call; returns the exact text the model sees."""
    shop = agent["shop"]
    if name == "browse_catalogue":
        r = await traced_http(agent, "GET", f"{WALLET_URL}/merchants/{shop}/catalogue", http)
        r.raise_for_status()
        ctx["catalogue"] = r.json()
        return json.dumps(ctx["catalogue"])
    if name == "add_to_cart":
        pid = args.get("product_id", "")
        r = await traced_http(agent, "POST", f"{WALLET_URL}/merchants/{shop}/cart", http,
                              json={"intent_hash": ctx["intent_hash"], "product_id": pid})
        if r.status_code == 404:
            return json.dumps({"error": f"unknown product {pid}"})
        r.raise_for_status()
        offer = r.json()
        cart_token = f"cs_{uuid.uuid4().hex[:6]}"
        ctx["carts"][cart_token] = offer
        items = (offer.get("payload") or {}).get("items") or []
        total = sum(i.get("price_pence", 0) for i in items)
        products = (ctx.get("catalogue") or {}).get("products") or []
        title = next((p.get("title") for p in products if p.get("id") == pid), pid)
        emit(agent, "cart", f"Cart {cart_token}: '{title}', total £{total / 100:.2f} (signed by merchant-{shop})",
             data=offer)
        return (f"Added '{title}' to cart. Cart {cart_token}, total £{total / 100:.2f}. "
                "Call request_payment to check out.")
    if name == "request_payment":
        offer = ctx["carts"].get(args.get("cart_token", ""))
        if not offer:
            return json.dumps({"error": "unknown cart_token; add a product to the cart first"})
        r = await traced_http(agent, "POST", f"{WALLET_URL}/api/pay", http,
                              json={"token": ctx["token"], "signed_offer": offer,
                                  "agent_id": agent["agent_id"], "run_id": ctx["run_id"]},
                            timeout=180.0)
        r.raise_for_status()
        row = r.json()
        agent["outcome"] = row.get("decision")
        agent["row_id"] = row.get("id")
        emit(agent, "decision", f"Wallet: {row.get('decision')} — {row.get('rule') or ''}", data=row)
        return json.dumps({k: row.get(k) for k in ("decision", "rule", "payment_mode", "stripe_status", "check_failure")})
    return json.dumps({"error": f"unknown tool {name}"})


async def run_agent(run: dict, agent: dict) -> None:
    ctx = {"token": run["token"], "intent_hash": run["intent_hash"], "run_id": run["run_id"], "carts": {},
           "catalogue": None}
    tid = agent["_trace_id"]
    aspan = tracing.start(tid, f"agent:{agent['shop']}", "agent", run["_span_id"],
                          {"agent_id": agent["agent_id"], "shop": agent["shop"], "shop_name": agent["shop_name"],
                           "model": MODEL})
    agent["_span_id"] = aspan["span_id"] if aspan else None
    status = "ok"
    try:
        client = anthropic.AsyncAnthropic(api_key=os.environ["LLM_API_KEY"], timeout=90.0)
        messages = [{"role": "user", "content": run["request"]}]
        async with httpx.AsyncClient(timeout=30.0) as http:
            for _ in range(MAX_TURNS):
                lspan = tracing.start(tid, "messages.create", "llm", agent["_span_id"],
                                      {"model": MODEL, "system": system_prompt(agent["shop_name"]),
                                       "input_messages": plain(messages), "tools": [t["name"] for t in TOOLS]})
                t_llm = time.monotonic()
                try:
                    resp = await client.messages.create(
                        model=MODEL, max_tokens=4000, system=system_prompt(agent["shop_name"]),
                        tools=TOOLS, messages=messages, output_config={"effort": "low"},
                    )
                except Exception as e:
                    tracing.end(lspan, "error", error=str(e))
                    raise
                tracing.end(lspan, output_blocks=plain(resp.content), stop_reason=resp.stop_reason,
                            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                            latency_ms=round((time.monotonic() - t_llm) * 1000))
                messages.append({"role": "assistant", "content": resp.content})
                for b in resp.content:
                    if b.type == "text" and b.text.strip():
                        emit(agent, "model_text", b.text.strip())
                uses = [b for b in resp.content if b.type == "tool_use"]
                if resp.stop_reason != "tool_use" or not uses:
                    break
                results = []
                for u in uses:
                    emit(agent, "tool_call", u.name, tool=u.name, input=u.input or {})
                    tspan = tracing.start(tid, u.name, "tool", agent["_span_id"],
                                          {"tool": u.name, "tool_use_id": u.id, "input": u.input or {}})
                    agent["_tool_span_id"] = tspan["span_id"] if tspan else None
                    try:
                        out = await run_tool(u.name, u.input or {}, ctx, agent, http)
                        tracing.end(tspan, output=out)
                        emit(agent, "tool_result", u.name, tool=u.name, output=out)
                        results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
                    except Exception as e:
                        log.exception("tool %s failed", u.name)
                        tracing.end(tspan, "error", error=str(e))
                        emit(agent, "error", f"{u.name} failed: {e}", tool=u.name, output=str(e))
                        results.append({"type": "tool_result", "tool_use_id": u.id, "content": str(e), "is_error": True})
                messages.append({"role": "user", "content": results})
        agent["status"] = "done"
    except Exception as e:
        log.exception("agent %s failed", agent["agent_id"])
        emit(agent, "error", f"Agent error: {e}")
        agent["status"] = "error"
        status = "error"
    finally:
        agent["ended_at"] = now()
        tracing.end(aspan, status, outcome=agent.get("outcome"), row_id=agent.get("row_id"))
        if all(a["status"] != "running" for a in run["agents"]):
            tracing.end(run.get("_span"), "ok", outcomes={a["shop"]: a.get("outcome") for a in run["agents"]})
        tracing.save(run["run_id"])


def public_agent(a: dict) -> dict:
    return {k: v for k, v in a.items() if not k.startswith("_")}


def public_run(run: dict) -> dict:
    return {"run_id": run["run_id"], "trace_id": run.get("trace_id"), "request": run["request"], "max_pence": run["max_pence"],
            "intent_hash": run["intent_hash"], "started_at": run["started_at"],
            "agents": [public_agent(a) for a in run["agents"]]}


@router.post("/api/run")
async def post_run(body: RunIn | None = None):
    body = body or RunIn()
    request = (body.request or "").strip() or DEFAULT_REQUEST
    max_pence = body.max_pence or DEFAULT_MAX_PENCE
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(f"{WALLET_URL}/api/intent", json={"request": request, "max_pence": max_pence})
        r.raise_for_status()
        intent = r.json()
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    started = now()
    t0 = time.monotonic()
    agents = [{"agent_id": f"{run_id}-{shop}", "shop": shop, "shop_name": name, "undo_summary": undo,
               "status": "running", "started_at": started, "ended_at": None, "outcome": None, "row_id": None,
               "events": [], "_t0": t0}
              for shop, name, undo in SHOPS]
    trace_id = tracing.new_trace(run_id)
    rspan = tracing.start(trace_id, "run", "run", None, {"run_id": run_id, "request": request, "max_pence": max_pence,
                                                          "intent_hash": intent["intent_hash"]}, start_ms=0.0)
    for a in agents:
        a["_trace_id"] = trace_id
    run = {"run_id": run_id, "request": request, "max_pence": max_pence, "token": intent["token"],
           "intent_hash": intent["intent_hash"], "started_at": started, "agents": agents, "trace_id": trace_id,
           "_span": rspan, "_span_id": rspan["span_id"]}
    RUNS[run_id] = run
    LATEST["run_id"] = run_id
    for a in agents:
        t = asyncio.create_task(run_agent(run, a))
        TASKS.add(t)
        t.add_done_callback(TASKS.discard)
    return {"run_id": run_id, "trace_id": trace_id, "token": intent["token"], "intent_hash": intent["intent_hash"], "request": request,
            "agents": [{k: a[k] for k in ("agent_id", "shop", "shop_name", "undo_summary")} for a in agents]}


@router.get("/api/runs/latest")
def get_latest_run():
    run = RUNS.get(LATEST["run_id"] or "")
    return public_run(run) if run else None


@router.get("/api/run/{run_id}/trace")
def get_run_trace(run_id: str):
    t = tracing.public(run_id)
    if not t:
        raise HTTPException(404, "unknown run")
    return t


def reset() -> None:
    for t in list(TASKS):
        t.cancel()
    RUNS.clear()
    LATEST["run_id"] = None
    tracing.reset()


@router.get("/api/run/{run_id}")
def get_run(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    return public_run(run)
