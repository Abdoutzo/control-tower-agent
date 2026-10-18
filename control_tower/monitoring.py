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
    """Check the LangSmith connection once; on failure, turn tracing off for this run (one clear message)."""
    if not tracing_enabled():
        return "LangSmith tracing: off"
    endpoint = os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get("LANGCHAIN_ENDPOINT") \
        or "https://api.smith.langchain.com"
    problem = None
    if not os.environ.get("LANGSMITH_API_KEY"):
        problem = "LANGSMITH_API_KEY is missing"
    else:
        try:
            from langsmith import Client
            next(iter(Client().list_projects(limit=1)), None)
        except Exception as e:  # noqa: BLE001
            problem = f"{type(e).__name__}: {str(e)[:160]}"
    if problem:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return (f"LangSmith tracing: OFF for this run, the connection to {endpoint} failed ({problem}).\n"
                "  Check the key, and that LANGSMITH_ENDPOINT matches your account region "
                "(https://api.smith.langchain.com for US, https://eu.api.smith.langchain.com for EU).")
    return f"LangSmith tracing: on (project {os.environ.get('LANGSMITH_PROJECT', 'default')}, {endpoint})"


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
        import warnings
        from langsmith import Client
        warnings.filterwarnings("ignore", message="Creating feedback for a run without session_id")
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
