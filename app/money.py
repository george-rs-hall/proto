def ensure_customer() -> tuple[str, str]:
    raise NotImplementedError


def pay(signed_offer: dict, *, p_match: float, rule: str) -> dict:
    raise NotImplementedError  # {"payment_id", "mode": "hold"|"charge", "status"}


def undo(payment_id: str) -> dict:
    raise NotImplementedError  # {"payment_id", "status"}


def status(payment_id: str) -> str:
    raise NotImplementedError
