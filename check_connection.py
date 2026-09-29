"""
Run this first. Confirms your PAT + the GitHub MCP server work before
you build anything on top of it.

    python check_connection.py
"""
import asyncio

from dotenv import load_dotenv

from mcp_client import github_connection

load_dotenv()


async def main():
    conn = await github_connection().connect()
    try:
        tools = await conn.list_tools()
        print(f"Connected. {len(tools)} tools available:\n")
        for t in tools:
            print(f"  - {t.name}: {(t.description or '').strip()[:90]}")

        print("\nCalling get_me...")
        text, is_error = await conn.call("get_me", {})
        print("ERROR:" if is_error else "OK:", text[:500])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
