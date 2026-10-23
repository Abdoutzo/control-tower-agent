"""Expose the tools to the language model, as LangChain tools.

Two kinds of tools:
- analysis tools (read-only, defined in catalog.py): the agent may call them freely;
- action tools (they produce a deliverable): the graph pauses and asks a
  human to approve before they run.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import Field

from . import catalog
from .catalog import Args

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "outputs"

ANALYSIS_TOOLS = [StructuredTool.from_function(name=t.name, description=t.description, args_schema=t.schema,
                                               func=t.run) for t in catalog.ANALYSIS_TOOLS]


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
