import app.config  # noqa: F401  (must load before cognee)

_blocklist: dict[str, dict] = {}


async def load_verified_facts() -> list[dict]:
    return []


async def explain(request: str) -> dict:
    return {"answer": "", "cited": []}


async def flag_injection(merchant_id: str, p_injection: float, signed_cart: dict) -> None:
    raise NotImplementedError


async def blocklist() -> dict[str, dict]:
    return dict(_blocklist)


async def record_undo_outcome(merchant_id: str, order_id: str, outcome: str) -> None:
    raise NotImplementedError


async def has_refused_undo(merchant_id: str) -> bool:
    return False
