"""The agent loop, tested offline with a scripted model (no API key needed).

The scripted model plays the LLM's role: it emits the tool calls a real model
would, so we test the graph mechanics (tool execution, approval pause,
refusal) deterministically.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command

from control_tower import agent_tools
from control_tower.agent import build_graph


class ScriptedModel(BaseChatModel):
    script: list[AIMessage]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def call(name: str, args: dict, i: int = 1) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}])


def run(graph, question: str, thread: str):
    cfg = {"configurable": {"thread_id": thread}, "recursion_limit": 25}
    return graph.invoke({"messages": [HumanMessage(question)]}, cfg), cfg


def test_agent_calls_analysis_tool_then_answers():
    model = ScriptedModel(script=[call("analyze_premium_transport", {"mode": "express"}),
                                  AIMessage(content="**Réponse** L'express évitable vient surtout de Bordeaux.")])
    graph = build_graph(model)
    out, _ = run(graph, "D'où vient notre express évitable ?", "t1")
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0].content)
    assert "Avoidable" in payload["summary"]
    assert out["messages"][-1].content.startswith("**Réponse**")


def test_tool_error_is_returned_to_the_model():
    model = ScriptedModel(script=[call("run_sql", {"query": "SELECT * FROM nope"}), AIMessage(content="ok")])
    out, _ = run(build_graph(model), "q", "t2")
    tool_msg = next(m for m in out["messages"] if isinstance(m, ToolMessage))
    assert "error" in tool_msg.content.lower()


@pytest.fixture
def tmp_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tools, "OUTPUT_DIR", tmp_path / "outputs")
    return tmp_path / "outputs"


def email_call() -> AIMessage:
    return call("draft_email", {"recipient": "EuroRoute", "subject": "Ponctualité", "body": "Bonjour..."})


def test_action_pauses_for_approval_and_runs_when_approved(tmp_outputs):
    model = ScriptedModel(script=[email_call(), AIMessage(content="Brouillon enregistré.")])
    graph = build_graph(model)
    out, cfg = run(graph, "Prépare un mail à EuroRoute", "t3")
    assert "__interrupt__" in out
    assert out["__interrupt__"][0].value["actions"][0]["tool"] == "draft_email"
    assert not tmp_outputs.exists()
    out = graph.invoke(Command(resume={"approved": True}), cfg)
    assert len(list((tmp_outputs / "emails").glob("*.md"))) == 1
    assert out["messages"][-1].content == "Brouillon enregistré."


def test_refused_action_writes_nothing(tmp_outputs):
    model = ScriptedModel(script=[email_call(), AIMessage(content="Compris, je n'enregistre rien.")])
    graph = build_graph(model)
    _, cfg = run(graph, "Prépare un mail à EuroRoute", "t4")
    out = graph.invoke(Command(resume={"approved": False, "comment": "pas maintenant"}), cfg)
    assert not tmp_outputs.exists()
    refusal = [m for m in out["messages"] if isinstance(m, ToolMessage)][-1]
    assert "Refusé" in refusal.content


def test_misplaced_argument_is_an_error_the_model_can_fix():
    """A date filter given outside `filters` used to be dropped silently: the KPI covered the whole period."""
    model = ScriptedModel(script=[
        call("kpi", {"metric": "on_time_rate", "start": "2026-04-01", "end": "2026-06-30"}, 1),
        call("kpi", {"metric": "on_time_rate", "filters": {"start": "2026-04-01", "end": "2026-06-30"}}, 2),
        AIMessage(content="ok")])
    out, _ = run(build_graph(model), "Taux à l'heure d'avril à juin 2026 ?", "t5")
    first, second = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert first.status == "error" and "start" in first.content
    assert json.loads(second.content)["data"]["shipments"] < 54285
