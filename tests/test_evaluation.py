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
