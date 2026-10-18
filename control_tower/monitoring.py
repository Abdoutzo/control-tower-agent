"""Optional LangSmith tracing.

LangChain and LangGraph send a trace of every run to LangSmith as soon as
these variables are set (in .env):

    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=...
    LANGSMITH_PROJECT=control-tower-agent

Nothing else is needed in the code. This module only adds what makes the
traces useful: a readable run name, tags and metadata (question id,
provider, model) to filter on, and a flush before the program exits so the
last traces are not lost; the evaluation also attaches its score to each
trace. With tracing off, it changes nothing.
"""
from __future__ import annotations

import os
from typing import Any


def tracing_enabled() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").strip().lower() == "true"


def status() -> str:
    if not tracing_enabled():
        return "LangSmith tracing: off"
    if not os.environ.get("LANGSMITH_API_KEY"):
        return "LangSmith tracing: LANGSMITH_TRACING=true but LANGSMITH_API_KEY is missing, traces will fail"
    return f"LangSmith tracing: on (project {os.environ.get('LANGSMITH_PROJECT', 'default')})"


def run_config(thread_id: str, run_name: str, tags: list[str] | None = None,
               metadata: dict[str, Any] | None = None, run_id: str | None = None) -> dict:
    """LangGraph config for one question: memory thread, loop limit, and trace labels."""
    from .agent import PROVIDERS, provider

    name = provider()
    meta = {"provider": name, "model": os.environ.get("LLM_MODEL") or PROVIDERS[name][1], **(metadata or {})}
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25,
              "run_name": run_name, "tags": [name, *(tags or [])], "metadata": meta}
    if run_id:
        config["run_id"] = run_id
    return config


def send_score(run_id: str, score: float, passed: bool, comment: str = "") -> None:
    """Attach the evaluation result to the trace, so failed runs can be filtered in LangSmith."""
    if not tracing_enabled():
        return
    try:
        from langsmith import Client
        flush()  # the run must exist before feedback is attached to it
        client = Client()
        client.create_feedback(run_id, key="score", score=score, comment=comment or None)
        client.create_feedback(run_id, key="passed", score=1.0 if passed else 0.0)
    except Exception as e:  # noqa: BLE001  (monitoring must never break the evaluation)
        print(f"      (LangSmith feedback not sent: {type(e).__name__}: {str(e)[:120]})")


def flush() -> None:
    """Wait until every trace is sent (traces are uploaded in the background)."""
    if tracing_enabled():
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
