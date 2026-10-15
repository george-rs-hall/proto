"""Fresh demo state: reset the wallet and memory, seed the user's facts, then build some wallet history.

Run: uv run python -m scripts.prepare_demo [--no-history]

History = the scripted agent_loop run (a purchase, an undone hold, a final sale you approve, declines and a
shop flagged for prompt injection). It starts the server for that and stops it afterwards.
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000
URL = f"http://127.0.0.1:{PORT}"


def run(*args: str) -> None:
    subprocess.run([sys.executable, "-m", *args], cwd=ROOT, check=True)


def build_history() -> None:
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                httpx.get(f"{URL}/api/audit", timeout=2).raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise SystemExit(f"server didn't start on port {PORT} (is something else using it?)")
        run("scripts.agent_loop", "--delay", "0.2")
        rows = httpx.get(f"{URL}/api/audit", timeout=30).json()["rows"]
        for row in rows:
            if row["decision"] == "ask":
                r = httpx.post(f"{URL}/api/decide", json={"row_id": row["id"], "approve": True}, timeout=60).json()
                print(f"Approved {r.get('merchant_name') or r.get('merchant_id')}: {r.get('decision')} {r.get('payment_id') or ''}")
    finally:
        server.terminate()
        server.wait(timeout=10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-history", action="store_true", help="only reset and seed")
    args = ap.parse_args()

    from app import money
    money.clear_demo()

    for d in (".cognee", ".wallet"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    run("scripts.seed")
    if not args.no_history:
        build_history()

    print(f"""
Ready. Next:
  uv run uvicorn app.main:app --port {PORT}
  open {URL}/chat    # say "Hey, go buy me some black jeans"
  open {URL}/        # the wallet: history, undo, why each cart was stopped
""")


if __name__ == "__main__":
    main()
