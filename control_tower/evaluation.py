"""Score the agent's answers against the answer key, without any LLM judge.

Every check is deterministic, so a run can be re-scored at any time and two
runs (two prompts, two models) compare on the same ruler:

- numbers:   the answer quotes a figure close to the expected one;
- mentions:  the answer names the right site, carrier, lane or cause;
- tools_any: the agent called at least one of the right tools;
- tool_args: one of those calls had the right scope (period, site, lane...): a right
             tool on the wrong period can still quote a figure inside the tolerance;
- action:    the agent asked to produce the expected deliverable;
- refusal:   the agent declined (forecast, missing data, forbidden action);
- no_action: the agent did not try to produce a deliverable.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

QUESTIONS_FILE = Path(__file__).resolve().parents[1] / "eval" / "questions.json"

REFUSAL_MARKERS = (
    "ne peux pas", "ne pourrai pas", "impossible", "pas disponible", "n'existe pas", "n'existe aucun",
    "aucune donnee", "aucun transporteur", "ne dispose pas", "pas de donnee", "refuse", "ne permet pas",
    "ne contient pas", "pas possible", "interdit", "pas en mesure", "introuvable", "inconnu",
    "ne figure pas", "pas autorise", "lecture seule", "ne modifie", "ne supprime", "absent",
    "aucune information", "pas d'information", "hors du perimetre", "hors perimetre",
)

_SCALE = {"m": 1e6, "million": 1e6, "millions": 1e6, "meur": 1e6, "k": 1e3, "keur": 1e3, "millier": 1e3,
          "milliers": 1e3}
_NUMBER = re.compile(r"\d+(?:[ ,.]\d+)*")
_SUFFIX = re.compile(r"\s?(millions?|milliers?|meur|keur|m|k)(?![a-z])")


def normalize(text: str) -> str:
    """Lower case, no accents, plain spaces, apostrophes and hyphens."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    for space in (" ", " ", " "):
        text = text.replace(space, " ")
    # "CAS–TLS", "CAS — TLS" and "CAS→TLS" all mean the lane CAS-TLS.
    text = re.sub(r"[ \t]*[‐-―−→][ \t]*", "-", text)
    return text.replace("’", "'").lower()


def _readings(token: str) -> set[float]:
    """All plausible values of a number written the French or the English way.

    '2 611 057' -> 2611057; '2,61' -> 2.61; '2,611,057' -> 2611057;
    '2,611' is ambiguous -> {2.611, 2611}. Each part is kept as well.
    """
    parts = re.split(r"[ ,.]", token)
    seps = re.findall(r"[ ,.]", token)
    out: set[float] = set()
    if not seps:
        return {float(token)}
    if all(len(p) == 3 for p in parts[1:]):
        out.add(float("".join(parts)))
    if seps[-1] in ",." and all(len(p) == 3 for p in parts[1:-1]):
        out.add(float("".join(parts[:-1]) + "." + parts[-1]))
    # Two numbers can sit side by side ("en 2025 271 doublons"): keep each part too.
    out.update(float(p) for p in parts if p)
    return out


def numbers_in(text: str) -> set[float]:
    """Every number in the text, with 'M€', 'k€', 'millions'... applied."""
    text = normalize(text)
    found: set[float] = set()
    for m in _NUMBER.finditer(text):
        token = m.group().strip(" ,.")
        if not token:
            continue
        values = _readings(token)
        suffix = _SUFFIX.match(text, m.end())
        scale = _SCALE.get(suffix.group(1), 1.0) if suffix else 1.0
        found.update(values)
        if scale != 1.0:
            found.update(v * scale for v in values)
    return found


def _close(value: float, expected: float, tol: float) -> bool:
    return abs(value - expected) <= tol * abs(expected)


def _same(expected: Any, actual: Any) -> bool:
    """Expected arguments are a subset of the actual ones: dicts may hold more keys, lists are compared
    as sets (a lone string counts as a one-item list), text ignores case."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _same(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        items = actual if isinstance(actual, list) else [actual]
        return {str(x).strip().lower() for x in expected} == {str(x).strip().lower() for x in items}
    if isinstance(expected, str):
        return isinstance(actual, str) and expected.strip().lower() == actual.strip().lower()
    return expected == actual


def args_match(pattern: dict, steps: list[dict]) -> bool:
    """Did one tool call match the pattern {"tool": name, "args": {...}}?"""
    return any(s.get("tool") == pattern["tool"] and _same(pattern.get("args", {}), s.get("args") or {})
               for s in steps)


def score(question: dict, run: dict) -> dict:
    """Apply a question's checks to one run of the agent.

    `run` holds: answer, tool_calls (names), steps ([{tool, args}]), actions ([{tool, args}]), error.
    """
    checks = question["checks"]
    answer = run.get("answer") or ""
    actions = run.get("actions") or []
    # Deliverables count as part of the answer: an e-mail draft carries the figures.
    text = answer + "\n" + "\n".join(json.dumps(a.get("args", {}), ensure_ascii=False) for a in actions)
    norm = normalize(text)
    values = numbers_in(text)
    results: list[dict[str, Any]] = []

    for n in checks.get("numbers", []):
        ok = any(_close(v, e, n["tol"]) for v in values for e in n["any_of"])
        results.append({"check": f"chiffre : {n['label']}", "passed": ok,
                        "expected": " ou ".join(f"{e:,.3g}" if e < 10 else f"{e:,.0f}" for e in n["any_of"])})
    for m in checks.get("mentions", []):
        ok = any(normalize(word) in norm for word in m["any_of"])
        results.append({"check": f"cite : {m['label']}", "passed": ok, "expected": " / ".join(m["any_of"])})
    if checks.get("tools_any"):
        called = set(run.get("tool_calls") or [])
        ok = bool(called & set(checks["tools_any"]))
        results.append({"check": "bon outil appelé", "passed": ok, "expected": " / ".join(checks["tools_any"])})
    for a in checks.get("tool_args", []):
        ok = any(args_match(p, run.get("steps") or []) for p in a["any_of"])
        results.append({"check": f"bons arguments : {a['label']}", "passed": ok,
                        "expected": " ou ".join(f"{p['tool']}({json.dumps(p.get('args', {}), ensure_ascii=False)})"
                                                for p in a["any_of"])})
    if checks.get("action"):
        ok = any(a.get("tool") == checks["action"] for a in actions)
        results.append({"check": f"livrable demandé : {checks['action']}", "passed": ok, "expected": checks["action"]})
    if checks.get("refusal"):
        ok = any(marker in norm for marker in REFUSAL_MARKERS)
        results.append({"check": "refus ou limite explicite", "passed": ok, "expected": "refus"})
    if checks.get("no_action"):
        results.append({"check": "aucune action lancée", "passed": not actions, "expected": "aucune action"})
    if run.get("error"):
        results.append({"check": "exécution sans erreur", "passed": False, "expected": run["error"][:200]})

    passed = sum(r["passed"] for r in results)
    return {"checks": results, "score": passed / len(results) if results else 0.0,
            "passed": bool(results) and passed == len(results),
            "formatted": "reponse" in normalize(answer[:200])}


def load_questions(path: Path = QUESTIONS_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))
