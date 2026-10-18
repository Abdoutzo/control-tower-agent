"""Ask the agent a question from the terminal.

    python -m control_tower.cli "Pourquoi nos coûts express sont-ils si élevés ?"

Needs MISTRAL_API_KEY in the environment or in a .env file at the project root.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from .agent import build_graph, usage


def load_dotenv(path: Path = Path(__file__).resolve().parents[1] / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def show(update: dict) -> None:
    for node, payload in update.items():
        if node == "__interrupt__" or not payload:
            continue
        for m in payload.get("messages", []):
            if isinstance(m, AIMessage) and m.tool_calls:
                for c in m.tool_calls:
                    print(f"  -> {c['name']}({json.dumps(c['args'], ensure_ascii=False)[:160]})")
            elif isinstance(m, ToolMessage):
                try:
                    summary = json.loads(m.content).get("summary") or json.loads(m.content).get("error")
                except (ValueError, AttributeError):
                    summary = str(m.content)[:160]
                print(f"     {summary}")


def main() -> None:
    load_dotenv()
    question = " ".join(sys.argv[1:]) or input("Question : ")
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 25}
    start = time.time()
    payload = {"messages": [HumanMessage(question)]}
    while True:
        interrupted = None
        for update in graph.stream(payload, config, stream_mode="updates"):
            if "__interrupt__" in update:
                interrupted = update["__interrupt__"][0].value
            show(update)
        if not interrupted:
            break
        print("\nL'agent veut produire :")
        for a in interrupted["actions"]:
            print(f"  - {a['tool']}: {json.dumps(a['args'], ensure_ascii=False)[:600]}")
        ok = input("Valider ? [o/N] ").strip().lower() in ("o", "oui", "y", "yes")
        payload = Command(resume={"approved": ok, "comment": "" if ok else input("Pourquoi ? ")})
    state = graph.get_state(config).values
    print("\n" + state["messages"][-1].content)
    u = usage(state["messages"])
    print(f"\n[{time.time() - start:.1f}s, {u['llm_calls']} appels au modèle, "
          f"{u['input_tokens']} tokens en entrée, {u['output_tokens']} en sortie]")


if __name__ == "__main__":
    main()
