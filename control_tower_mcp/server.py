"""Control Tower MCP server: the analysis tools of the agent, for any MCP client.

    python -m control_tower_mcp                          # stdio: the client starts the server itself
    python -m control_tower_mcp --http                   # Streamable HTTP on http://127.0.0.1:8000/mcp
    python -m control_tower_mcp --http --host 0.0.0.0 --port 9000

What it publishes (the three MCP primitives):
- tools: the 12 read-only analysis tools of control_tower/catalog.py, the same
  definitions the agent uses; an unknown argument is an error, never dropped;
- resources: the data dictionary and the business rules, readable without a tool call;
- prompt: the agent's instructions and answer format, around a question.

The action tools (e-mail draft, transport plan) are not published: they need a
human approval, which is the agent's job, not a service for any client.

Configuration: CT_DB points to another database (default data/transport.db,
built by scripts/generate_data.py).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import ToolAnnotations
from pydantic import ConfigDict

from control_tower import catalog, rules
from control_tower.prompts import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[1]

INSTRUCTIONS = """Transport analysis tools for AeroParts, a multi-site aerospace manufacturer (simulated data, \
54,000 shipments, 2025-2026). Every tool is read-only and deterministic, and returns summary, data, method and \
caveats. Start with data_overview if you do not know the data; prefer the specific tools to run_sql; quote the \
figures the tools return, with their period and unit. Site codes, modes and filters are described in the \
resource controltower://data-dictionary; business rules in controltower://business-rules."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _error(text: str) -> str | None:
    """The error message of a tool result, if the tool failed."""
    try:
        body = json.loads(text)
    except ValueError:  # a truncated result is not valid JSON, and not an error
        return None
    return body.get("error") if isinstance(body, dict) else None


def as_mcp_tool(spec: catalog.ToolSpec) -> Tool:
    """Publish one catalogue tool.

    FastMCP usually builds the argument model from the function signature, and that model
    ignores unknown arguments. Here the model is the catalogue's own schema (extra fields
    forbidden), so a misplaced argument is reported to the client as in the agent.
    """
    arg_model = type(f"{spec.schema.__name__}Arguments", (spec.schema, ArgModelBase),
                     {"model_config": ConfigDict(extra="forbid", arbitrary_types_allowed=True)})

    def run(**arguments) -> str:
        text = spec.run(**arguments)
        if message := _error(text):
            raise ToolError(message)  # sent back with isError, so the model knows and can fix its call
        return text

    return Tool(fn=run, name=spec.name, title=spec.name.replace("_", " ").capitalize(),
                description=spec.description, parameters=spec.schema.model_json_schema(),
                fn_metadata=FuncMetadata(arg_model=arg_model), is_async=False, context_kwarg=None,
                annotations=READ_ONLY)


def build_server(host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    mcp = FastMCP("control-tower", instructions=INSTRUCTIONS, host=host, port=port,
                  tools=[as_mcp_tool(t) for t in catalog.ANALYSIS_TOOLS])

    @mcp.resource("controltower://data-dictionary", name="data-dictionary", mime_type="text/markdown",
                  description="Tables, columns, codes (sites, modes, carriers) and how the data was cleaned.")
    def data_dictionary() -> str:
        return (ROOT / "docs" / "data.md").read_text(encoding="utf-8")

    @mcp.resource("controltower://business-rules", name="business-rules", mime_type="application/json",
                  description="Rules a transport referent knows: the Toulouse-Casablanca shuttle, premium modes, "
                              "the Maghreb air rule, consolidable modes, minimum sample size.")
    def business_rules() -> str:
        shuttle = {**rules.SHUTTLE, "lanes": sorted("-".join(lane) for lane in rules.SHUTTLE["lanes"]),
                   "excluded_families": sorted(rules.SHUTTLE["excluded_families"])}
        return json.dumps({"shuttle": shuttle, "premium_modes": list(rules.PREMIUM_MODES),
                           "maghreb_sites_customer_flows_by_air": sorted(rules.MAGHREB_SITES),
                           "consolidable_modes": list(rules.CONSOLIDABLE_MODES),
                           "min_sample_per_carrier_lane": rules.MIN_SAMPLE}, ensure_ascii=False, indent=2)

    @mcp.prompt(name="transport-analysis", title="Transport analysis",
                description="The Control Tower agent's instructions and answer format, around your question.")
    def transport_analysis(question: str) -> str:
        return f"{SYSTEM_PROMPT}\n\nQuestion : {question}"

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--http", action="store_true", help="serve over Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default 127.0.0.1, local only)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    args = parser.parse_args()
    build_server(args.host, args.port).run(transport="streamable-http" if args.http else "stdio")


if __name__ == "__main__":
    main()
