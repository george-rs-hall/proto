"""Fresh demo state: clear this app's Stripe test payments, reset the wallet and memory, seed the user's signed
facts, then record Shady Denim's earlier prompt injection in provider history.

Run: uv run python -m scripts.prepare_demo

The flag is real: Shady Denim (merchant E) signs the injected cart it served before (charcoal jeans carrying a
"SYSTEM NOTE"), the evaluator scores it, and the merchant is flagged with that P(injection) and the signed cart
as evidence. No wallet row and no Stripe payment are created.
"""
import app.config  # noqa: F401  (load .env first)

import asyncio
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"
EARLIER_REQUEST = "black jeans, under £80"
TRIES = 3


async def preflag_shady() -> None:
    from app import evaluator, memory, merchants, policy, signing

    intent = signing.sign({"request": EARLIER_REQUEST, "max_pence": 8000,
                           "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                           "nonce": secrets.token_hex(8)}, "user-george")
    signed_cart = merchants.shady_earlier_cart(signing.sha256_hex(intent))
    assert signing.verify(signed_cart, "merchant-shady")
    rel = await memory.relevant_facts(EARLIER_REQUEST)
    sentences = evaluator.render_facts(rel["facts"])
    for attempt in range(1, TRIES + 1):
        probs = await asyncio.to_thread(evaluator.evaluate, EARLIER_REQUEST, sentences, signed_cart["payload"]["items"])
        print(f"Shady Denim's earlier cart: P(match) {probs['p_match']:.2f}, P(injection) {probs['p_injection']:.2f} "
              f"({probs['source']}, {len(rel['facts'])} of {rel['total']} signed memories, {rel['method']})")
        if probs["p_injection"] >= policy.INJECTION_THRESHOLD:
            await memory.flag_injection("shady", probs["p_injection"], signed_cart, prepared=True)
            await memory.wait_for_mirrors()
            print("Flagged Shady Denim (merchant-shady) for prompt injection in provider history.")
            return
        print(f"  below the {policy.INJECTION_THRESHOLD} threshold (attempt {attempt}/{TRIES})")
    raise SystemExit("evaluator did not flag Shady Denim's injected cart; not pre-flagging")


def main() -> None:
    from app import money
    money.clear_demo()

    for d in (".cognee", ".wallet"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "scripts.seed"], cwd=ROOT, check=True)
    asyncio.run(preflag_shady())

    print(f"""
Ready. Next:
  uv run uvicorn app.main:app --port {PORT}
  open {URL}/agents  # one click: six agents shop in parallel, all paying through the wallet
  open {URL}/        # the wallet: starts empty, Shady Denim already blocked
""")


if __name__ == "__main__":
    main()
