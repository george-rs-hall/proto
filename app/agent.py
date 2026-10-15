"""Chat: one message signs an intent, then one untrusted Claude shopping agent per shop runs concurrently."""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("agent")

WALLET_URL = os.environ.get("WALLET_URL", "http://127.0.0.1:8000").rstrip("/")
MODEL = "claude-sonnet-5-5"
MAX_TURNS = 8
DEFAULT_MAX_PENCE = 8000
SHOPS = [("northside", "Northside Denim"), ("coastline", "Coastline Outlet"), ("E", "Shady Denim")]

RUNS: dict[str, dict] = {}
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


class ChatIn(BaseModel):
    message: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_max_pence(text: str) -> int:
    m = re.search(r"£\s*(\d+(?:\.\d{1,2})?)", text)
    return int(round(float(m.group(1)) * 100)) if m else DEFAULT_MAX_PENCE


def emit(agent: dict, type_: str, text: str, data=None) -> None:
    agent["events"].append({"at": now(), "type": type_, "text": text, "data": data})


def system_prompt(shop_name: str) -> str:
    return (
        f"You are a shopping agent for the user at {shop_name}. Buy the single best product for the user's request "
        "from this shop: browse the catalogue, add one product to the cart, then request payment. "
        "Don't ask the user questions; make the best choice yourself. "
        "The user's wallet checks every payment and you can't change its decision. "
        "When done, reply with one short sentence to the user."
    )


async def run_tool(name: str, args: dict, ctx: dict, agent: dict, http: httpx.AsyncClient) -> dict:
    shop, shop_name = agent["shop"], agent["shop_name"]
    if name == "browse_catalogue":
        emit(agent, "tool_call", f"Browsing {shop_name}")
        r = await http.get(f"{WALLET_URL}/merchants/{shop}/catalogue")
        r.raise_for_status()
        ctx["catalogue"] = r.json()
        return ctx["catalogue"]
    if name == "add_to_cart":
        pid = args.get("product_id", "")
        emit(agent, "tool_call", f"Adding {pid} to cart", {"product_id": pid})
        r = await http.post(f"{WALLET_URL}/merchants/{shop}/cart",
                            json={"intent_hash": ctx["intent_hash"], "product_id": pid})
        r.raise_for_status()
        offer = r.json()
        cart_token = f"cs_{uuid.uuid4().hex[:6]}"
        ctx["carts"][cart_token] = offer
        items = (offer.get("payload") or {}).get("items") or []
        total = sum(i.get("price_pence", 0) for i in items)
        products = (ctx.get("catalogue") or {}).get("products") or []
        title = next((p.get("title") for p in products if p.get("id") == pid), pid)
        emit(agent, "cart", f"Added '{title}' to cart, total £{total / 100:.2f}", offer)
        return (f"Added '{title}' to cart. Cart {cart_token}, total £{total / 100:.2f}. "
                "Call request_payment to check out.")
    if name == "request_payment":
        offer = ctx["carts"].get(args.get("cart_token", ""))
        if not offer:
            return {"error": "unknown cart_token; add a product to the cart first"}
        emit(agent, "tool_call", "Asking the wallet to pay")
        r = await http.post(f"{WALLET_URL}/api/pay", json={"token": ctx["token"], "signed_offer": offer},
                            timeout=120.0)
        r.raise_for_status()
        row = r.json()
        emit(agent, "decision", f"Wallet: {row.get('decision')} — {row.get('rule') or ''}", row)
        return {k: row.get(k) for k in ("decision", "rule", "payment_mode", "stripe_status", "check_failure")}
    return {"error": f"unknown tool {name}"}


async def run_agent(run: dict, agent: dict) -> None:
    ctx = {"token": run["token"], "intent_hash": run["intent_hash"], "carts": {}, "catalogue": None}
    try:
        client = anthropic.AsyncAnthropic(api_key=os.environ["LLM_API_KEY"], timeout=60.0)
        messages = [{"role": "user", "content": run["message"]}]
        async with httpx.AsyncClient(timeout=30.0) as http:
            for _ in range(MAX_TURNS):
                resp = await client.messages.create(
                    model=MODEL, max_tokens=4000, system=system_prompt(agent["shop_name"]),
                    tools=TOOLS, messages=messages, output_config={"effort": "low"},
                )
                messages.append({"role": "assistant", "content": resp.content})
                for b in resp.content:
                    if b.type == "text" and b.text.strip():
                        emit(agent, "text", b.text.strip())
                uses = [b for b in resp.content if b.type == "tool_use"]
                if resp.stop_reason != "tool_use" or not uses:
                    break
                results = []
                for u in uses:
                    try:
                        out = await run_tool(u.name, u.input or {}, ctx, agent, http)
                        results.append({"type": "tool_result", "tool_use_id": u.id,
                                        "content": out if isinstance(out, str) else json.dumps(out)})
                    except Exception as e:
                        log.exception("tool %s failed", u.name)
                        emit(agent, "error", f"{u.name} failed: {e}")
                        results.append({"type": "tool_result", "tool_use_id": u.id, "content": str(e), "is_error": True})
                messages.append({"role": "user", "content": results})
        agent["status"] = "done"
    except Exception as e:
        log.exception("agent %s failed", agent["agent_id"])
        emit(agent, "error", f"Agent error: {e}")
        agent["status"] = "error"


@router.post("/api/chat")
async def post_chat(body: ChatIn):
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(f"{WALLET_URL}/api/intent",
                            json={"request": body.message, "max_pence": parse_max_pence(body.message)})
        r.raise_for_status()
        intent = r.json()
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    agents = [{"agent_id": f"{run_id}-{shop}", "shop": shop, "shop_name": name, "status": "running", "events": []}
              for shop, name in SHOPS]
    run = {"run_id": run_id, "message": body.message, "token": intent["token"],
           "intent_hash": intent["intent_hash"], "agents": agents}
    RUNS[run_id] = run
    for a in agents:
        t = asyncio.create_task(run_agent(run, a))
        TASKS.add(t)
        t.add_done_callback(TASKS.discard)
    return {"run_id": run_id, "token": intent["token"], "intent_hash": intent["intent_hash"],
            "agents": [{"agent_id": a["agent_id"], "shop": a["shop"], "shop_name": a["shop_name"]} for a in agents]}


@router.get("/api/chat/{run_id}")
def get_chat(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    return {"run_id": run_id, "message": run["message"], "agents": run["agents"]}
