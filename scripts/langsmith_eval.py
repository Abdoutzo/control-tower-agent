"""Evaluation in LangSmith: the questions as a dataset, each run of the agent as an experiment.

    python scripts/langsmith_eval.py sync                       # push eval/questions.json as a dataset
    python scripts/langsmith_eval.py run                        # run the agent on it (model chosen in .env)
    python scripts/langsmith_eval.py run --split kpi --repetitions 3
    python scripts/langsmith_eval.py import eval/results/<run>  # a recorded local run, no model call

LangSmith then compares experiments side by side (same questions, same scorer):
pass rate per check, per question, per category, with tokens, latency and the
full trace of every answer. Two prompts or two models are compared on the same
ruler, and a regression shows up as a red cell.

The scorer is the deterministic one of control_tower/evaluation.py; nothing
here asks a model to judge. Needs LANGSMITH_API_KEY (and LANGSMITH_ENDPOINT
for an EU account) in .env.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from control_tower.evaluation import load_questions, score  # noqa: E402

DATASET = os.environ.get("LANGSMITH_DATASET", "control-tower-eval")

# Check label prefix (see evaluation.score) -> feedback key shown as a column in LangSmith.
CHECK_KEYS = {"chiffre": "figures", "cite": "mentions", "bon outil": "tool", "bons arguments": "arguments",
              "livrable": "deliverable", "refus": "refusal", "aucune action": "no_action", "exécution": "no_error"}


def feedback(question: dict, run: dict) -> list[dict]:
    """The scorecard of one answer as LangSmith feedback: passed, score, and one key per kind of check."""
    s = score(question, run)
    by_key: dict[str, list[dict]] = defaultdict(list)
    for c in s["checks"]:
        key = next((v for k, v in CHECK_KEYS.items() if c["check"].startswith(k)), "other")
        by_key[key].append(c)
    failed = "; ".join(f"{c['check']} (attendu : {c['expected']})" for c in s["checks"] if not c["passed"])
    results = [{"key": "passed", "score": int(s["passed"]), "comment": failed or None},
               {"key": "score", "score": s["score"]}]
    for key, checks in by_key.items():
        results.append({"key": key, "score": sum(c["passed"] for c in checks) / len(checks),
                        "comment": "; ".join(c["check"] for c in checks if not c["passed"]) or None})
    return results


def make_evaluator(questions: list[dict]):
    by_text = {q["question"]: q for q in questions}

    def deterministic_scorer(inputs: dict, outputs: dict) -> dict:
        return {"results": feedback(by_text[inputs["question"]], outputs)}
    return deterministic_scorer


def pass_rate(outputs: list[dict], examples: list) -> dict:
    """Experiment-level summary: overall pass rate, per category, and cost of the run."""
    questions = {q["question"]: q for q in load_questions()}
    rows = [(questions[e.inputs["question"]], o) for o, e in zip(outputs, examples, strict=True)
            if e.inputs["question"] in questions]
    results = [{"key": "pass_rate", "score": statistics.mean(score(q, o)["passed"] for q, o in rows)}]
    for cat in sorted({q["category"] for q, _ in rows}):
        sub = [score(q, o)["passed"] for q, o in rows if q["category"] == cat]
        results.append({"key": f"pass_rate_{cat}", "score": statistics.mean(sub)})
    for field in ("input_tokens", "output_tokens", "seconds"):
        values = [o.get(field) for _, o in rows if o.get(field) is not None]
        if values:
            results.append({"key": f"mean_{field}", "score": statistics.mean(values)})
    return {"results": results}


# ------------------------------------------------------------------ dataset
def sync(client, questions: list[dict]) -> None:
    """Create or update the dataset so it matches eval/questions.json (LangSmith keeps every version)."""
    if client.has_dataset(dataset_name=DATASET):
        dataset = client.read_dataset(dataset_name=DATASET)
    else:
        dataset = client.create_dataset(DATASET, description="Control Tower Agent: questions and their checks, "
                                                              "from eval/questions.json")
    existing = {(e.metadata or {}).get("id"): e for e in client.list_examples(dataset_id=dataset.id)}
    created = updated = 0
    for q in questions:
        inputs, outputs = {"question": q["question"]}, {"checks": q["checks"]}
        metadata = {"id": q["id"], "category": q["category"]}
        old = existing.pop(q["id"], None)
        if old is None:
            client.create_example(inputs=inputs, outputs=outputs, metadata=metadata, split=q["category"],
                                  dataset_id=dataset.id)
            created += 1
        elif old.inputs != inputs or old.outputs != outputs:
            client.update_example(old.id, inputs=inputs, outputs=outputs, metadata=metadata, split=q["category"])
            updated += 1
    for gone in existing.values():
        client.delete_example(gone.id)
    print(f"Dataset {DATASET}: {created} created, {updated} updated, {len(existing)} deleted, "
          f"{len(questions)} questions in total")


def examples(client, split: str | None):
    return client.list_examples(dataset_name=DATASET, splits=[split] if split else None)


# --------------------------------------------------------------- experiments
def run_live(client, questions: list[dict], split: str | None, repetitions: int) -> None:
    """Run the agent on the dataset; every answer is traced and scored."""
    from langsmith import evaluate

    from control_tower import agent_tools
    from control_tower.agent import PROVIDERS, build_graph, provider, reasoning_enabled
    from run_eval import ask, prompt_version

    name = provider()
    model = os.environ.get("LLM_MODEL") or PROVIDERS[name][1]
    agent_tools.OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="control-tower-drafts-"))
    graph = build_graph()

    def agent(inputs: dict) -> dict:
        return ask(graph, inputs["question"], {"run_name": "agent", "tags": ["langsmith-experiment"]})

    evaluate(agent, data=examples(client, split), evaluators=[make_evaluator(questions)],
             summary_evaluators=[pass_rate], experiment_prefix=f"{name}-{model.split('/')[-1]}",
             description=f"{name} / {model}, reasoning {'on' if reasoning_enabled() else 'off'}",
             metadata={"provider": name, "model": model, "reasoning": reasoning_enabled(),
                       "prompt_version": prompt_version(), "source": "live"},
             num_repetitions=repetitions, max_concurrency=0, client=client)


def import_run(client, questions: list[dict], run_dir: Path) -> None:
    """Upload a run recorded by scripts/run_eval.py as an experiment: no model call, same scorer."""
    from langsmith import evaluate

    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    recorded = {r["question"]: r for r in rows}  # the last answer to a question wins
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    data = [e for e in examples(client, None) if e.inputs["question"] in recorded]

    def replay(inputs: dict) -> dict:
        return recorded[inputs["question"]]

    evaluate(replay, data=data, evaluators=[make_evaluator(questions)], summary_evaluators=[pass_rate],
             experiment_prefix=run_dir.name, description=f"Recorded run {run_dir.name}: "
             f"{meta['provider']} / {meta['model']}, reasoning {meta.get('reasoning', '?')}",
             metadata={**meta, "source": "recorded", "run": run_dir.name}, max_concurrency=0, client=client)
    print(f"Imported {len(data)} answers of {run_dir.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="push eval/questions.json as the LangSmith dataset")
    live = sub.add_parser("run", help="run the agent on the dataset (model from .env)")
    live.add_argument("--split", help="only one category: kpi, diagnostic, recommandation, action, piege")
    live.add_argument("--repetitions", type=int, default=1, help="answer each question N times (default 1)")
    imp = sub.add_parser("import", help="upload a recorded run of scripts/run_eval.py as an experiment")
    imp.add_argument("run_dirs", type=Path, nargs="+")
    args = parser.parse_args()

    from langsmith import Client

    from control_tower.cli import load_dotenv
    load_dotenv()
    if not os.environ.get("LANGSMITH_API_KEY"):
        sys.exit("LANGSMITH_API_KEY is not set (put it in .env, see .env.example)")
    client = Client()
    questions = load_questions()
    sync(client, questions)  # always: an experiment must run on the current questions
    if args.command == "run":
        from control_tower.cli import check_key
        check_key()
        run_live(client, questions, args.split, args.repetitions)
    elif args.command == "import":
        for run_dir in args.run_dirs:
            import_run(client, questions, run_dir)


if __name__ == "__main__":
    main()
