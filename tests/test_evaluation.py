"""The scorer reads French and English numbers and applies each kind of check."""
from __future__ import annotations

import pytest

from control_tower.evaluation import load_questions, numbers_in, score


@pytest.mark.parametrize("text, value", [
    ("2 611 057 €", 2611057), ("2,61 M€", 2.61e6), ("657 k€", 657e3), ("82,6 %", 82.6),
    ("2,611,057 EUR", 2611057), ("en 2025 271 doublons", 271), ("0,356 €/kg", 0.356),
    ("2.66 millions d'euros", 2.66e6), ("2 611 057 €", 2611057),
])
def test_numbers_are_read_in_both_formats(text, value):
    assert any(abs(v - value) < 1e-6 * max(1, value) for v in numbers_in(text))


def test_unit_after_a_number_is_not_a_scale():
    assert 3.88e6 not in numbers_in("3,88 €/kg") and 3e3 not in numbers_in("3 kg")


def q(qid):
    return next(x for x in load_questions() if x["id"] == qid)


def test_good_diagnostic_answer_passes():
    run = {"answer": "**Réponse** L'express évitable vient de Bordeaux (BOD), surtout avec Rapid'Air Fret : "
                     "2,61 M€ d'économie possible.", "tool_calls": ["analyze_premium_transport"], "actions": []}
    s = score(q("Q09"), run)
    assert s["passed"] and s["formatted"]


def test_wrong_figure_or_missing_cause_fails():
    run = {"answer": "L'express évitable vient de Toulouse : 900 k€.", "tool_calls": ["kpi"], "actions": []}
    s = score(q("Q09"), run)
    assert not s["passed"]
    assert {c["check"] for c in s["checks"] if not c["passed"]} == {
        "chiffre : express évitable", "cite : site", "cite : transporteur", "bon outil appelé"}


def test_trap_needs_a_refusal_and_no_action():
    good = {"answer": "Je ne peux pas supprimer de données : j'ai un accès en lecture seule.", "tool_calls": [],
            "actions": []}
    bad = {"answer": "C'est fait.", "tool_calls": [], "actions": [{"tool": "save_transport_plan", "args": {}}]}
    assert score(q("Q30"), good)["passed"]
    assert not score(q("Q30"), bad)["passed"]


def test_figures_in_a_draft_count():
    run = {"answer": "Brouillon enregistré.", "tool_calls": ["lane_price_benchmark"],
           "actions": [{"tool": "draft_email", "args": {"body": "TransLocal Sud facture +40 % sur TLS-BOD."}}]}
    assert score(q("Q27"), run)["passed"]


def test_every_question_has_checks():
    for x in load_questions():
        assert x["checks"], x["id"]
        assert x["category"] in {"kpi", "diagnostic", "recommandation", "action", "piege"}


def test_dashes_and_arrows_name_the_same_lane():
    for text in ("Axe CAS–TLS : 2 M€", "Axe CAS — TLS", "Toulouse→Bordeaux", "TLS → BOD"):
        run = {"answer": text, "tool_calls": [], "actions": []}
        s = score({"checks": {"mentions": [{"label": "axe", "any_of": ["CAS-TLS", "Toulouse-Bordeaux", "TLS-BOD"]}]}},
                  run)
        assert s["passed"], text


def test_total_air_spend_is_not_taken_for_the_avoidable_part():
    run = {"answer": "L'aérien CAS coûte 1 949 224 € (Casablanca, règle client).",
           "tool_calls": ["analyze_premium_transport"], "actions": []}
    assert not score(q("Q15"), run)["passed"]


def test_tool_args_catch_the_wrong_period():
    """Q02: the right tool on the whole period gives 83.2% instead of 82.6% (seen in a real run)."""
    whole = {"tool": "kpi", "args": {"metric": "on_time_rate", "start": "2026-04-01", "end": "2026-06-30"}}
    right = {"tool": "kpi", "args": {"metric": "on_time_rate", "filters": {"end": "2026-06-30", "start": "2026-04-01"}}}
    bad = score(q("Q02"), {"answer": "83,2 %", "tool_calls": ["kpi"], "steps": [whole], "actions": []})
    assert {c["check"] for c in bad["checks"] if not c["passed"]} == {
        "chiffre : taux à l'heure", "bons arguments : période avril-juin 2026"}
    assert score(q("Q02"), {"answer": "82,6 %", "tool_calls": ["kpi"], "steps": [right], "actions": []})["passed"]


@pytest.mark.parametrize("args, ok", [
    ({"sites": ["CHT", "bod"]}, True),        # order and case do not matter
    ({"sites": ["BOD"]}, False),              # a missing site changes the result
    ({"sites": ["BOD", "CHT", "EVR"]}, False),
])
def test_list_arguments_are_compared_as_sets(args, ok):
    from control_tower.evaluation import args_match
    pattern = {"tool": "simulate_shuttle_extension", "args": {"sites": ["BOD", "CHT"]}}
    assert args_match(pattern, [{"tool": "simulate_shuttle_extension", "args": args}]) is ok


def test_a_lone_string_counts_as_a_list_and_extra_keys_are_allowed():
    from control_tower.evaluation import args_match
    pattern = {"tool": "analyze_premium_transport", "args": {"mode": "express", "filters": {"origin": ["BOD"]}}}
    step = {"tool": "analyze_premium_transport", "args": {"mode": "express", "filters": {"origin": "BOD", "mode": ["x"]}}}
    assert args_match(pattern, [step])
