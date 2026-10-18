"""Ask the agent a question from the terminal.

    python -m control_tower.cli "Pourquoi nos coûts express sont-ils si élevés ?"

Needs LLM_PROVIDER and the matching API key in a .env file at the project root (see .env.example).
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

from .agent import PROVIDERS, build_graph, is_rate_limit, provider, usage
from .monitoring import flush, run_config, status


def load_dotenv(path: Path = Path(__file__).resolve().parents[1] / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            # The project's .env wins over an older value set in the system.
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


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


def check_key() -> None:
    try:
        key_var = PROVIDERS[provider()][0]
    except RuntimeError as e:
        sys.exit(str(e))
    key = os.environ.get(key_var, "")
    if not key or key == "your-key-here":
        sys.exit(f"{key_var} is missing: open the .env file and replace your-key-here with your key.")


def main() -> None:
    load_dotenv()
    check_key()
    question = " ".join(sys.argv[1:]) or input("Question : ")
    graph = build_graph()
    print(status())
    config = run_config(str(uuid.uuid4()), "question", tags=["cli"], metadata={"question": question})
    start = time.time()
    payload = {"messages": [HumanMessage(question)]}
    while True:
        interrupted = None
        try:
            for update in graph.stream(payload, config, stream_mode="updates"):
                if "__interrupt__" in update:
                    interrupted = update["__interrupt__"][0].value
                show(update)
        except Exception as e:  # noqa: BLE001
            text = str(e)
            if "401" in text[:200] or "API key not valid" in text or "invalid_api_key" in text:
                sys.exit(f"The provider refused the API key. Check it in .env.\n{text[:500]}")
            if is_rate_limit(e):
                sys.exit(f"Rate limit reached even after retries. Wait a minute, or lower "
                         f"LLM_REQUESTS_PER_SECOND in .env.\n{text[:500]}")
            raise
        if not interrupted:
            break
        print("\nL'agent veut produire :")
        for a in interrupted["actions"]:
            print(f"  - {a['tool']}: {json.dumps(a['args'], ensure_ascii=False)[:600]}")
        ok = input("Valider ? [o/N] ").strip().lower() in ("o", "oui", "y", "yes")
        payload = Command(resume={"approved": ok, "comment": "" if ok else input("Pourquoi ? ")})
    state = graph.get_state(config).values
    print("\n" + state["messages"][-1].text)
    u = usage(state["messages"])
    print(f"\n[{time.time() - start:.1f}s, {u['llm_calls']} appels au modèle, "
          f"{u['input_tokens']} tokens en entrée, {u['output_tokens']} en sortie]")
    flush()


if __name__ == "__main__":
    main()
