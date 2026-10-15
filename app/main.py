import app.config  # noqa: F401  (load .env first)

import asyncio
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from app import agent, evaluator, memory, merchants, money, policy, signing
from app.models import CartOffer, Intent

log = logging.getLogger("wallet")

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "static" / "index.html"
HISTORY = ROOT / ".wallet" / "history.json"
USER_KEY = "user-george"
INTENT_TTL_HOURS = 24
STRIPE_REFRESH_SECONDS = 5

app = FastAPI()
app.include_router(merchants.router)
app.include_router(agent.router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/chat")
def chat_page():
    return RedirectResponse("/agents")


@app.get("/agents")
def agents_page():
    page = ROOT / "static" / "agents.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"ok": True, "ui": "static/agents.html not found"})

STATE = {
    "intent": None,          # signed intent dict
    "intent_hash": None,
    "explanation": None,
    "facts": [],             # last loaded verified facts
    "rows": [],
    "rows_by_id": {},
    "stripe_refreshed_at": 0.0,
    "intents": {},           # intent_hash -> signed intent (every intent the wallet has signed)
    "explanations": {},      # intent_hash -> Cognee explanation
}
BG_TASKS: set = set()


def save_history() -> None:
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        payments = {pid: {"merchant_id": p["merchant_id"], "window_hours": p["window_hours"],
                          "paid_at": p["paid_at"].isoformat() if isinstance(p["paid_at"], datetime) else p["paid_at"]}
                    for pid, p in merchants.PAYMENTS.items()}
        data = {"intent": STATE["intent"], "intent_hash": STATE["intent_hash"], "explanation": STATE["explanation"],
                "intents": STATE["intents"], "explanations": STATE["explanations"], "rows": STATE["rows"], "payments": payments}
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(HISTORY)
    except Exception:
        log.exception("save_history failed")


def load_history() -> None:
    if not HISTORY.exists():
        return
    try:
        data = json.loads(HISTORY.read_text())
    except Exception:
        log.exception("load_history failed")
        return
    rows = data.get("rows") or []
    STATE.update(intent=data.get("intent"), intent_hash=data.get("intent_hash"), explanation=data.get("explanation"),
                 intents=data.get("intents") or {}, explanations=data.get("explanations") or {}, rows=rows, rows_by_id={r["id"]: r for r in rows})
    for pid, p in (data.get("payments") or {}).items():
        try:
            merchants.register_payment(pid, p["merchant_id"], int(p["window_hours"]), parse_ts(p["paid_at"]))
        except Exception:
            log.exception("restore payment %s failed", pid)




class IntentIn(BaseModel):
    request: str
    max_pence: int


class PayIn(BaseModel):
    token: str
    signed_offer: dict
    agent_id: str | None = None
    run_id: str | None = None


class UndoIn(BaseModel):
    payment_id: str


class DecideIn(BaseModel):
    row_id: str
    approve: bool


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def spawn(coro):
    task = asyncio.create_task(coro)
    BG_TASKS.add(task)
    task.add_done_callback(BG_TASKS.discard)
    return task


async def load_facts() -> list[dict]:
    try:
        facts = await memory.load_verified_facts()
        STATE["facts"] = facts
        return facts
    except Exception:
        log.exception("load_verified_facts failed")
        return STATE["facts"]


async def compute_explanation(request: str, intent_hash: str):
    try:
        result = await memory.explain(request)
        if STATE["intent_hash"] == intent_hash:
            STATE["explanation"] = result
        STATE["explanations"][intent_hash] = result
        save_history()
    except Exception:
        log.exception("explain failed")


async def get_blocklist() -> dict:
    try:
        return await memory.blocklist()
    except Exception:
        log.exception("blocklist failed")
        return {}


def new_row(merchant_id, intent_hash, payload) -> dict:
    row = {
        "id": f"row-{uuid.uuid4().hex[:8]}", "at": now_utc().isoformat(), "merchant_id": merchant_id,
        "intent_hash": intent_hash, "items": (payload or {}).get("items", []),
        "signature_ok": None, "check_failure": None, "facts_used": [], "p_match": None, "p_injection": None,
        "eval_source": None,
        "undo": dict((payload or {}).get("undo") or {"window_hours": None, "cancel_url": None, "fee_pence": None}),
        "undo_cost_pence": None, "expected_loss_pence": None, "decision": None, "rule": None,
        "payment_id": None, "payment_mode": None, "stripe_status": None, "window_ends_at": None,
        "merchant_name": merchant_name(merchant_id), "trace": [], "agent_id": None, "run_id": None,
    }
    return row


class Tracer:
    def __init__(self, row: dict):
        self.row = row
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)

    def add(self, step: str, ok, detail: str) -> None:
        self.row.setdefault("trace", []).append({"t_ms": self.ms(), "step": step, "ok": ok, "detail": detail})


def gbp(pence) -> str:
    return f"£{(pence or 0) / 100:.2f}"


def store_row(row: dict) -> dict:
    STATE["rows"].append(row)
    STATE["rows_by_id"][row["id"]] = row
    save_history()
    return row


async def do_pay(row: dict, signed_offer: dict, p_match: float, rule: str, tr: "Tracer | None" = None) -> None:
    t = time.perf_counter()
    try:
        res = await asyncio.to_thread(money.pay, signed_offer, p_match=p_match if p_match is not None else 0.0, rule=rule)
    except Exception as e:
        log.exception("money.pay failed")
        row["decision"] = "decline"
        row["rule"] = f"{rule} (payment failed: {e})"
        if tr:
            tr.add("Stripe", False, f"payment failed: {e}")
        return
    window = int((row["undo"] or {}).get("window_hours") or 0)
    paid_at = now_utc()
    row["decision"] = "pay"
    row["rule"] = rule
    row["payment_id"] = res.get("payment_id")
    row["payment_mode"] = res.get("mode")
    row["stripe_status"] = res.get("status")
    row["window_ends_at"] = (paid_at + timedelta(hours=window)).isoformat() if window > 0 else None
    if row["payment_id"]:
        merchants.register_payment(row["payment_id"], row["merchant_id"], window, paid_at)
    if tr:
        what = "hold (manual capture, cancellable in the undo window)" if res.get("mode") == "hold" else "charge (captured now)"
        tr.add("Stripe", True, f"{what}: {res.get('payment_id')} status {res.get('status')} in "
                               f"{round((time.perf_counter() - t) * 1000)}ms")


def check_offer(signed_intent: dict, signed_offer: dict, intent_hash: str, tr: "Tracer | None" = None) -> tuple[bool, str | None, dict | None]:
    """Returns (signature_ok, check_failure, offer_payload). Adds one trace step per check when given a tracer."""
    add = tr.add if tr else (lambda *a: None)
    try:
        intent = Intent(**signed_intent["payload"])
    except (KeyError, TypeError, ValidationError):
        add("intent signature", False, "malformed signed intent")
        return False, "malformed intent", None
    try:
        offer = CartOffer(**signed_offer["payload"])
    except (KeyError, TypeError, ValidationError):
        add("cart signature", False, "malformed signed cart")
        return False, "malformed offer", None
    intent_ok = signing.verify(signed_intent, USER_KEY)
    add("intent signature", intent_ok, f"Ed25519 signature by {USER_KEY} " + ("valid" if intent_ok else "INVALID"))
    if not intent_ok:
        return False, "intent signature invalid", offer.model_dump()
    known = offer.merchant_id in merchants.MERCHANTS
    offer_ok = known and signing.verify(signed_offer, f"merchant-{offer.merchant_id}")
    add("cart signature", offer_ok, f"Ed25519 signature by merchant-{offer.merchant_id} " +
        ("valid" if offer_ok else ("unknown merchant" if not known else "INVALID")))
    if not offer_ok:
        return False, f"cart signature invalid for merchant-{offer.merchant_id}", offer.model_dump()
    hash_ok = offer.intent_hash == intent_hash
    add("intent hash", hash_ok, f"cart bound to intent {offer.intent_hash[:12]}… " +
        ("= signed intent" if hash_ok else f"≠ signed intent {intent_hash[:12]}…"))
    if not hash_ok:
        return True, "cart intent_hash does not match the signed intent", offer.model_dump()
    now = now_utc()
    try:
        ie, oe = parse_ts(intent.expires_at), parse_ts(offer.expires_at)
    except ValueError:
        add("expiry", False, "bad expiry timestamp")
        return True, "bad expiry timestamp", offer.model_dump()
    if ie < now:
        add("expiry", False, f"signed intent expired at {intent.expires_at}")
        return True, "signed intent expired", offer.model_dump()
    if oe < now:
        add("expiry", False, f"cart offer expired at {offer.expires_at}")
        return True, "cart offer expired", offer.model_dump()
    add("expiry", True, f"intent valid {round((ie - now).total_seconds() / 3600)}h more, cart "
                        f"{round((oe - now).total_seconds() / 60)} min more")
    total = sum(i.price_pence for i in offer.items)
    cap_ok = total <= intent.max_pence
    add("spending cap", cap_ok, f"cart total {gbp(total)} {'≤' if cap_ok else '>'} signed cap {gbp(intent.max_pence)}")
    if not cap_ok:
        return True, f"cart total £{total / 100:.2f} > signed cap £{intent.max_pence / 100:.2f}", offer.model_dump()
    return True, None, offer.model_dump()


@app.post("/api/intent")
async def post_intent(body: IntentIn):
    payload = {
        "request": body.request, "max_pence": body.max_pence,
        "expires_at": (now_utc() + timedelta(hours=INTENT_TTL_HOURS)).isoformat(),
        "nonce": secrets.token_hex(8),
    }
    signed_intent = signing.sign(payload, USER_KEY)
    intent_hash = signing.sha256_hex(signed_intent)
    STATE.update(intent=signed_intent, intent_hash=intent_hash, explanation=None)
    STATE["intents"][intent_hash] = signed_intent
    save_history()
    spawn(compute_explanation(body.request, intent_hash))
    spawn(load_facts())
    return {"token": signing.encode_token(signed_intent), "intent_hash": intent_hash, "signed_intent": signed_intent}


def decode(token: str) -> dict:
    try:
        signed_intent = signing.decode_token(token)
        assert isinstance(signed_intent, dict) and "payload" in signed_intent
        return signed_intent
    except Exception:
        raise HTTPException(400, "bad intent token")


@app.get("/api/offers")
def get_offers(token: str):
    signed_intent = decode(token)
    intent_hash = signing.sha256_hex(signed_intent)
    return {"offers": [merchants.make_offer(m, intent_hash) for m in merchants.MERCHANTS]}


@app.post("/api/pay")
async def post_pay(body: PayIn):
    signed_intent = decode(body.token)
    intent_hash = signing.sha256_hex(signed_intent)
    STATE["intents"].setdefault(intent_hash, signed_intent)
    if STATE["intent"] is None:
        STATE.update(intent=signed_intent, intent_hash=intent_hash)
        req = (signed_intent.get("payload") or {}).get("request", "")
        spawn(compute_explanation(req, intent_hash))
    signed_offer = body.signed_offer or {}
    raw_payload = signed_offer.get("payload") if isinstance(signed_offer.get("payload"), dict) else {}
    merchant_id = raw_payload.get("merchant_id")
    row = new_row(merchant_id, intent_hash, raw_payload)
    row["agent_id"], row["run_id"] = body.agent_id, body.run_id
    tr = Tracer(row)
    items = raw_payload.get("items") or []
    total = sum(int(i.get("price_pence") or 0) for i in items if isinstance(i, dict))
    who = f" from agent {body.agent_id}" if body.agent_id else ""
    tr.add("received", None, f"pay request{who}: {merchant_name(merchant_id) or merchant_id} cart, "
                             f"{len(items)} item(s), {gbp(total)}")

    # 1. blocklist: stop before anything else, evaluator not called
    blocked = await get_blocklist()
    if merchant_id in blocked:
        ev = blocked[merchant_id] or {}
        p = ev.get("p_injection")
        tr.add("blocklist", False, f"merchant-{merchant_id} flagged for prompt injection at {ev.get('at')}"
                                   + (f" (P(injection) {p:.2f})" if isinstance(p, (int, float)) else "")
                                   + "; refused before any other check, Jev not called")
        row.update(policy.decide(blocked=blocked[merchant_id] or True, check_failure=None, p_match=None, p_injection=None,
                                 offer=signed_offer, refused_undo=False))
        tr.add("policy", False, f"{row['decision']}: {row['rule']}")
        tr.add("Stripe", None, "not called")
        return public_row(store_row(row))
    tr.add("blocklist", True, f"merchant-{merchant_id} not on the blocklist ({len(blocked)} flagged)")

    # 2. signatures, hash, expiries, cap
    sig_ok, failure, offer_payload = check_offer(signed_intent, signed_offer, intent_hash, tr)
    row["signature_ok"] = sig_ok
    row["check_failure"] = failure
    if failure:
        row.update(policy.decide(blocked=False, check_failure=failure, p_match=None, p_injection=None,
                                 offer=signed_offer, refused_undo=False))
        tr.add("policy", False, f"{row['decision']}: {row['rule']}")
        tr.add("Stripe", None, "not called")
        return public_row(store_row(row))

    # 3. verified facts
    t = time.perf_counter()
    facts = await load_facts()
    row["facts_used"] = [{"relation": f.get("relation"), "object": f.get("object")} for f in facts]
    tr.add("signed facts loaded", bool(facts),
           f"{len(facts)} verified (Ed25519, {USER_KEY}) from Cognee in {round((time.perf_counter() - t) * 1000)}ms: "
           + ", ".join(f"{f.get('relation')}={f.get('object')}" for f in facts))

    # 4. evaluator
    request = signed_intent["payload"]["request"]
    items = offer_payload["items"]
    t = time.perf_counter()
    try:
        sentences = evaluator.render_facts(facts)
        probs = await asyncio.to_thread(evaluator.evaluate, request, sentences, items)
    except Exception as e:
        log.exception("evaluator failed")
        row.update(decision="decline", rule=f"evaluator unavailable: {e}")
        tr.add("Jev", False, f"evaluator unavailable after {round((time.perf_counter() - t) * 1000)}ms: {e}")
        tr.add("policy", False, f"decline: {row['rule']}")
        tr.add("Stripe", None, "not called")
        return public_row(store_row(row))
    p_match, p_injection = probs.get("p_match"), probs.get("p_injection")
    row.update(p_match=p_match, p_injection=p_injection, eval_source=probs.get("source"))
    tr.add("Jev", None, f"P(match) {p_match:.2f}, P(injection) {p_injection:.2f} via {probs.get('source')} in "
                        f"{round((time.perf_counter() - t) * 1000)}ms")

    # 5. policy
    try:
        refused = await memory.has_refused_undo(merchant_id)
    except Exception:
        log.exception("has_refused_undo failed")
        refused = False
    decision = policy.decide(blocked=False, check_failure=None, p_match=p_match, p_injection=p_injection,
                             offer=signed_offer, refused_undo=refused)
    row.update(decision)
    extra = ""
    if row.get("undo_cost_pence") is not None:
        extra = f" (undo cost {gbp(row['undo_cost_pence'])}, expected loss {gbp(row['expected_loss_pence'])})"
    tr.add("policy", None if row["decision"] == "ask" else row["decision"] == "pay",
           f"{row['decision']}: {row['rule']}{extra}")

    if p_injection is not None and p_injection >= policy.INJECTION_THRESHOLD:
        try:
            await memory.flag_injection(merchant_id, p_injection, signed_offer)
            tr.add("blocklist", False, f"merchant-{merchant_id} added to the blocklist; signed cart kept as evidence "
                                       "in Cognee provider history")
        except Exception as e:
            log.exception("flag_injection failed")
            tr.add("blocklist", False, f"flagging merchant-{merchant_id} failed: {e}")

    # 6. money
    if row["decision"] == "pay":
        await do_pay(row, signed_offer, p_match, row["rule"], tr)
    elif row["decision"] == "ask":
        tr.add("Stripe", None, "not called yet: waiting for the user to approve")
    else:
        tr.add("Stripe", None, "not called")
    row["_signed_offer"] = signed_offer
    store_row(row)
    return public_row(row)


def merchant_name(merchant_id) -> str | None:
    return (merchants.MERCHANTS.get(merchant_id) or {}).get("name")


def public_row(row: dict) -> dict:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out["merchant_name"] = merchant_name(row.get("merchant_id")) or row.get("merchant_name")
    intent = STATE["intents"].get(row.get("intent_hash")) or {}
    out["request"] = (intent.get("payload") or {}).get("request")
    out["max_pence"] = (intent.get("payload") or {}).get("max_pence")
    return out


@app.post("/api/undo")
async def post_undo(body: UndoIn):
    row = next((r for r in STATE["rows"] if r.get("payment_id") == body.payment_id), None)
    merchant_id = row["merchant_id"] if row else (merchants.PAYMENTS.get(body.payment_id) or {}).get("merchant_id")
    if not merchant_id:
        raise HTTPException(404, "unknown payment")
    res = await merchants.cancel_payment(merchant_id, body.payment_id)
    if row:
        row["stripe_status"] = res.get("status")
        save_history()
    return {"payment_id": body.payment_id, "status": res.get("status")}


@app.post("/api/decide")
async def post_decide(body: DecideIn):
    row = STATE["rows_by_id"].get(body.row_id)
    if not row:
        raise HTTPException(404, "unknown row")
    if row["decision"] != "ask":
        raise HTTPException(409, f"row decision is {row['decision']}, not ask")
    tr = Tracer(row)
    tr.t0 = time.perf_counter() - (now_utc() - parse_ts(row["at"])).total_seconds()
    if body.approve:
        tr.add("user decision", True, "approved by user")
        await do_pay(row, row.get("_signed_offer"), row["p_match"], "approved by user", tr)
    else:
        row.update(decision="decline", rule="rejected by user")
        tr.add("user decision", False, "rejected by user")
    save_history()
    return public_row(row)


FINAL_STRIPE = {"canceled", "refunded"}


async def refresh_stripe():
    changed = False
    for row in list(STATE["rows"]):
        pid = row.get("payment_id")
        if not pid or row.get("stripe_status") in FINAL_STRIPE:
            continue
        try:
            new = await asyncio.to_thread(money.status, pid)
        except Exception:
            log.exception("stripe status failed for %s", pid)
            continue
        if new != row.get("stripe_status"):
            row["stripe_status"] = new
            changed = True
    if changed:
        save_history()


@app.get("/api/audit")
async def get_audit():
    if time.monotonic() - STATE["stripe_refreshed_at"] > STRIPE_REFRESH_SECONDS:
        STATE["stripe_refreshed_at"] = time.monotonic()
        spawn(refresh_stripe())
    blocked = await get_blocklist()
    return {
        "intent": STATE["intent"],
        "intent_hash": STATE["intent_hash"],
        "facts": [{"relation": f.get("relation"), "object": f.get("object"), "signed_at": f.get("signed_at"),
                   "verified": f.get("verified", True)} for f in STATE["facts"]],
        "explanation": STATE["explanation"],
        "explanations": STATE["explanations"],
        "rows": [public_row(r) for r in STATE["rows"]],
        "blocklist": [{"merchant_id": m, "merchant_name": merchant_name(m), "p_injection": (ev or {}).get("p_injection"),
                       "signed_cart": (ev or {}).get("signed_cart"), "at": (ev or {}).get("at")}
                      for m, ev in blocked.items()],
    }


load_history()


@app.on_event("startup")
async def warm():
    spawn(load_facts())


@app.get("/")
def index():
    if INDEX.exists():
        return FileResponse(INDEX)
    return JSONResponse({"ok": True, "ui": "static/index.html not found"})
