from fastapi import APIRouter

MERCHANTS: dict[str, dict] = {}

router = APIRouter()


def make_offer(merchant_id: str, intent_hash: str) -> dict:
    raise NotImplementedError


@router.get("/merchants/{merchant_id}/offer")
def get_offer(merchant_id: str, intent_hash: str):
    return make_offer(merchant_id, intent_hash)


@router.post("/merchants/{merchant_id}/cancel")
def cancel(merchant_id: str, body: dict):
    raise NotImplementedError  # {"payment_id"} -> checks window, money.undo, records undo outcome
