"""The tools must find the problems hidden in the simulated data.

Each test compares a tool's output with eval/ground_truth.json, which the
tools never read. Run `python scripts/generate_data.py` first.
"""
import json
from pathlib import Path

import pytest

from control_tower import tools as T

GT = json.loads((Path(__file__).resolve().parents[1] / "eval" / "ground_truth.json").read_text())


def by(rows, key, value):
    return [r for r in rows if r[key] == value]


def test_overview_reports_dirty_data():
    q = T.data_overview()["data"]["data_quality"]
    assert q["duplicate_rows_removed"] > 0
    assert q["missing_weight_rows"] > 0
    assert len(q["carrier_name_variants"]) >= 3


def test_avoidable_express_is_bordeaux_with_rapid_air_fret():
    res = T.analyze_premium_transport("express")
    top = res["data"]["avoidable_by_origin_carrier"][0]
    s1 = GT["S1_avoidable_express"]
    assert (top["origin"], top["carrier_id"]) == (s1["site"], s1["carrier"])
    saving = sum(r["saving_eur"] for r in by(res["data"]["by_category"], "category", "avoidable"))
    assert saving == pytest.approx(GT["S1_avoidable_express"]["avoidable_cost_eur"], rel=0.10)


def test_most_express_spend_is_forced_by_production_delays():
    cats = T.analyze_premium_transport("express")["data"]["by_category"]
    share = by(cats, "category", "production_delay")[0]["cost_share"]
    assert share > 0.5


def test_production_delays_concentrated_on_chatellerault_and_casablanca():
    summary = T.production_delay_impact()["summary"]
    assert "CHT" in summary.split("Most delayed sites:")[1][:20]
    assert "CAS" in summary.split("Most delayed sites:")[1][:30]


def test_avoidable_air_on_casablanca_internal_flows():
    res = T.analyze_premium_transport("aerien")
    saving = sum(r["saving_eur"] for r in by(res["data"]["by_category"], "category", "avoidable"))
    assert saving == pytest.approx(GT["S4_air_casablanca"]["avoidable_cost_eur"], rel=0.10)
    assert all("CAS" in r["lane"] for r in res["data"]["avoidable_by_lane"][:3])
    assert by(res["data"]["by_category"], "category", "business_rule")  # Maghreb -> customers


def test_overpriced_carriers():
    ids = {r["carrier_id"] for r in T.lane_price_benchmark()["data"]["by_carrier"]}
    assert GT["S3_small_carrier"]["carrier"] in ids


def test_carrier_degradation_on_both_lanes():
    flagged = {(r["carrier_id"], r["lane"]) for r in T.detect_carrier_degradation()["data"]}
    for lane in GT["S7_carrier_degradation"]["lanes"]:
        assert (GT["S7_carrier_degradation"]["carrier"], lane) in flagged


def test_consolidation_opportunities_exist():
    res = T.find_consolidation()
    assert res["data"]["by_lane"] and res["data"]["by_lane"][0]["saving_eur"] > 0


def test_shuttle_extension_saves_money_and_co2():
    rows = T.simulate_shuttle_extension(["BOD", "CHT"])["data"]["by_site"]
    assert {r["origin"] for r in rows} == {"BOD", "CHT"}
    assert all(r["saving_eur"] > 0 and r["co2_saved_kg"] > 0 for r in rows)


def test_spend_based_carbon_overstates_express_and_understates_air():
    rows = {r["mode"]: r["spend_vs_activity"] for r in T.compare_carbon_methods()["data"]}
    assert rows["express"] > 1.5 and rows["messagerie"] > 1.5
    assert rows["aerien"] < rows["express"]


def test_august_dip_is_visible_in_both_years():
    rows = {r["month"]: r["shipments"] for r in T.kpi("shipments", group_by="month", top=100)["data"]}
    assert rows["2025-08"] < 0.7 * rows["2025-07"]


def test_run_sql_is_read_only():
    assert "Refused" in T.run_sql("DELETE FROM shipments")["summary"]
    assert "error" in T.run_sql("SELECT * FROM sqlite_master")["summary"].lower()
    assert T.run_sql("SELECT COUNT(*) AS n FROM sites")["data"][0]["n"] == 9
