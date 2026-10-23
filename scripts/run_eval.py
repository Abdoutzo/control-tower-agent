"""Ask the agent every question of eval/questions.json and score the answers.

    python scripts/run_eval.py                      # full run (about 30 questions)
    python scripts/run_eval.py --only Q01,Q09       # a few questions
    python scripts/run_eval.py --resume eval/results/<run>   # continue after a quota stop
    python scripts/run_eval.py --rescore eval/results/<run>  # rebuild the report, no API call

Each run gets its own folder in eval/results/ with:
    results.jsonl  one line per question: answer, tools called, tokens, time
    report.md      the scorecard
    outputs/       the e-mail drafts and plans the agent produced (auto-approved)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402
from langgraph.types import Command  # noqa: E402

from control_tower import agent_tools  # noqa: E402
from control_tower.agent import (PROVIDERS, SYSTEM_PROMPT, build_graph, is_rate_limit,  # noqa: E402
                                 provider, reasoning_enabled, usage)
from control_tower.cli import check_key, load_dotenv  # noqa: E402
from control_tower.evaluation import load_questions, score  # noqa: E402
from control_tower.monitoring import flush, run_config, send_score, status  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "results"
TARGETS = {"kpi": 0.9, "diagnostic": 0.7, "recommandation": 0.7, "action": 0.7, "piege": 1.0}
MAX_APPROVALS = 3


def prompt_version() -> str:
    """Short hash of the system prompt: two runs with the same hash ran the same prompt."""
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:8]


class QuotaExhausted(Exception):
    pass


def ask(graph, question: str, trace: dict | None = None) -> dict:
    """Run one question to the end, approving every deliverable, and record what happened."""
    trace = trace or {}
    run_id = str(uuid.uuid4())
    config = run_config(str(uuid.uuid4()), trace.get("run_name", "question"), tags=trace.get("tags"),
                        metadata=trace.get("metadata"), run_id=run_id)
    payload: object = {"messages": [HumanMessage(question)]}
    actions, error = [], None
    start = time.time()
    try:
        for _ in range(MAX_APPROVALS + 1):
            interrupted = None
            for update in graph.stream(payload, config, stream_mode="updates"):
                if "__interrupt__" in update:
                    interrupted = update["__interrupt__"][0].value
            if not interrupted:
                break
            actions.extend(interrupted["actions"])
            payload = Command(resume={"approved": True})
    except GraphRecursionError:
        error = "recursion limit reached (the agent looped)"
    except Exception as e:  # noqa: BLE001
        if is_rate_limit(e):
            raise QuotaExhausted(str(e)) from e
        error = f"{type(e).__name__}: {str(e)[:300]}"
    seconds = time.time() - start

    messages = graph.get_state(config).values.get("messages", [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None)
    steps, n_call = [], 0
    for m in messages:
        if isinstance(m, AIMessage):
            n_call += 1  # tools asked in the same model call ran together
            steps += [{"call": n_call, "tool": c["name"], "args": c["args"]} for c in m.tool_calls]
        elif isinstance(m, ToolMessage) and steps:
            try:
                body = json.loads(m.content)
                steps[-1]["result"] = body.get("summary") or body.get("error") or body.get("saved")
            except (ValueError, AttributeError):
                steps[-1]["result"] = str(m.content)[:200]
    return {"answer": last_ai.text if last_ai else "", "tool_calls": [s["tool"] for s in steps],
            "steps": steps, "actions": actions, "seconds": round(seconds, 1), "error": error, "run_id": run_id,
            **usage(messages)}


def read_results(run_dir: Path) -> dict[str, dict]:
    path = run_dir / "results.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}  # the last run of a question wins


def report(run_dir: Path) -> str:
    questions = load_questions()
    results = read_results(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    rows = []
    for q in questions:
        if q["id"] in results:
            r = results[q["id"]]
            rows.append((q, r, score(q, r)))

    lines = [f"# Évaluation du Control Tower Agent",
             "",
             f"- Date : {meta['started']}",
             f"- Modèle : {meta['provider']} / {meta['model']}"
             + (f" (raisonnement : {meta['reasoning']})" if "reasoning" in meta else "")
             + (f", prompt {meta['prompt_version']}" if "prompt_version" in meta else ""),
             f"- Questions évaluées : {len(rows)} sur {len(questions)}",
             "",
             "Notation automatique et déterministe (aucun LLM juge) : chaque question a des contrôles "
             "(chiffres attendus avec une tolérance, entités à citer, outils à appeler, livrable ou refus attendu), "
             "définis dans `eval/questions.json` à partir du corrigé caché.",
             ""]
    if not rows:
        return "\n".join(lines + ["Aucun résultat pour l'instant."])

    n_pass = sum(s["passed"] for _, _, s in rows)
    lines += [f"## Résultat global : {n_pass}/{len(rows)} questions entièrement réussies "
              f"({n_pass / len(rows):.0%}), score moyen {statistics.mean(s['score'] for _, _, s in rows):.0%}",
              "",
              "| Catégorie | Réussies | Score moyen | Objectif | Atteint |",
              "|---|---|---|---|---|"]
    for cat in TARGETS:
        sub = [(q, r, s) for q, r, s in rows if q["category"] == cat]
        if not sub:
            continue
        rate = sum(s["passed"] for _, _, s in sub) / len(sub)
        lines.append(f"| {cat} | {sum(s['passed'] for _, _, s in sub)}/{len(sub)} | "
                     f"{statistics.mean(s['score'] for _, _, s in sub):.0%} | {TARGETS[cat]:.0%} | "
                     f"{'oui' if rate >= TARGETS[cat] else 'non'} |")

    secs = [r["seconds"] for _, r, _ in rows]
    tin = [r["input_tokens"] for _, r, _ in rows]
    tout = [r["output_tokens"] for _, r, _ in rows]
    calls = [r["llm_calls"] for _, r, _ in rows]
    lines += ["",
              "## Coût et rapidité",
              "",
              f"- Temps par question : médiane {statistics.median(secs):.0f} s, max {max(secs):.0f} s "
              "(inclut l'espacement imposé par la limite gratuite du fournisseur)",
              f"- Appels au modèle par question : moyenne {statistics.mean(calls):.1f}",
              f"- Tokens par question : {statistics.mean(tin):,.0f} en entrée, {statistics.mean(tout):,.0f} en sortie (moyenne)",
              f"- Total du run : {sum(tin):,} tokens en entrée, {sum(tout):,} en sortie",
              f"- Réponses au format demandé (Réponse, Chiffres clés...) : "
              f"{sum(s['formatted'] for q, _, s in rows if q['category'] != 'piege')}/"
              f"{sum(1 for q, _, _ in rows if q['category'] != 'piege')}",
              "",
              "## Détail par question",
              "",
              "| Id | Catégorie | Résultat | Score | Temps | Outils appelés | Contrôles échoués |",
              "|---|---|---|---|---|---|---|"]
    for q, r, s in rows:
        failed = "; ".join(c["check"] for c in s["checks"] if not c["passed"]) or ""
        tools = ", ".join(dict.fromkeys(r["tool_calls"])) or "aucun"
        lines.append(f"| {q['id']} | {q['category']} | {'réussi' if s['passed'] else 'échoué'} | {s['score']:.0%} | "
                     f"{r['seconds']:.0f} s | {tools} | {failed} |")

    failed_rows = [(q, r, s) for q, r, s in rows if not s["passed"]]
    if failed_rows:
        lines += ["", "## Questions échouées : ce que l'agent a répondu", ""]
        for q, r, s in failed_rows:
            lines += [f"### {q['id']} : {q['question']}", ""]
            for c in s["checks"]:
                if not c["passed"]:
                    lines.append(f"- Échec « {c['check']} » (attendu : {c['expected']})")
            excerpt = (r["answer"] or "(pas de réponse)").strip().replace("\n", "\n> ")
            lines += ["", "> " + excerpt[:1500], ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated question ids, e.g. Q01,Q09")
    parser.add_argument("--resume", type=Path, help="continue an earlier run (skips questions already answered)")
    parser.add_argument("--rescore", type=Path, help="rebuild the report of an earlier run, without API calls")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between questions (default 5)")
    args = parser.parse_args()

    if args.rescore:
        (args.rescore / "report.md").write_text(report(args.rescore), encoding="utf-8")
        print(f"Report rebuilt: {args.rescore / 'report.md'}")
        return

    load_dotenv()
    check_key()
    name = provider()
    model = os.environ.get("LLM_MODEL") or PROVIDERS[name][1]
    run_dir = args.resume.resolve() if args.resume else RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M}-{name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps({"started": f"{datetime.now():%Y-%m-%d %H:%M}", "provider": name,
                                         "model": model, "reasoning": "on" if reasoning_enabled() else "off",
                                         "prompt_version": prompt_version()},
                                        ensure_ascii=False), encoding="utf-8")
    agent_tools.OUTPUT_DIR = run_dir / "outputs"

    questions = load_questions()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]
    done = set(read_results(run_dir)) if args.resume else set()
    todo = [q for q in questions if q["id"] not in done]
    print(f"{len(todo)} question(s) to run with {name} / {model}. Results in {run_dir}")
    print(status())

    graph = build_graph()
    stopped = None
    with (run_dir / "results.jsonl").open("a", encoding="utf-8") as out:
        for i, q in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {q['id']} {q['question'][:70]}", flush=True)
            try:
                r = ask(graph, q["question"], {"run_name": f"eval {q['id']}",
                                               "tags": ["eval", q["category"], run_dir.name],
                                               "metadata": {"question_id": q["id"], "run": run_dir.name}})
            except QuotaExhausted as e:
                stopped = str(e)
                break
            row = {"id": q["id"], "category": q["category"], "question": q["question"], **r}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            s = score(q, r)
            failed = "; ".join(c["check"] for c in s["checks"] if not c["passed"])
            send_score(r["run_id"], s["score"], s["passed"], failed)
            print(f"      {'OK ' if s['passed'] else 'NOK'} score {s['score']:.0%}, {r['seconds']:.0f} s, "
                  f"{r['llm_calls']} appels, outils: {', '.join(r['tool_calls']) or 'aucun'}"
                  + (f", erreur: {r['error']}" if r["error"] else ""), flush=True)
            if i < len(todo):
                time.sleep(args.pause)

    flush()
    (run_dir / "report.md").write_text(report(run_dir), encoding="utf-8")
    print(f"\nReport: {run_dir / 'report.md'}")
    if stopped:
        quota = re.findall(r"quotaId'?\"?: ?'?\"?([A-Za-z-]+)", stopped)
        limit = re.findall(r"quotaValue'?\"?: ?'?\"?(\d+)", stopped)
        detail = f"{quota[0]}, limit {limit[0] if limit else '?'}" if quota else stopped[:300]
        print(f"\nStopped: the provider's quota is exhausted ({detail}).\n"
              f"Wait (per-minute or daily quota), then continue with:\n"
              f"  python scripts/run_eval.py --resume {run_dir}")


if __name__ == "__main__":
    main()
