"""The MCP server, tested through a real MCP client (in memory, then over stdio and Streamable HTTP)."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.memory import create_connected_server_and_client_session

from control_tower import catalog
from control_tower_mcp.server import build_server

ROOT = Path(__file__).resolve().parents[1]

# One realistic call per tool, as the agent makes them.
CALLS = {
    "data_overview": {},
    "kpi": {"metric": "on_time_rate", "filters": {"start": "2026-04-01", "end": "2026-06-30"}},
    "run_sql": {"query": "SELECT mode, COUNT(*) AS n FROM shipments GROUP BY mode"},
    "analyze_premium_transport": {"mode": "aerien", "filters": {"origin": ["CAS"]}},
    "production_delay_impact": {"filters": {"origin": ["BOD"]}},
    "find_consolidation": {},
    "simulate_shuttle_extension": {"sites": ["BOD", "CHT"]},
    "simulate_mode_shift": {"origin": "BOD", "destination": "TLS", "from_mode": "express", "to_mode": "messagerie"},
    "carrier_performance": {"filters": {"lane": ["TLS-BOD"]}},
    "detect_carrier_degradation": {},
    "lane_price_benchmark": {},
    "compare_carbon_methods": {"group_by": "mode"},
}


def with_client(check):
    """Run `check(session)` against the server, connected in memory."""
    async def go():
        async with create_connected_server_and_client_session(build_server()._mcp_server) as session:
            return await check(session)
    return asyncio.run(go())


def test_publishes_the_catalogue_read_only_and_no_action_tool():
    tools = with_client(lambda s: s.list_tools()).tools
    assert [t.name for t in tools] == [t.name for t in catalog.ANALYSIS_TOOLS] == list(CALLS)
    assert all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools)
    assert not {"draft_email", "save_transport_plan"} & {t.name for t in tools}
    kpi = next(t for t in tools if t.name == "kpi")
    assert kpi.inputSchema["additionalProperties"] is False and "metric" in kpi.inputSchema["required"]


def test_every_tool_answers_exactly_as_in_the_agent():
    """Same catalogue, same result: the migration to MCP cannot change a figure."""
    async def check(session):
        for name, args in CALLS.items():
            result = await session.call_tool(name, args)
            spec = next(t for t in catalog.ANALYSIS_TOOLS if t.name == name)
            direct = spec.run(**spec.schema.model_validate(args).__dict__)
            assert not result.isError, (name, result.content)
            assert result.content[0].text == direct, name
    with_client(check)


@pytest.mark.parametrize("name, args, expected", [
    ("kpi", {"metric": "on_time_rate", "start": "2026-04-01"}, "start"),       # misplaced filter
    ("detect_carrier_degradation", {"carrier_id": "ERL"}, "carrier_id"),       # seen in a real run
    ("kpi", {"metric": "nope"}, "metric"),                                      # invalid value
    ("kpi", {"metric": "cost_eur", "group_by": ["nope"]}, "allowed"),           # the tool itself fails
])
def test_errors_reach_the_client_as_errors(name, args, expected):
    result = with_client(lambda s: s.call_tool(name, args))
    assert result.isError and expected in result.content[0].text


def test_resources_and_prompt():
    async def check(session):
        uris = {str(r.uri) for r in (await session.list_resources()).resources}
        rules = json.loads((await session.read_resource("controltower://business-rules")).contents[0].text)
        dictionary = (await session.read_resource("controltower://data-dictionary")).contents[0].text
        prompt = await session.get_prompt("transport-analysis", {"question": "D'où vient l'express évitable ?"})
        return uris, rules, dictionary, prompt.messages[0].content.text
    uris, rules, dictionary, prompt = with_client(check)
    assert uris == {"controltower://data-dictionary", "controltower://business-rules"}
    assert rules["shuttle"]["lanes"] == ["TLS-CAS"] and rules["shuttle"]["excluded_families"] == ["FAM-OPT"]
    assert "shipments" in dictionary
    assert prompt.endswith("Question : D'où vient l'express évitable ?") and "**Chiffres clés**" in prompt


async def _list_and_call(session: ClientSession) -> tuple[int, str]:
    await session.initialize()
    tools = (await session.list_tools()).tools
    result = await session.call_tool("kpi", CALLS["kpi"])
    return len(tools), json.loads(result.content[0].text)["summary"]


def test_stdio_transport():
    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "control_tower_mcp"], cwd=str(ROOT))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            return await _list_and_call(session)
    assert asyncio.run(go()) == (12, "on_time_rate = 0.826")


def test_streamable_http_transport():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "control_tower_mcp", "--http", "--port", str(port)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        async def go():
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _), \
                    ClientSession(read, write) as session:
                return await _list_and_call(session)
        assert asyncio.run(go()) == (12, "on_time_rate = 0.826")
    finally:
        server.terminate()
        server.wait(timeout=10)
