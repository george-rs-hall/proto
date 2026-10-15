# Signed-intent check for agent payments

**Problem:** when an AI agent pays for something, today's payment systems check the amount, the merchant and that the agent may spend, but nothing checks that what's being bought is what you asked for.

**Fix:** you sign your request, the merchant signs its cart and undo terms, and the wallet checks the cart against your request before any money moves, needing more certainty the harder the purchase is to undo.

## Run it

Prerequisites: [uv](https://docs.astral.sh/uv/) and a `.env` in the repo root with:

- `STRIPE_SECRET_KEY`: a Stripe test-mode key (`sk_test_…`)
- `TYPESAFE_API_KEY`: for the Jev cart evaluator
- `LLM_API_KEY`: Anthropic key, used by the shopping agents, Cognee and the evaluator fallback

```sh
uv run python -m scripts.prepare_demo      # reset local state, sign the user's facts, pre-flag Shady Denim
uv run uvicorn app.main:app --port 8000    # wallet, shops, chat and agents
```

- `http://127.0.0.1:8000/chat`: say "Hey, go buy me some black jeans". Three Claude agents shop at three stores at once; the wallet checks each signed cart and pays, asks, declines or blocks.
- `http://127.0.0.1:8000/`: the wallet. History, undo, and why each cart was stopped.

`prepare_demo` only resets local state (`.wallet`, `.cognee`); it never touches Stripe, so earlier test payments stay there. It seeds the signed memories and has the evaluator score Shady Denim's earlier injected cart, so that shop is already flagged for prompt injection. Run it with the server stopped: only one process writes to Cognee at a time.

To let Claude Code shop through the wallet, add the MCP server and paste the signed-intent token from the UI:

```sh
claude mcp add wallet --scope project -e WALLET_URL=http://127.0.0.1:8000 -- uv --directory /path/to/repo run python -m app.mcp_server
```
