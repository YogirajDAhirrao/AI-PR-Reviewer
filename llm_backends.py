"""
Two implementations of the same tool-calling loop: one for Anthropic's
native API, one for any OpenAI-compatible API (Groq, OpenRouter, etc.).

The outer shape is identical for both — call the model, if it wants a
tool, call it via MCP and feed the result back, repeat until it gives a
final text answer or max_turns runs out. Only the request/response
*format* differs between providers, which is why they're kept as
separate functions instead of forcing one shared code path.
"""
from __future__ import annotations

import os


async def run_anthropic_loop(conn, mcp_tools, system: str, user_message: str, max_turns: int) -> str:
    import anthropic

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    client = anthropic.AsyncAnthropic()

    tools = [
        {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
        for t in mcp_tools
    ]
    messages = [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        response = await client.messages.create(
            model=model, max_tokens=4096, system=system, tools=tools, messages=messages
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[turn {turn}] calling {block.name}({block.input})")
            text, is_error = await conn.call(block.name, block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text[:20000],
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return "Stopped: reached max_turns without a final answer."


async def run_openai_compatible_loop(conn, mcp_tools, system: str, user_message: str, max_turns: int) -> str:
    """
    Works with any OpenAI-compatible chat-completions endpoint that
    supports tool calling — Groq, OpenRouter, Together, local vLLM, etc.
    Pick the provider via env vars (see .env.example / README).
    """
    from openai import AsyncOpenAI

    base_url = os.environ["OPENAI_COMPAT_BASE_URL"]
    api_key = os.environ["OPENAI_COMPAT_API_KEY"]
    model = os.environ.get("OPENAI_COMPAT_MODEL", "llama-3.3-70b-versatile")

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_tools
    ]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    for turn in range(max_turns):
        response = await client.chat.completions.create(
            model=model, messages=messages, tools=tools, max_tokens=4096
        )
        choice = response.choices[0]
        messages.append(choice.message.model_dump(exclude_none=True))

        tool_calls = choice.message.tool_calls
        if not tool_calls:
            return choice.message.content or ""

        for call in tool_calls:
            import json

            args = json.loads(call.function.arguments or "{}")
            print(f"[turn {turn}] calling {call.function.name}({args})")
            text, is_error = await conn.call(call.function.name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": (f"ERROR: {text}" if is_error else text)[:20000],
                }
            )

    return "Stopped: reached max_turns without a final answer."
