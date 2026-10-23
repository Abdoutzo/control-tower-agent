"""Expose the analysis tools to the language model.

Two kinds of tools:
- analysis tools (read-only): the agent may call them freely;
- action tools (they produce a deliverable): the graph pauses and asks a
  human to approve before they run.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from . import tools as T

MAX_TOOL_OUTPUT_CHARS = 6000
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs"

SITE_CODES = "TLS, BOD, EVR, LEH, CHT, MEL, NTE (France), CAS, TUN (Maghreb)"


class Args(BaseModel):
    """Base of every tool schema: an unknown argument is an error sent back to the model, not silently
    dropped (a date filter given outside `filters` would otherwise compute over the whole period)."""
    model_config = ConfigDict(extra="forbid")


class Filters(Args):
    """Optional filters. Every list means 'any of these values'."""
    origin: Optional[list[str]] = Field(None, description=f"Origin site codes: {SITE_CODES}")
    destination: Optional[list[str]] = Field(None, description="Destination codes (sites or customers such as C-HAM)")
    mode: Optional[list[str]] = Field(None, description="express, messagerie, routier, aerien, maritime, ferroviaire, navette")
    carrier_id: Optional[list[str]] = Field(None, description="Carrier ids, e.g. RAF, ERL, TLSUD")
    carrier_type: Optional[list[str]] = Field(None, description="integrateur, expressiste, messagerie, routier, commissionnaire, ferroviaire, petit_local")
    flow_type: Optional[list[str]] = Field(None, description="intersite or client")
    product_type: Optional[list[str]] = Field(None, description="serie or commande")
    lane: Optional[list[str]] = Field(None, description="Lane ids 'ORIGIN-DESTINATION', e.g. TLS-BOD")
    start: Optional[str] = Field(None, description="First ship date included, YYYY-MM-DD")
    end: Optional[str] = Field(None, description="Last ship date included, YYYY-MM-DD")


def _clean(filters: Optional[Filters | dict]) -> dict | None:
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


# ---------------------------------------------------------------- analysis
class NoArgs(Args):
    pass


class KpiArgs(Args):
    metric: Literal[T.METRICS] = Field(..., description="Which KPI")  # type: ignore[valid-type]
    group_by: Optional[list[str]] = Field(None, description="Columns: origin, destination, mode, carrier_id, carrier_name, carrier_type, flow_type, product_type, product_family, lane, month, week")
    filters: Optional[Filters] = None


class SqlArgs(Args):
    query: str = Field(..., description="One read-only SELECT on tables shipments, orders, lanes, rate_card, carriers, emission_factors, spend_carbon_factor, sites, customers, product_families")


class PremiumArgs(Args):
    mode: Literal["express", "aerien"] = "express"
    filters: Optional[Filters] = None


class FiltersOnly(Args):
    filters: Optional[Filters] = None


class ShuttleArgs(Args):
    sites: list[str] = Field(..., description="French sites that would send their Casablanca shipments via Toulouse, e.g. ['BOD', 'CHT']")


class ShiftArgs(Args):
    origin: str
    destination: str
    from_mode: str
    to_mode: str


class CarbonArgs(Args):
    group_by: str = Field("mode", description="mode, origin, carrier_type, flow_type, month...")


ANALYSIS_TOOLS = [
    StructuredTool.from_function(
        name="data_overview", args_schema=NoArgs,
        description="Start here: period, volumes, sites, modes and data-quality issues (duplicates, missing weights, misspelled carrier names).",
        func=lambda: _run(T.data_overview)),
    StructuredTool.from_function(
        name="kpi", args_schema=KpiArgs,
        description="Standard transport KPI with fixed definitions, optionally grouped and filtered. Prefer this to SQL for costs, volumes, punctuality, express share and CO2.",
        func=lambda metric, group_by=None, filters=None: _run(T.kpi, metric=metric, group_by=group_by, filters=_clean(filters))),
    StructuredTool.from_function(
        name="run_sql", args_schema=SqlArgs,
        description="Last resort: one read-only SQL SELECT on the raw tables (not cleaned: duplicates and misspelled carrier names remain).",
        func=lambda query: _run(T.run_sql, query=query)),
    StructuredTool.from_function(
        name="analyze_premium_transport", args_schema=PremiumArgs,
        description="Split express or air shipments into avoidable (a cheaper mode would have been on time), forced by production delays, tight promise, or business rule; with estimated savings by site, carrier and lane.",
        func=lambda mode="express", filters=None: _run(T.analyze_premium_transport, mode=mode, filters=_clean(filters))),
    StructuredTool.from_function(
        name="production_delay_impact", args_schema=FiltersOnly,
        description="Where production is late (by site and product type) and how much premium transport it forces.",
        func=lambda filters=None: _run(T.production_delay_impact, filters=_clean(filters))),
    StructuredTool.from_function(
        name="find_consolidation", args_schema=FiltersOnly,
        description="Road/parcel shipments on the same lane and week that could have left together in one truck and still been on time; estimated savings.",
        func=lambda filters=None: _run(T.find_consolidation, filters=_clean(filters))),
    StructuredTool.from_function(
        name="simulate_shuttle_extension", args_schema=ShuttleArgs,
        description="What-if: other French sites send their Casablanca shipments by road to Toulouse, then on the Wednesday shuttle. Savings in EUR and CO2.",
        func=lambda sites: _run(T.simulate_shuttle_extension, sites=sites)),
    StructuredTool.from_function(
        name="simulate_mode_shift", args_schema=ShiftArgs,
        description="What-if: shipments of one lane move from one mode to another when the lead time allows. Savings in EUR and CO2.",
        func=lambda origin, destination, from_mode, to_mode: _run(T.simulate_mode_shift, origin=origin, destination=destination, from_mode=from_mode, to_mode=to_mode)),
    StructuredTool.from_function(
        name="carrier_performance", args_schema=FiltersOnly,
        description="Cost per kg and punctuality by carrier, lane and mode, with the last 3 months vs before.",
        func=lambda filters=None: _run(T.carrier_performance, filters=_clean(filters))),
    StructuredTool.from_function(
        name="detect_carrier_degradation", args_schema=NoArgs,
        description="Carriers whose own punctuality (vs standard transit time) dropped over the last 3 months, by lane.",
        func=lambda: _run(T.detect_carrier_degradation)),
    StructuredTool.from_function(
        name="lane_price_benchmark", args_schema=NoArgs,
        description="Carriers charging more than the other carriers on the same lane and mode, weight-adjusted; overspend in EUR.",
        func=lambda: _run(T.lane_price_benchmark)),
    StructuredTool.from_function(
        name="compare_carbon_methods", args_schema=CarbonArgs,
        description="Spend-based vs activity-based CO2e, grouped, with the reason they differ.",
        func=lambda group_by="mode": _run(T.compare_carbon_methods, group_by=group_by)),
]


# ------------------------------------------------------------------ actions
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "document"


def _write(kind: str, title: str, body: str) -> str:
    folder = OUTPUT_DIR / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(title)}.md"
    path.write_text(body, encoding="utf-8")
    return json.dumps({"saved": str(path.relative_to(OUTPUT_DIR.parent))}, ensure_ascii=False)


class EmailArgs(Args):
    recipient: str = Field(..., description="Who it is for, e.g. 'EuroRoute Logistics account manager'")
    subject: str
    body: str = Field(..., description="Full text of the e-mail, in French, with the figures and their period")


class PlanArgs(Args):
    title: str
    content: str = Field(..., description="The transport plan in Markdown: actions, sites concerned, expected savings in EUR and CO2, assumptions, owners")


def draft_email(recipient: str, subject: str, body: str) -> str:
    return _write("emails", subject, f"To: {recipient}\nSubject: {subject}\n\n{body}\n")


def save_transport_plan(title: str, content: str) -> str:
    return _write("plans", title, f"# {title}\n\n{content}\n")


ACTION_TOOLS = [
    StructuredTool.from_function(
        name="draft_email", args_schema=EmailArgs, func=draft_email,
        description="Save an e-mail DRAFT (never sent) for a carrier or a site. Needs human approval."),
    StructuredTool.from_function(
        name="save_transport_plan", args_schema=PlanArgs, func=save_transport_plan,
        description="Save a transport plan or recommendation note as a document. Needs human approval."),
]
ACTION_TOOL_NAMES = {t.name for t in ACTION_TOOLS}
ALL_TOOLS = ANALYSIS_TOOLS + ACTION_TOOLS
