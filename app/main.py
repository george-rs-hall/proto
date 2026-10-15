import app.config  # noqa: F401  (load .env first)

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import merchants

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "static" / "index.html"

app = FastAPI()
app.include_router(merchants.router)

STATE = {"intent": None, "intent_hash": None, "rows": []}


class IntentIn(BaseModel):
    request: str
    max_pence: int


class PayIn(BaseModel):
    token: str
    signed_offer: dict


class UndoIn(BaseModel):
    payment_id: str


class DecideIn(BaseModel):
    row_id: str
    approve: bool


def placeholder_row() -> dict:
    return {
        "id": "row-0", "at": None, "merchant_id": None, "intent_hash": None, "items": [],
        "signature_ok": None, "check_failure": None, "facts_used": [], "p_match": None, "p_injection": None,
        "eval_source": None, "undo": {"window_hours": None, "cancel_url": None, "fee_pence": None},
        "undo_cost_pence": None, "expected_loss_pence": None, "decision": None, "rule": None,
        "payment_id": None, "payment_mode": None, "stripe_status": None, "window_ends_at": None,
    }


@app.post("/api/intent")
def post_intent(body: IntentIn):
    return {"token": "", "intent_hash": "", "signed_intent": {"payload": {}, "signature": ""}}


@app.get("/api/offers")
def get_offers(token: str):
    return {"offers": []}


@app.post("/api/pay")
def post_pay(body: PayIn):
    return placeholder_row()


@app.post("/api/undo")
def post_undo(body: UndoIn):
    return {"payment_id": body.payment_id, "status": "unknown"}


@app.post("/api/decide")
def post_decide(body: DecideIn):
    return placeholder_row()


@app.get("/api/audit")
def get_audit():
    return {
        "intent": STATE["intent"],
        "intent_hash": STATE["intent_hash"],
        "facts": [],
        "explanation": None,
        "rows": STATE["rows"],
        "blocklist": [],
    }


@app.get("/")
def index():
    if INDEX.exists():
        return FileResponse(INDEX)
    return JSONResponse({"ok": True, "ui": "static/index.html not found"})
