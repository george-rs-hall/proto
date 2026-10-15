import os
import time

import stripe

import app.config  # noqa: F401  loads .env
from app import signing

HOLD_LIMIT_HOURS = 168

_key = os.environ["STRIPE_SECRET_KEY"]
if not _key.startswith(("sk_test_", "rk_test_")):
    raise RuntimeError("STRIPE_SECRET_KEY must be a Stripe test-mode key")
stripe.api_key = _key

_customer: tuple[str, str] | None = None

APP_TAG = "proto-wallet"
CUSTOMER_NAME = "Proto agent buyer"
LEGACY_KEYS = ("merchant_id", "intent_hash", "rule", "undo_window_hours")


def ensure_customer() -> tuple[str, str]:
    global _customer
    if _customer is None:
        customer = stripe.Customer.create(name=CUSTOMER_NAME, metadata={"app": APP_TAG})
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
            "app": APP_TAG,
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


def clear_demo() -> dict:
    """Cancel or refund this app's demo payments and delete its customers. Test mode only."""
    global _customer
    assert stripe.api_key.startswith(("sk_test_", "rk_test_")), "clear_demo needs a Stripe test-mode key"
    pis = {pi.id: pi for pi in stripe.PaymentIntent.search(query=f"metadata['app']:'{APP_TAG}'").auto_paging_iter()}
    today = int(time.time()) // 86400 * 86400
    for pi in stripe.PaymentIntent.list(created={"gte": today}, limit=100).auto_paging_iter():
        md = pi.metadata.to_dict() if pi.metadata else {}
        if md.get("app") == APP_TAG or all(k in md for k in LEGACY_KEYS):
            pis[pi.id] = pi
    cancelled = refunded = 0
    for pi_id in pis:
        pi = stripe.PaymentIntent.retrieve(pi_id, expand=["latest_charge"])
        if pi.status in ("requires_capture", "requires_confirmation", "requires_payment_method"):
            stripe.PaymentIntent.cancel(pi_id)
            cancelled += 1
        elif pi.status == "succeeded" and pi.latest_charge and pi.latest_charge.amount_refunded < pi.latest_charge.amount:
            stripe.Refund.create(payment_intent=pi_id)
            refunded += 1
    deleted = 0
    for c in stripe.Customer.list(limit=100).auto_paging_iter():
        if (c.metadata.to_dict() if c.metadata else {}).get("app") == APP_TAG or getattr(c, "name", None) == CUSTOMER_NAME:
            stripe.Customer.delete(c.id)
            deleted += 1
    _customer = None
    print(f"Stripe: cancelled {cancelled}, refunded {refunded}, customers deleted {deleted} (of {len(pis)} demo payments)")
    return {"cancelled": cancelled, "refunded": refunded, "customers_deleted": deleted}
