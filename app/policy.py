INJECTION_THRESHOLD = 0.8
MATCH_FLOOR = 0.25
LOSS_LIMIT_PENCE = 1000


def _gbp(pence: int) -> str:
    return f"£{pence / 100:.2f}"


def _payload(offer: dict) -> dict:
    return offer.get("payload", offer) if isinstance(offer, dict) else {}


def cart_total(offer: dict) -> int:
    return sum(int(i["price_pence"]) for i in _payload(offer).get("items", []))


def decide(*, blocked, check_failure, p_match, p_injection, offer, refused_undo) -> dict:
    out = {"decision": None, "rule": None, "undo_cost_pence": None, "expected_loss_pence": None}
    if blocked:
        ev = blocked if isinstance(blocked, dict) else {}
        when = ""
        try:
            from datetime import datetime
            when = " on " + datetime.fromisoformat(str(ev.get("at")).replace("Z", "+00:00")).strftime("%-d %b %H:%M UTC")
        except (TypeError, ValueError):
            pass
        return {**out, "decision": "block",
                "rule": f"blocked on past record: merchant flagged for prompt injection{when}; this cart was not evaluated"}
    if check_failure:
        return {**out, "decision": "decline", "rule": f"check failed: {check_failure}"}
    if p_injection is not None and p_injection >= INJECTION_THRESHOLD:
        return {**out, "decision": "decline",
                "rule": f"P(injection) {p_injection:.2f} ≥ {INJECTION_THRESHOLD} → decline and blocklist merchant"}
    payload = _payload(offer)
    undo = payload.get("undo") or {}
    window = int(undo.get("window_hours") or 0)
    total = cart_total(offer)
    if window > 0 and bool(undo.get("cancel_url")) and not refused_undo:
        # undoable: always pay (hold or charge by window); the user can undo if it's wrong
        undo_cost = int(undo.get("fee_pence") or 0)
        fee = "free" if undo_cost == 0 else f"{_gbp(undo_cost)} fee"
        out.update(undo_cost_pence=undo_cost, expected_loss_pence=round((1 - p_match) * undo_cost))
        return {**out, "decision": "pay",
                "rule": f"undoable ({fee} {window}h undo) → pay; {round(p_match * 100)}% match, you can undo"}
    undo_cost = total
    if refused_undo:
        terms = "merchant has refused an undo before; full cart at risk"
    else:
        terms = "no undo (final sale); full cart at risk"
    if p_match < MATCH_FLOOR:
        return {**out, "decision": "decline", "rule": f"final sale and P(match) {p_match:.2f} < {MATCH_FLOOR} floor"}
    loss = round((1 - p_match) * undo_cost)
    out.update(undo_cost_pence=undo_cost, expected_loss_pence=loss)
    if loss <= LOSS_LIMIT_PENCE:
        return {**out, "decision": "pay", "rule": f"expected loss {_gbp(loss)} ≤ {_gbp(LOSS_LIMIT_PENCE)} ({terms})"}
    return {**out, "decision": "ask", "rule": f"expected loss {_gbp(loss)} > {_gbp(LOSS_LIMIT_PENCE)} ({terms}) → ask user"}
