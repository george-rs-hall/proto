import os

import stripe

import app.config  # noqa: F401  loads .env
from app import signing

HOLD_LIMIT_HOURS = 168

_key = os.environ["STRIPE_SECRET_KEY"]
if not _key.startswith(("sk_test_", "rk_test_")):
    raise RuntimeError("STRIPE_SECRET_KEY must be a Stripe test-mode key")
stripe.api_key = _key

_customer: tuple[str, str] | None = None


def ensure_customer() -> tuple[str, str]:
    global _customer
    if _customer is None:
        customer = stripe.Customer.create(name="Proto agent buyer")
        pm = stripe.PaymentMethod.attach("pm_card_visa", customer=customer.id)
        _customer = (customer.id, pm.id)
    return _customer


def pay(signed_offer: dict, *, p_match: float, rule: str) -> dict:
    offer = signed_offer["payload"]
    window_hours = offer["undo"]["window_hours"]
    mode = "hold" if 0 < window_hours <= HOLD_LIMIT_HOURS else "charge"
    customer_id, payment_method_id = ensure_customer()
    pi = stripe.PaymentIntent.create(
        amount=sum(item["price_pence"] for item in offer["items"]),
        currency="gbp",
        customer=customer_id,
        payment_method=payment_method_id,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        capture_method="manual" if mode == "hold" else "automatic",
        confirm=True,
        off_session=True,
        metadata={
            "merchant_id": offer["merchant_id"],
            "intent_hash": offer["intent_hash"],
            "p_match": str(p_match),
            "undo_window_hours": str(window_hours),
            "rule": rule,
        },
        idempotency_key=signing.sha256_hex(signed_offer),
    )
    return {"payment_id": pi.id, "mode": mode, "status": pi.status}


def undo(payment_id: str) -> dict:
    pi = stripe.PaymentIntent.retrieve(payment_id)
    if pi.status == "requires_capture":
        pi = stripe.PaymentIntent.cancel(payment_id)
        return {"payment_id": payment_id, "status": pi.status}
    if pi.status == "succeeded":
        stripe.Refund.create(payment_intent=payment_id)
        return {"payment_id": payment_id, "status": status(payment_id)}
    return {"payment_id": payment_id, "status": status(payment_id)}


def status(payment_id: str) -> str:
    pi = stripe.PaymentIntent.retrieve(payment_id, expand=["latest_charge"])
    charge = pi.latest_charge
    if pi.status == "succeeded" and charge and charge.refunded:
        return "refunded"
    return pi.status
