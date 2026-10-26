"""The catalogue of analysis tools: name, description, argument schema and function.

One definition, two ways to serve it:
- control_tower/agent_tools.py wraps each one as a LangChain tool for the agent;
- control_tower_mcp/server.py publishes each one as an MCP tool, for any MCP client.

Everything here is read-only and framework-free (pydantic only). The action tools,
which need a human approval, are not here: they belong to the agent.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import tools as T

MAX_TOOL_OUTPUT_CHARS = 6000

SITE_CODES = "TLS, BOD, EVR, LEH, CHT, MEL, NTE (France), CAS, TUN (Maghreb)"


class Args(BaseModel):
    """Base of every tool schema: an unknown argument is an error sent back to the model, not silently
    dropped (a date filter given outside `filters` would otherwise compute over the whole period)."""
    model_config = ConfigDict(extra="forbid")


class Filters(Args):
    """Optional filters. Every list means 'any of these values'."""
    origin: list[str] | None = Field(None, description=f"Origin site codes: {SITE_CODES}")
    destination: list[str] | None = Field(None, description="Destination codes (sites or customers such as C-HAM)")
    mode: list[str] | None = Field(None, description="express, messagerie, routier, aerien, maritime, ferroviaire, navette")
    carrier_id: list[str] | None = Field(None, description="Carrier ids, e.g. RAF, ERL, TLSUD")
    carrier_type: list[str] | None = Field(None, description="integrateur, expressiste, messagerie, routier, commissionnaire, ferroviaire, petit_local")
    flow_type: list[str] | None = Field(None, description="intersite or client")
    product_type: list[str] | None = Field(None, description="serie or commande")
    lane: list[str] | None = Field(None, description="Lane ids 'ORIGIN-DESTINATION', e.g. TLS-BOD")
    start: str | None = Field(None, description="First ship date included, YYYY-MM-DD")
    end: str | None = Field(None, description="Last ship date included, YYYY-MM-DD")


def _clean(filters: Filters | dict | None) -> dict | None:
    if filters is None:
        return None
    d = filters.model_dump() if isinstance(filters, BaseModel) else dict(filters)
    return {k: v for k, v in d.items() if v not in (None, [], "")} or None


def _run(fn, **kwargs) -> str:
    """Call a tool and return compact JSON; errors go back to the model so it can fix its call."""
    try:
        out = fn(**kwargs)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + '... [truncated: ask a narrower question or use filters]"}'
    return text


@dataclass(frozen=True)
class ToolSpec:
    """One analysis tool. `run` takes the validated arguments and returns the JSON text the model reads."""
    name: str
    description: str
    schema: type[Args]
    run: Callable[..., str]


# ---------------------------------------------------------------- analysis
class NoArgs(Args):
    pass


class KpiArgs(Args):
    metric: Literal[T.METRICS] = Field(..., description="Which KPI")  # type: ignore[valid-type]
    group_by: list[str] | None = Field(None, description="Columns: origin, destination, mode, carrier_id, carrier_name, carrier_type, flow_type, product_type, product_family, lane, month, week")
    filters: Filters | None = None


class SqlArgs(Args):
    query: str = Field(..., description="One read-only SELECT on tables shipments, orders, lanes, rate_card, carriers, emission_factors, spend_carbon_factor, sites, customers, product_families")


class PremiumArgs(Args):
    mode: Literal["express", "aerien"] = "express"
    filters: Filters | None = None


class FiltersOnly(Args):
    filters: Filters | None = None


class ShuttleArgs(Args):
    sites: list[str] = Field(..., description="French sites that would send their Casablanca shipments via Toulouse, e.g. ['BOD', 'CHT']")


class ShiftArgs(Args):
    origin: str
    destination: str
    from_mode: str
    to_mode: str


class CarbonArgs(Args):
    group_by: str = Field("mode", description="mode, origin, carrier_type, flow_type, month...")


ANALYSIS_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="data_overview", schema=NoArgs,
        description="Start here: period, volumes, sites, modes and data-quality issues (duplicates, missing weights, misspelled carrier names).",
        run=lambda: _run(T.data_overview)),
    ToolSpec(
        name="kpi", schema=KpiArgs,
        description="Standard transport KPI with fixed definitions, optionally grouped and filtered. Prefer this to SQL for costs, volumes, punctuality, express share and CO2.",
        run=lambda metric, group_by=None, filters=None: _run(T.kpi, metric=metric, group_by=group_by, filters=_clean(filters))),
    ToolSpec(
        name="run_sql", schema=SqlArgs,
        description="Last resort: one read-only SQL SELECT on the raw tables (not cleaned: duplicates and misspelled carrier names remain).",
        run=lambda query: _run(T.run_sql, query=query)),
    ToolSpec(
        name="analyze_premium_transport", schema=PremiumArgs,
        description="Split express or air shipments into avoidable (a cheaper mode would have been on time), forced by production delays, tight promise, or business rule; with estimated savings by site, carrier and lane.",
        run=lambda mode="express", filters=None: _run(T.analyze_premium_transport, mode=mode, filters=_clean(filters))),
    ToolSpec(
        name="production_delay_impact", schema=FiltersOnly,
        description="Where production is late (by site and product type) and how much premium transport it forces.",
        run=lambda filters=None: _run(T.production_delay_impact, filters=_clean(filters))),
    ToolSpec(
        name="find_consolidation", schema=FiltersOnly,
        description="Road/parcel shipments on the same lane and week that could have left together in one truck and still been on time; estimated savings.",
        run=lambda filters=None: _run(T.find_consolidation, filters=_clean(filters))),
    ToolSpec(
        name="simulate_shuttle_extension", schema=ShuttleArgs,
        description="What-if: other French sites send their Casablanca shipments by road to Toulouse, then on the Wednesday shuttle. Savings in EUR and CO2.",
        run=lambda sites: _run(T.simulate_shuttle_extension, sites=sites)),
    ToolSpec(
        name="simulate_mode_shift", schema=ShiftArgs,
        description="What-if: shipments of one lane move from one mode to another when the lead time allows. Savings in EUR and CO2.",
        run=lambda origin, destination, from_mode, to_mode: _run(T.simulate_mode_shift, origin=origin, destination=destination, from_mode=from_mode, to_mode=to_mode)),
    ToolSpec(
        name="carrier_performance", schema=FiltersOnly,
        description="Cost per kg and punctuality by carrier, lane and mode, with the last 3 months vs before.",
        run=lambda filters=None: _run(T.carrier_performance, filters=_clean(filters))),
    ToolSpec(
        name="detect_carrier_degradation", schema=NoArgs,
        description="Carriers whose own punctuality (vs standard transit time) dropped over the last 3 months, by lane.",
        run=lambda: _run(T.detect_carrier_degradation)),
    ToolSpec(
        name="lane_price_benchmark", schema=NoArgs,
        description="Carriers charging more than the other carriers on the same lane and mode, weight-adjusted; overspend in EUR.",
        run=lambda: _run(T.lane_price_benchmark)),
    ToolSpec(
        name="compare_carbon_methods", schema=CarbonArgs,
        description="Spend-based vs activity-based CO2e, grouped, with the reason they differ.",
        run=lambda group_by="mode": _run(T.compare_carbon_methods, group_by=group_by)),
]
