"""
The agent loop: gives the LLM the GitHub MCP tools and lets it drive
the review, turn by turn, until it produces a final answer.

Which LLM runs the loop is controlled by LLM_PROVIDER in .env:
  LLM_PROVIDER=anthropic   -> Claude (paid, best quality)
  LLM_PROVIDER=openai_compat -> any OpenAI-compatible endpoint, e.g. Groq's
                                 free tier (see .env.example)
"""
from __future__ import annotations

import os

from mcp_client import github_connection
from prompts import REVIEWER_SYSTEM_PROMPT
from llm_backends import run_anthropic_loop, run_openai_compatible_loop

# Read-only allowlist. Nothing here can modify the repo or post anything.
# Matches the current github-mcp-server tool set (checked via
# check_connection.py) — GitHub consolidated the old get_pull_request /
# get_pull_request_diff / get_pull_request_files / etc. into a single
# pull_request_read tool with a `method` argument (get, get_diff, get_files,
# get_status, get_review_comments, get_reviews, get_comments, get_check_runs).
# The LLM sees the full method list from the tool's own schema at runtime,
# so nothing else needs to change if GitHub adds more methods later —
# only re-check this set if a whole *tool name* changes.
ALLOWED_TOOLS = {
    "get_me",
    "pull_request_read",
    "get_file_contents",
    "list_pull_requests",
    "search_code",
}

# Posting a comment is the ONE write action this project allows — it leaves
# feedback like a human reviewer would, it does not touch code. It is never
# offered to the LLM as a tool in the loop above; review_pr() calls it
# directly, deterministically, exactly once, after the review text is final.
# GitHub's MCP server has used different names for this across versions —
# try each until one matches what your server exposes (check_connection.py
# will show you the real name if none of these hit).
COMMENT_TOOL_CANDIDATES = ["add_issue_comment", "create_issue_comment"]


async def _post_comment(conn, available: set[str], owner: str, repo: str, pr_number: int, body: str) -> str:
    """Post `body` as a PR comment. Tries each candidate tool name in turn."""
    for name in COMMENT_TOOL_CANDIDATES:
        if name not in available:
            continue
        text, is_error = await conn.call(
            name, {"owner": owner, "repo": repo, "issue_number": pr_number, "body": body}
        )
        if not is_error:
            return f"Posted comment via {name}."
        print(f"[post_comment] {name} failed: {text[:200]}")
    return (
        "Could not post comment: no working tool found among "
        f"{COMMENT_TOOL_CANDIDATES}. Check check_connection.py output for the "
        "real tool name and update COMMENT_TOOL_CANDIDATES."
    )


async def review_pr(
    owner: str,
    repo: str,
    pr_number: int,
    max_turns: int = 15,
    post_comment: bool = False,
) -> str:
    conn = await github_connection().connect()
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

    try:
        all_tools = await conn.list_tools()
        available = {t.name for t in all_tools}
        allow = ALLOWED_TOOLS & available
        if not allow:
            raise RuntimeError(
                "None of ALLOWED_TOOLS matched the server's tools. "
                f"Server has: {sorted(available)}"
            )
        mcp_tools = [t for t in all_tools if t.name in allow]
        user_message = f"Review pull request #{pr_number} in {owner}/{repo}."

        if provider == "anthropic":
            review_text = await run_anthropic_loop(
                conn, mcp_tools, REVIEWER_SYSTEM_PROMPT, user_message, max_turns
            )
        elif provider == "openai_compat":
            review_text = await run_openai_compatible_loop(
                conn, mcp_tools, REVIEWER_SYSTEM_PROMPT, user_message, max_turns
            )
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")

        if post_comment:
            status = await _post_comment(conn, available, owner, repo, pr_number, review_text)
            print(f"[review_pr] {status}")

        return review_text
    finally:
        await conn.close()
