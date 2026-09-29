"""
Receives GitHub 'pull_request' webhook events and runs the reviewer
automatically, posting the result as a PR comment.

Run:
    uvicorn webhook:app --reload --port 8000

Then point a GitHub webhook (repo Settings -> Webhooks) at this
server's public URL + /webhook, content type application/json,
with the same secret as GITHUB_WEBHOOK_SECRET below.

For local testing without deploying anywhere, expose your local
port with ngrok or smee.io:
    ngrok http 8000
and use the printed https URL as the webhook's payload URL.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from agent import review_pr

load_dotenv()

app = FastAPI()

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

# Only react to these PR actions. 'opened' = new PR, 'synchronize' = new
# commits pushed to an existing PR. Reviewing on every push keeps the
# comment current as the PR changes.
TRIGGER_ACTIONS = {"opened", "synchronize", "reopened"}


def _verify_signature(raw_body: bytes, signature: str | None) -> None:
    if not WEBHOOK_SECRET:
        # No secret configured -> refuse to run unverified in anything
        # that looks like production. Fine to skip only for quick local
        # experiments, but set GITHUB_WEBHOOK_SECRET before exposing this
        # publicly.
        raise HTTPException(500, "GITHUB_WEBHOOK_SECRET is not set on the server")
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(401, "Missing or malformed signature")

    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Signature mismatch")


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    raw_body = await request.body()
    _verify_signature(raw_body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event={x_github_event}"}

    payload = await request.json()
    action = payload.get("action")
    if action not in TRIGGER_ACTIONS:
        return {"status": "ignored", "reason": f"action={action}"}

    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]
    pr_number = payload["pull_request"]["number"]

    # Run the review in the background so GitHub's webhook delivery
    # (which times out after ~10s) gets an immediate 200, instead of
    # blocking on the full LLM + tool-calling loop.
    background_tasks.add_task(_run_and_log, owner, repo, pr_number)

    return {"status": "queued", "owner": owner, "repo": repo, "pr": pr_number}


async def _run_and_log(owner: str, repo: str, pr_number: int):
    print(f"[webhook] reviewing {owner}/{repo}#{pr_number}")
    try:
        result = await review_pr(owner, repo, pr_number, post_comment=True)
        print(f"[webhook] done {owner}/{repo}#{pr_number}:\n{result[:500]}")
    except Exception as e:
        print(f"[webhook] FAILED {owner}/{repo}#{pr_number}: {e}")


@app.get("/health")
async def health():
    return {"status": "ok"}
