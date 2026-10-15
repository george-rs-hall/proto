"""Scripted agent: fetches every merchant offer and asks the wallet to pay each one.

Run: uv run python -m scripts.agent_loop [--token TOKEN] [--delay 1.5] [--no-undo] [--no-retry]
"""
import argparse
import os
import sys
import time

import httpx

WALLET_URL = os.environ.get("WALLET_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST = "black jeans, under £80"
MAX_PENCE = 8000


def merchant_of(offer: dict) -> str:
    return (offer.get("payload") or {}).get("merchant_id", "?")


def fmt(p) -> str:
    return "-" if p is None else f"{p:.2f}"


def line(row: dict) -> str:
    decision = row.get("decision") or "?"
    mode = f" ({row['payment_mode']})" if row.get("payment_mode") else ""
    pid = row.get("payment_id") or "-"
    return (
        f"{row.get('merchant_id') or '?'} → {decision}{mode} {pid} : {row.get('rule') or ''}"
        f"  [P(match) {fmt(row.get('p_match'))}, P(injection) {fmt(row.get('p_injection'))}]"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--undo", default="B", help="merchant whose payment to undo after paying (default B)")
    ap.add_argument("--no-undo", action="store_true")
    ap.add_argument("--retry", default="E", help="merchant to resubmit after the first pass (default E)")
    ap.add_argument("--no-retry", action="store_true")
    args = ap.parse_args()

    c = httpx.Client(base_url=WALLET_URL, timeout=120)

    token = args.token
    if not token:
        r = c.post("/api/intent", json={"request": REQUEST, "max_pence": MAX_PENCE})
        r.raise_for_status()
        token = r.json()["token"]
        print(f"Signed intent for {REQUEST!r} (max £{MAX_PENCE / 100:.0f})")
        print(f"token: {token}\n")
        time.sleep(args.delay)

    def offers() -> dict[str, dict]:
        r = c.get("/api/offers", params={"token": token})
        r.raise_for_status()
        return {merchant_of(o): o for o in r.json()["offers"]}

    def pay(offer: dict) -> dict:
        r = c.post("/api/pay", json={"token": token, "signed_offer": offer})
        r.raise_for_status()
        return r.json()

    by_merchant = offers()
    print(f"Got {len(by_merchant)} signed offers: {', '.join(sorted(by_merchant))}\n")
    time.sleep(args.delay)

    payments: dict[str, str] = {}
    for mid in sorted(by_merchant):
        row = pay(by_merchant[mid])
        print(line(row), flush=True)
        if row.get("payment_id"):
            payments[mid] = row["payment_id"]
        time.sleep(args.delay)

    if not args.no_retry and args.retry:
        print(f"\nResubmitting {args.retry}'s fresh offer…")
        fresh = offers().get(args.retry)
        if fresh is None:
            print(f"{args.retry} → no offer returned")
        else:
            print(line(pay(fresh)), flush=True)
        time.sleep(args.delay)

    if not args.no_undo and args.undo:
        pid = payments.get(args.undo)
        if not pid:
            print(f"\nNo payment for {args.undo} to undo")
        else:
            print(f"\nUser rejects {args.undo}; undoing {pid}…")
            r = c.post("/api/undo", json={"payment_id": pid})
            if r.is_success:
                u = r.json()
                print(f"{args.undo} → undo {u.get('payment_id')} : {u.get('status')}")
            else:
                print(f"{args.undo} → undo failed {r.status_code}: {r.text}", file=sys.stderr)


if __name__ == "__main__":
    main()
