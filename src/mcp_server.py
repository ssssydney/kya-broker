"""stdio MCP server exposing the broker as 4 tools for Claude Code.

Register in Claude Code's MCP config with:

    {
      "mcpServers": {
        "kya-broker": {
          "command": "kya-broker-mcp"
        }
      }
    }

or invoke directly: `python -m src.mcp_server`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auditor import AuditContext
from .broker import Broker, BrokerError

logger = logging.getLogger("kya_broker.mcp")


TOOL_DEFS: list[Tool] = [
    Tool(
        name="propose_intent",
        description=(
            "Submit a payment intent for audit and (if approved) execution. "
            "Call this when the agent needs to pay a merchant on behalf of the user."
        ),
        inputSchema={
            "type": "object",
            "required": ["merchant", "amount_usd", "rationale", "estimated_actual_cost_usd"],
            "properties": {
                "merchant": {
                    "type": "string",
                    "description": "Merchant slug from policy.yaml allowlist (e.g. 'vast.ai').",
                },
                "amount_usd": {
                    "type": "number",
                    "minimum": 0.01,
                    "description": "Amount to charge, in USD. Should be >= estimated_actual_cost_usd.",
                },
                "rationale": {
                    "type": "string",
                    "minLength": 10,
                    "description": "Why this payment is needed. The auditor cross-checks against conversation context.",
                },
                "estimated_actual_cost_usd": {
                    "type": "number",
                    "minimum": 0.01,
                    "description": "Best estimate of the true underlying cost (e.g. vast GPU-hours * rate).",
                },
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths or URLs backing the rationale.",
                },
                "context": {
                    "type": "object",
                    "description": "Optional audit context the agent wants to pass through.",
                    "properties": {
                        "conversation_excerpt": {"type": "string"},
                        "cited_files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content_excerpt": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    ),
    Tool(
        name="get_status",
        description="Get the current state of an intent by intent_id.",
        inputSchema={
            "type": "object",
            "required": ["intent_id"],
            "properties": {
                "intent_id": {"type": "string"},
            },
        },
    ),
    Tool(
        name="get_history",
        description="List recent intents in reverse-chronological order.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
        },
    ),
    Tool(
        name="check_balance",
        description=(
            "Return MetaMask USDC balance (when Chrome is running), vast.ai credit (when "
            "cached), today's and this month's spend, and remaining caps."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]


def _result(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _error(code: str, message: str) -> list[TextContent]:
    return _result({"error": {"code": code, "message": message}})


def _make_server() -> Server:
    server: Server = Server("kya-broker")
    broker = Broker()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return TOOL_DEFS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "propose_intent":
                ctx_raw = arguments.pop("context", None) or {}
                context = AuditContext(
                    conversation_excerpt=ctx_raw.get("conversation_excerpt", ""),
                    cited_files=ctx_raw.get("cited_files", []),
                )
                resp = await broker.propose_intent(arguments, context)
                return _result(resp.to_dict())

            if name == "get_status":
                intent_id = arguments["intent_id"]
                data = broker.status(intent_id)
                if data is None:
                    return _error("not_found", f"no intent {intent_id}")
                return _result(data)

            if name == "get_history":
                limit = int(arguments.get("limit", 50))
                return _result(broker.history(limit=limit))

            if name == "check_balance":
                return _result(broker.check_balance())

            return _error("unknown_tool", f"unknown tool {name!r}")
        except BrokerError as e:
            return _error("broker_error", str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("unhandled error in tool %s", name)
            return _error("internal_error", f"{type(e).__name__}: {e}")

    return server


async def _run_async() -> None:
    server = _make_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="kya-broker",
                server_version="0.3.1",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(_run_async())


if __name__ == "__main__":
    main()
