"""Compare evaluation runs: quality, speed and cost, one line per configuration.

    python scripts/compare_runs.py                      # every run in eval/results/
    python scripts/compare_runs.py eval/results/A eval/results/B --out docs/model_comparison.md

Runs with the same provider, model, reasoning setting and prompt are one
configuration: their results are averaged and the spread shown, because one
run is not enough (the same agent does not answer exactly the same way twice).
Every run is re-scored with the current scorer, so all compare on one ruler.

Cost per question uses eval/prices.json (price per million input and output
tokens, by model); a model missing from it shows no cost.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from run_eval import RESULTS_DIR, read_results  # noqa: E402

from control_tower.evaluation import load_questions, score  # noqa: E402

PRICES_FILE = ROOT / "eval" / "prices.json"
CATEGORIES = ("kpi", "diagnostic", "recommandation", "action", "piege")


def summarize(run_dir: Path, questions: dict[str, dict]) -> dict | None:
    """One run, re-scored: pass counts, time and tokens per question. None if it is not a full run."""
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None
    results = {i: r for i, r in read_results(run_dir).items() if i in questions}
    if len(results) < len(questions):
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    scored = {i: score(questions[i], r) for i, r in results.items()}
    return {
        "run": run_dir.name, "meta": meta,
        "passed": sum(s["passed"] for s in scored.values()),
        "by_category": {c: sum(s["passed"] for i, s in scored.items() if questions[i]["category"] == c)
                        for c in CATEGORIES},
        "median_s": statistics.median(r["seconds"] for r in results.values()),
        "input_tokens": statistics.mean(r["input_tokens"] for r in results.values()),
        "output_tokens": statistics.mean(r["output_tokens"] for r in results.values()),
        "failed": sorted(i for i, s in scored.items() if not s["passed"]),
    }


def config_key(meta: dict) -> tuple:
    return (meta["provider"], meta["model"], meta.get("reasoning", "?"), meta.get("prompt_version", "not recorded"))


def spread(values: list[float], fmt: str) -> str:
    """'30' for one run, '29.5 (29-30)' for several."""
    if min(values) == max(values):
        return format(values[0], ".0f" if float(values[0]).is_integer() else fmt)
    return f"{format(statistics.mean(values), fmt)} ({format(min(values), fmt)}-{format(max(values), fmt)})"


def table(runs: list[dict], questions: dict[str, dict], prices: dict) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        groups[config_key(r["meta"])].append(r)
    totals = {c: sum(q["category"] == c for q in questions.values()) for c in CATEGORIES}
    lines = [f"| Model | Reasoning | Prompt | Runs | Passed /{len(questions)} | "
             + " | ".join(f"{c} /{totals[c]}" for c in CATEGORIES)
             + " | Median time | Tokens in / out per question | Cost per question | Failed (all runs) |",
             "|" + "---|" * (9 + len(CATEGORIES))]
    for (prov, model, reasoning, prompt), rs in sorted(groups.items(), key=lambda kv: -statistics.mean(
            r["passed"] for r in kv[1])):
        tin = statistics.mean(r["input_tokens"] for r in rs)
        tout = statistics.mean(r["output_tokens"] for r in rs)
        price = prices.get(model)
        cost = f"{(tin * price['input'] + tout * price['output']) / 1e6:.4f} {price.get('currency', '')}".strip() \
            if price else "n/a"
        failed = sorted({i for r in rs for i in r["failed"]})
        lines.append(f"| {prov} / {model} | {reasoning} | {prompt} | {len(rs)} | "
                     f"{spread([r['passed'] for r in rs], '.1f' if len(rs) > 1 else '.0f')} | "
                     + " | ".join(spread([r["by_category"][c] for r in rs], ".1f" if len(rs) > 1 else ".0f")
                                  for c in CATEGORIES)
                     + f" | {spread([r['median_s'] for r in rs], '.0f')} s | {tin:,.0f} / {tout:,.0f} | {cost} | "
                     f"{', '.join(failed) or 'none'} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dirs", type=Path, nargs="*", help="runs to compare (default: all in eval/results)")
    parser.add_argument("--out", type=Path, help="also write the table to this Markdown file")
    args = parser.parse_args()

    questions = {q["id"]: q for q in load_questions()}
    prices = json.loads(PRICES_FILE.read_text(encoding="utf-8"))["models"] if PRICES_FILE.exists() else {}
    dirs = args.run_dirs or sorted(p for p in RESULTS_DIR.iterdir() if p.is_dir())
    runs, skipped = [], []
    for d in dirs:
        summary = summarize(d, questions)
        if summary:
            runs.append(summary)
        else:
            skipped.append(d.name)
    if not runs:
        sys.exit("No complete run to compare.")
    text = table(runs, questions, prices)
    if skipped:
        text += f"\nNot compared (incomplete or no meta.json): {', '.join(skipped)}\n"
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
