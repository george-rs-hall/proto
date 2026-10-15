# Signed-intent check for agent payments

**Problem:** when an AI agent pays for something, today's payment systems check the amount, the merchant and that the agent may spend, but nothing checks that what's being bought is what you asked for.

**Fix:** you sign your request, the merchant signs its cart and undo terms, and the wallet checks the cart against your request before any money moves, needing more certainty the harder the purchase is to undo.

## Run it

Prerequisites: [uv](https://docs.astral.sh/uv/) and a `.env` in the repo root with:

- `STRIPE_SECRET_KEY`: a Stripe test-mode key (`sk_test_…`)
- `TYPESAFE_API_KEY`: for the Jev cart evaluator
- `LLM_API_KEY`: Anthropic key, used by Cognee and the evaluator fallback

```sh
uv run python -m scripts.seed              # sign the user's facts into Cognee (resets .cognee/)
uv run uvicorn app.main:app --port 8000    # wallet, merchants and UI
open http://127.0.0.1:8000                 # sign a request, watch decisions arrive
uv run python -m scripts.agent_loop        # scripted agent: requests payment for every offer
```

Seed before starting the server: only one process writes to Cognee at a time.

To let Claude Code shop through the wallet, add the MCP server and paste the signed-intent token from the UI:

```sh
claude mcp add wallet --scope project -e WALLET_URL=http://127.0.0.1:8000 -- uv --directory /path/to/repo run python -m app.mcp_server
```
