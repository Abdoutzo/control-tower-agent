"""Analysis tools the agent can call.

Every tool is deterministic, read-only, and returns the same shape:
    {"summary": one-line answer, "data": table or dict, "method": how it was
     computed, "caveats": what the reader should know}
so the agent can quote figures, show its method and admit limits.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

import pandas as pd

from . import rules
from .data import connect_readonly, load

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
FILTER_COLUMNS = ("origin", "destination", "mode", "carrier_id", "carrier_type",
                  "flow_type", "product_type", "product_family", "lane")
GROUP_COLUMNS = FILTER_COLUMNS + ("carrier_name", "month", "week")


def _result(summary: str, data: Any, method: str, caveats: list[str] | None = None) -> dict:
    return {"summary": summary, "data": data, "method": method, "caveats": caveats or []}


def _records(df: pd.DataFrame, n: int = 20) -> list[dict]:
    out = df.head(n).copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(3)
        elif pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return out.to_dict(orient="records")


def _shipments(filters: dict | None = None) -> pd.DataFrame:
    s = load().shipments
    s = s.assign(lane=s["origin"] + "-" + s["destination"])
    if not filters:
        return s
    unknown = set(filters) - set(FILTER_COLUMNS) - {"start", "end"}
    if unknown:
        raise ValueError(f"unknown filter(s): {sorted(unknown)}; allowed: {FILTER_COLUMNS + ('start', 'end')}")
    for k, v in filters.items():
        if k == "start":
            s = s[s["ship_date"] >= pd.Timestamp(v)]
        elif k == "end":
            s = s[s["ship_date"] <= pd.Timestamp(v)]
        else:
            vals = v if isinstance(v, (list, tuple, set)) else [v]
            s = s[s[k].isin(vals)]
    return s


def _lane_table() -> dict:
    """(origin, destination, mode) -> (standard lead days, distance km)."""
    lanes = load().lanes
    return {(r.origin, r.destination, r.mode): (int(r.standard_lead_days), float(r.distance_km))
            for r in lanes.itertuples()}


def _ref_cost(mode: str, weight_kg: float, distance_km: float) -> float:
    rc = load().rate_card.set_index("mode")
    r = rc.loc[mode]
    return float(r.fixed_eur + weight_kg * (r.eur_per_kg + r.eur_per_kg_per_1000km * distance_km / 1000.0))


def _ef(mode: str, distance_km: float) -> float:
    ef = load().emission_factors.set_index("mode_key")["kgco2e_per_tkm"]
    if mode == "express":
        return float(ef["express_air" if distance_km > 800 else "express_road"])
    return float(ef[mode])


def _effective_lead(mode: str, lead: int, ready: pd.Timestamp) -> int:
    if mode == "navette":
        return (rules.SHUTTLE["weekday"] - ready.weekday()) % 7 + lead
    return lead


def _cheapest_on_time(row, lanes: dict, exclude: tuple[str, ...]) -> tuple[str | None, float | None]:
    """Cheapest mode on the lane, outside `exclude`, that would still arrive by the need date."""
    best, best_cost = None, None
    if pd.isna(row.weight_kg) or pd.isna(row.slack_days):
        return None, None
    for (o, d, m), (lead, dist) in lanes.items():
        if o != row.origin or d != row.destination or m in exclude:
            continue
        if m == "navette" and row.product_family in rules.SHUTTLE["excluded_families"]:
            continue
        if _effective_lead(m, lead, row.actual_ready_date) > row.slack_days:
            continue
        c = _ref_cost(m, row.weight_kg, dist)
        if best_cost is None or c < best_cost:
            best, best_cost = m, c
    return best, best_cost


# --------------------------------------------------------------------------
# 1. overview and raw SQL
# --------------------------------------------------------------------------
def data_overview() -> dict:
    """Tables, period, volumes and data-quality issues found while loading."""
    ds = load()
    s = ds.shipments
    data = {
        "period": [s.ship_date.min().strftime("%Y-%m-%d"), s.ship_date.max().strftime("%Y-%m-%d")],
        "shipments": int(len(s)), "orders": int(len(ds.orders)),
        "sites": ds.sites["site_id"].tolist(), "modes": sorted(s["mode"].unique().tolist()),
        "carriers": int(ds.carriers.shape[0]), "total_cost_eur": round(float(s.cost_eur.sum()), 0),
        "data_quality": ds.quality,
        "tables": ["shipments", "orders", "lanes", "rate_card", "carriers", "emission_factors",
                   "spend_carbon_factor", "sites", "customers", "product_families"],
    }
    q = ds.quality
    return _result(
        f"{len(s)} shipments from {data['period'][0]} to {data['period'][1]}, "
        f"{data['total_cost_eur']:,.0f} EUR; {q['duplicate_rows_removed']} duplicates removed, "
        f"{q['missing_weight_rows']} shipments without weight, "
        f"{sum(q['carrier_name_variants'].values())} carrier names misspelled.",
        data, "Read from the cleaned dataset (see control_tower/data.py).",
        ["Per-kg metrics exclude shipments without a weight."])


_ALLOWED_TABLES = {"shipments", "orders", "lanes", "rate_card", "carriers", "emission_factors",
                   "spend_carbon_factor", "sites", "customers", "product_families"}


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and (not arg1 or arg1 in _ALLOWED_TABLES):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_sql(query: str, limit: int = 200) -> dict:
    """Read-only SQL on the raw tables, for questions no other tool covers.

    Raw tables are NOT cleaned: duplicates and carrier name variants remain.
    """
    q = query.strip().rstrip(";")
    if ";" in q or not re.match(r"(?is)^\s*(with\b.*)?select\b", q):
        return _result("Refused: only one read-only SELECT is allowed.", None, "guardrail", [])
    try:
        with connect_readonly() as con:
            con.set_authorizer(_authorizer)
            df = pd.read_sql(f"SELECT * FROM ({q}) LIMIT {int(limit)}", con)
    except Exception as e:  # noqa: BLE001 - the agent reads the error and corrects its query
        return _result(f"SQL error: {e}", None, "sqlite (read-only)", [])
    return _result(f"{len(df)} rows", _records(df, limit), f"SQL: {q}",
                   ["Raw tables: duplicates and misspelled carrier names are not cleaned here."])


# --------------------------------------------------------------------------
# 2. KPIs
# --------------------------------------------------------------------------
METRICS = ("shipments", "cost_eur", "weight_t", "cost_per_kg", "on_time_rate", "late_rate",
           "express_cost_share", "premium_cost_share", "co2_activity_t", "co2_spend_t",
           "avg_late_days")


def kpi(metric: str, group_by: list[str] | str | None = None, filters: dict | None = None,
        top: int = 30) -> dict:
    """Standard transport KPI, optionally grouped. Definitions are fixed here so
    every answer uses the same ones."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric}; choose from {METRICS}")
    s = _shipments(filters)
    gb = [group_by] if isinstance(group_by, str) else (group_by or [])
    bad = set(gb) - set(GROUP_COLUMNS)
    if bad:
        raise ValueError(f"cannot group by {sorted(bad)}; allowed: {GROUP_COLUMNS}")

    def premium_share(g: pd.DataFrame, modes: tuple[str, ...]) -> float:
        return g.loc[g["mode"].isin(modes), "cost_eur"].sum() / g.cost_eur.sum()

    def per_kg(g: pd.DataFrame) -> float:
        w = g.dropna(subset=["weight_kg"])
        return w.cost_eur.sum() / w.weight_kg.sum() if len(w) else float("nan")

    formulas = {  # evaluated lazily: only the requested metric is computed
        "shipments": len,
        "cost_eur": lambda g: g.cost_eur.sum(),
        "weight_t": lambda g: g.weight_kg.sum() / 1000,
        "cost_per_kg": per_kg,
        "on_time_rate": lambda g: 1 - g.late.mean(),
        "late_rate": lambda g: g.late.mean(),
        "express_cost_share": lambda g: premium_share(g, ("express",)),
        "premium_cost_share": lambda g: premium_share(g, rules.PREMIUM_MODES),
        "co2_activity_t": lambda g: g.co2_activity_kg.sum() / 1000,
        "co2_spend_t": lambda g: g.co2_spend_kg.sum() / 1000,
        "avg_late_days": lambda g: g.loc[g.late, "late_days"].mean() if g.late.any() else 0.0,
    }
    compute = formulas[metric]

    if not gb:
        v = compute(s)
        return _result(f"{metric} = {v:,.3f}", {metric: round(float(v), 4), "shipments": len(s)},
                       f"{metric} over {len(s)} cleaned shipments, filters={filters or {}}",
                       _kpi_caveats(metric))
    out = s.groupby(gb)[s.columns.tolist()].apply(
        lambda g: pd.Series({metric: compute(g), "shipments": len(g)})).reset_index()
    out = out.sort_values(metric, ascending=False)
    return _result(f"{metric} by {', '.join(gb)} ({len(out)} groups)", _records(out, top),
                   f"{metric} grouped by {gb}, filters={filters or {}}", _kpi_caveats(metric))


def _kpi_caveats(metric: str) -> list[str]:
    c = []
    if metric in ("cost_per_kg", "weight_t"):
        c.append("Shipments without a weight are excluded.")
    if metric in ("on_time_rate", "late_rate"):
        c.append("Late = delivered after the need date of the order.")
    if metric == "co2_spend_t":
        c.append("Spend-based method: cost x monthly kgCO2e/EUR factor, not physical emissions.")
    return c


# --------------------------------------------------------------------------
# 3. Premium transport: avoidable vs forced by production delays
# --------------------------------------------------------------------------
def analyze_premium_transport(mode: str = "express", filters: dict | None = None, top: int = 15) -> dict:
    """Classify express or air shipments:
    - avoidable: a cheaper mode would still have arrived by the need date;
    - production_delay: nothing cheaper was on time because production was late;
    - tight_promise: nothing cheaper was on time even without a production delay;
    - business_rule: customer flows from the Maghreb, air by company rule.
    """
    if mode not in rules.PREMIUM_MODES:
        raise ValueError(f"mode must be one of {rules.PREMIUM_MODES}")
    s = _shipments({**(filters or {}), "mode": mode})
    lanes = _lane_table()
    rows = []
    for r in s.itertuples():
        if mode == "aerien" and r.flow_type == "client" and r.origin in rules.MAGHREB_SITES:
            rows.append(("business_rule", None, 0.0))
            continue
        alt, alt_cost = _cheapest_on_time(r, lanes, exclude=rules.PREMIUM_MODES)
        if alt is not None:
            rows.append(("avoidable", alt, max(r.cost_eur - alt_cost, 0.0)))
        elif pd.isna(r.weight_kg) or pd.isna(r.slack_days):
            rows.append(("unknown", None, 0.0))
        elif r.production_delay_days > 0:
            rows.append(("production_delay", None, 0.0))
        else:
            rows.append(("tight_promise", None, 0.0))
    s = s.assign(category=[x[0] for x in rows], alternative=[x[1] for x in rows],
                 saving_eur=[x[2] for x in rows])
    by_cat = s.groupby("category").agg(shipments=("shipment_id", "size"), cost_eur=("cost_eur", "sum"),
                                       saving_eur=("saving_eur", "sum")).reset_index()
    by_cat["cost_share"] = by_cat.cost_eur / by_cat.cost_eur.sum()
    av = s[s.category == "avoidable"]
    by_origin = av.groupby(["origin", "carrier_id"]).agg(
        shipments=("shipment_id", "size"), saving_eur=("saving_eur", "sum"),
        main_alternative=("alternative", lambda x: x.mode().iat[0])).reset_index() \
        .sort_values("saving_eur", ascending=False)
    by_lane = av.groupby("lane").agg(shipments=("shipment_id", "size"), saving_eur=("saving_eur", "sum")) \
        .reset_index().sort_values("saving_eur", ascending=False)
    total = s.cost_eur.sum()
    sav = av.saving_eur.sum()
    pd_cost = s.loc[s.category == "production_delay", "cost_eur"].sum()
    return _result(
        f"{mode}: {total:,.0f} EUR over {len(s)} shipments. Avoidable: {len(av)} shipments, "
        f"~{sav:,.0f} EUR saving. Forced by production delays: {pd_cost / total:.0%} of {mode} spend.",
        {"by_category": _records(by_cat), "avoidable_by_origin_carrier": _records(by_origin, top),
         "avoidable_by_lane": _records(by_lane, top)},
        "For each shipment, slack = need date - actual ready date. A cheaper mode is "
        "'on time' if its standard lead time (shuttle: wait for Wednesday + transit) fits in "
        "the slack. Saving = actual cost - reference rate-card cost of that mode.",
        ["Savings use the reference rate card, not negotiated prices: treat them as estimates.",
         "Standard lead times assume no carrier delay."])


def production_delay_impact(filters: dict | None = None) -> dict:
    """How late production is, where, and what it costs in forced premium transport."""
    o = load().orders
    if filters:
        for k in ("origin", "product_type", "product_family"):
            if k in filters:
                vals = filters[k] if isinstance(filters[k], (list, tuple)) else [filters[k]]
                o = o[o[k].isin(vals)]
    by = o.groupby(["origin", "product_type"]).agg(
        orders=("order_id", "size"),
        delayed_share=("production_delay_days", lambda x: (x > 0).mean()),
        avg_delay_days=("production_delay_days", lambda x: x[x > 0].mean() if (x > 0).any() else 0)) \
        .reset_index().sort_values("delayed_share", ascending=False)
    forced = []
    for m in rules.PREMIUM_MODES:
        res = analyze_premium_transport(m, {k: v for k, v in (filters or {}).items() if k in ("origin",)})
        for row in res["data"]["by_category"]:
            if row["category"] == "production_delay":
                forced.append({"mode": m, "shipments": row["shipments"], "cost_eur": row["cost_eur"]})
    total_forced = sum(f["cost_eur"] for f in forced)
    worst = by.groupby("origin").apply(lambda g: (g.delayed_share * g.orders).sum() / g.orders.sum(),
                                       include_groups=False).sort_values(ascending=False)
    return _result(
        f"Premium transport forced by production delays: {total_forced:,.0f} EUR. "
        f"Most delayed sites: {', '.join(f'{k} ({v:.0%})' for k, v in worst.head(3).items())}.",
        {"delays_by_site_and_type": _records(by, 30), "forced_premium_cost": forced},
        "Production delay = actual ready date - planned ready date (orders table). Forced premium "
        "= express/air shipments with no cheaper on-time alternative and a production delay.",
        ["A forced express is a transport cost caused by the factory: fix it in production planning."])


# --------------------------------------------------------------------------
# 4. Consolidation and shuttle
# --------------------------------------------------------------------------
def find_consolidation(filters: dict | None = None, top: int = 15) -> dict:
    """Road and parcel shipments on the same lane in the same week that could have
    left together in one road shipment and still arrived on time."""
    s = _shipments({**(filters or {}), "mode": list(rules.CONSOLIDABLE_MODES)}).dropna(subset=["weight_kg"])
    lanes = _lane_table()
    groups = []
    for (o, d, wk), g in s.groupby(["origin", "destination", "week"]):
        if len(g) < 2 or (o, d, "routier") not in lanes:
            continue
        lead, dist = lanes[(o, d, "routier")]
        depart = g.ship_date.max()
        if (g.need_date < depart + pd.Timedelta(days=lead)).any():
            g = g[g.need_date >= depart + pd.Timedelta(days=lead)]
            if len(g) < 2:
                continue
        consolidated = _ref_cost("routier", g.weight_kg.sum(), dist)
        saving = g.cost_eur.sum() - consolidated
        if saving > 0:
            groups.append(dict(lane=f"{o}-{d}", week=wk, shipments=len(g), weight_kg=g.weight_kg.sum(),
                               actual_cost_eur=g.cost_eur.sum(), consolidated_cost_eur=consolidated,
                               saving_eur=saving))
    df = pd.DataFrame(groups)
    if df.empty:
        return _result("No consolidation opportunity found.", [], "lane x ISO week grouping", [])
    by_lane = df.groupby("lane").agg(weeks=("week", "size"), shipments=("shipments", "sum"),
                                     saving_eur=("saving_eur", "sum")).reset_index() \
        .sort_values("saving_eur", ascending=False)
    return _result(
        f"{len(df)} lane-weeks could have been consolidated ({int(df.shipments.sum())} shipments), "
        f"~{df.saving_eur.sum():,.0f} EUR saving.",
        {"by_lane": _records(by_lane, top), "examples": _records(df.sort_values("saving_eur", ascending=False), 5)},
        "Group road/parcel shipments by lane and ISO week; ship together on the latest ship date of "
        "the week with the road lead time; keep only shipments still on time; cost of the grouped "
        "shipment from the reference rate card.",
        ["Assumes the goods can wait until the last departure of the week.",
         "Truck capacity is not modelled; very heavy weeks may need two trucks."])


def simulate_shuttle_extension(sites: list[str] | str) -> dict:
    """What if these sites sent their Casablanca shipments by road to Toulouse,
    then on the Wednesday shuttle?"""
    sites = [sites] if isinstance(sites, str) else list(sites)
    s = _shipments({"origin": sites, "destination": "CAS"}).dropna(subset=["weight_kg"])
    lanes = _lane_table()
    if ("TLS", "CAS", "navette") not in lanes:
        return _result("No shuttle lane in the data.", None, "", [])
    nav_lead, nav_dist = lanes[("TLS", "CAS", "navette")]
    rows = []
    for r in s.itertuples():
        if (r.origin, "TLS", "routier") not in lanes:
            continue
        if r.product_family in rules.SHUTTLE["excluded_families"]:
            rows.append(dict(origin=r.origin, eligible=False, reason="family excluded from shuttle"))
            continue
        lead1, dist1 = lanes[(r.origin, "TLS", "routier")]
        at_tls = r.actual_ready_date + pd.Timedelta(days=lead1)
        depart = at_tls + pd.Timedelta(days=(rules.SHUTTLE["weekday"] - at_tls.weekday()) % 7)
        arrival = depart + pd.Timedelta(days=nav_lead)
        if arrival > r.need_date:
            rows.append(dict(origin=r.origin, eligible=False, reason="would arrive late"))
            continue
        new_cost = _ref_cost("routier", r.weight_kg, dist1) + _ref_cost("navette", r.weight_kg, nav_dist)
        new_co2 = r.weight_kg / 1000 * (dist1 * _ef("routier", dist1) + nav_dist * _ef("navette", nav_dist))
        rows.append(dict(origin=r.origin, eligible=True, reason="", current_mode=r.mode,
                         current_cost=r.cost_eur, new_cost=new_cost, saving_eur=r.cost_eur - new_cost,
                         current_co2_kg=r.co2_activity_kg, new_co2_kg=new_co2))
    df = pd.DataFrame(rows)
    el = df[df.eligible]
    summ = el.groupby("origin").agg(shipments=("eligible", "size"), current_cost=("current_cost", "sum"),
                                    new_cost=("new_cost", "sum"), saving_eur=("saving_eur", "sum"),
                                    co2_saved_kg=("current_co2_kg", "sum")).reset_index()
    if not summ.empty:
        summ["co2_saved_kg"] = summ["co2_saved_kg"] - el.groupby("origin")["new_co2_kg"].sum().values
    excluded = df[~df.eligible].groupby(["origin", "reason"]).size().reset_index(name="shipments")
    return _result(
        f"{len(el)} of {len(df)} shipments to Casablanca from {', '.join(sites)} could use the shuttle: "
        f"~{el.saving_eur.sum():,.0f} EUR and {summ.co2_saved_kg.sum() / 1000 if not summ.empty else 0:,.1f} t CO2e saved.",
        {"by_site": _records(summ), "not_eligible": _records(excluded)},
        "Route = road from the site to Toulouse, wait for the Wednesday shuttle, 5 days transit. "
        "Eligible if it still arrives by the need date and the product family is allowed. "
        "Costs from the reference rate card; CO2 with activity-based factors.",
        ["Shuttle capacity is assumed sufficient; check the truck fill rate before deciding."])


# --------------------------------------------------------------------------
# 5. Carriers
# --------------------------------------------------------------------------
def carrier_performance(filters: dict | None = None, top: int = 30) -> dict:
    """Cost per kg and lateness by carrier and lane, with the recent trend."""
    s = _shipments(filters)
    last = s.ship_date.max()
    recent_from = (last - pd.DateOffset(months=3)).normalize()
    s = s.assign(recent=s.ship_date >= recent_from)
    w = s.dropna(subset=["weight_kg"])
    g = s.groupby(["carrier_id", "carrier_name", "lane", "mode"])
    out = g.agg(shipments=("shipment_id", "size"), carrier_late_rate=("carrier_late", "mean"),
                late_vs_need_rate=("late", "mean")).reset_index()
    kg = w.groupby(["carrier_id", "lane", "mode"]).apply(
        lambda x: x.cost_eur.sum() / x.weight_kg.sum(), include_groups=False).rename("eur_per_kg").reset_index()
    rec = s[s.recent].groupby(["carrier_id", "lane", "mode"])["carrier_late"].agg(["mean", "size"]) \
        .rename(columns={"mean": "late_rate_last_3m", "size": "n_last_3m"}).reset_index()
    old = s[~s.recent].groupby(["carrier_id", "lane", "mode"])["carrier_late"].mean() \
        .rename("late_rate_before").reset_index()
    out = out.merge(kg, on=["carrier_id", "lane", "mode"], how="left") \
        .merge(rec, on=["carrier_id", "lane", "mode"], how="left") \
        .merge(old, on=["carrier_id", "lane", "mode"], how="left")
    out = out.sort_values("shipments", ascending=False)
    return _result(f"{len(out)} carrier x lane x mode combinations.", _records(out, top),
                   "carrier_late_rate = delivered later than the standard transit time of the lane (the "
                   "carrier's own punctuality); late_vs_need_rate = delivered after the need date (includes "
                   f"production delays). 'last 3 months' = since {recent_from.date()}.",
                   [f"Combinations with fewer than {rules.MIN_SAMPLE} shipments are not reliable."])


def detect_carrier_degradation(min_increase: float = 0.15) -> dict:
    """Carrier x lane whose late rate over the last 3 months jumped vs before."""
    perf = pd.DataFrame(carrier_performance(top=10_000)["data"])
    m = perf[(perf.n_last_3m >= rules.MIN_SAMPLE) & (perf.shipments - perf.n_last_3m >= rules.MIN_SAMPLE)].copy()
    m["increase"] = m.late_rate_last_3m - m.late_rate_before
    flagged = m[m.increase >= min_increase].sort_values("increase", ascending=False)
    if flagged.empty:
        return _result("No carrier degradation above the threshold.", [], "", [])
    top_row = flagged.iloc[0]
    return _result(
        f"{len(flagged)} degradation(s). Worst: {top_row.carrier_name} on {top_row.lane}, late rate "
        f"{top_row.late_rate_before:.0%} -> {top_row.late_rate_last_3m:.0%}.",
        _records(flagged[["carrier_id", "carrier_name", "lane", "mode", "late_rate_before",
                          "late_rate_last_3m", "n_last_3m", "increase"]]),
        f"Carrier late rate (vs standard transit time) over the last 3 months vs the earlier period, "
        f"at least {rules.MIN_SAMPLE} shipments "
        f"in each, increase >= {min_increase:.0%}.",
        ["A jump can come from a few bad weeks: look at the monthly trend before acting."])


def lane_price_benchmark(threshold: float = 0.10, top: int = 15) -> dict:
    """Carriers that charge more than the other carriers of the same lane and mode,
    once shipment weights are taken into account."""
    s = _shipments().dropna(subset=["weight_kg"])
    lanes = _lane_table()
    ref = [_ref_cost(r.mode, r.weight_kg, lanes[(r.origin, r.destination, r.mode)][1]) for r in s.itertuples()]
    s = s.assign(ref_cost=ref)
    g = s.groupby(["lane", "mode", "carrier_id", "carrier_name", "carrier_type"]).agg(
        shipments=("shipment_id", "size"), cost=("cost_eur", "sum"), ref=("ref_cost", "sum")).reset_index()
    g = g[g.shipments >= rules.MIN_SAMPLE]
    g["price_index"] = g.cost / g.ref
    rows = []
    for (lane, mode), grp in g.groupby(["lane", "mode"]):
        if len(grp) < 2:
            continue
        for r in grp.itertuples():
            others = grp[grp.carrier_id != r.carrier_id]
            others_index = others.cost.sum() / others.ref.sum()
            premium = r.price_index / others_index - 1
            if premium >= threshold:
                rows.append(dict(lane=lane, mode=mode, carrier_id=r.carrier_id, carrier_name=r.carrier_name,
                                 carrier_type=r.carrier_type, shipments=r.shipments,
                                 price_index=r.price_index, others_price_index=others_index, premium=premium,
                                 overspend_eur=r.cost - others_index * r.ref))
    df = pd.DataFrame(rows)
    if df.empty:
        return _result("No overpriced carrier found.", [], "", [])
    by_carrier = df.groupby(["carrier_id", "carrier_name", "carrier_type"]).agg(
        lanes=("lane", "nunique"), shipments=("shipments", "sum"), overspend_eur=("overspend_eur", "sum"),
        avg_premium=("premium", "mean")).reset_index().sort_values("overspend_eur", ascending=False)
    return _result(
        f"{by_carrier.shape[0]} carrier(s) charge {threshold:.0%}+ more than the others on the same lanes; "
        f"overspend ~{df.overspend_eur.sum():,.0f} EUR. Main: "
        + ", ".join(f"{r.carrier_name} (+{r.avg_premium:.0%})" for r in by_carrier.head(3).itertuples()) + ".",
        {"by_carrier": _records(by_carrier, top),
         "by_lane": _records(df.sort_values("overspend_eur", ascending=False), top)},
        "Price index = actual cost / reference rate-card cost of the same shipment (same mode, weight, "
        "distance), which removes the effect of heavier or lighter shipments. A carrier is flagged when "
        "its index is above the index of the other carriers on the same lane and mode.",
        [f"Only carriers with at least {rules.MIN_SAMPLE} shipments on the lane are compared.",
         "A higher price can buy better service: check the late rate before switching."])


# --------------------------------------------------------------------------
# 6. Carbon
# --------------------------------------------------------------------------
def compare_carbon_methods(group_by: str = "mode") -> dict:
    """Spend-based vs activity-based CO2e, and why they differ."""
    if group_by not in GROUP_COLUMNS:
        raise ValueError(f"group_by must be one of {GROUP_COLUMNS}")
    s = _shipments()
    g = s.groupby(group_by).agg(cost_eur=("cost_eur", "sum"), co2_activity_t=("co2_activity_kg", "sum"),
                                co2_spend_t=("co2_spend_kg", "sum")).reset_index()
    g["co2_activity_t"] /= 1000
    g["co2_spend_t"] /= 1000
    g["spend_vs_activity"] = g.co2_spend_t / g.co2_activity_t
    g = g.sort_values("co2_activity_t", ascending=False)
    tot_a, tot_s = g.co2_activity_t.sum(), g.co2_spend_t.sum()
    return _result(
        f"Activity-based {tot_a:,.0f} t CO2e vs spend-based {tot_s:,.0f} t ({tot_s / tot_a - 1:+.0%}).",
        _records(g), "Activity-based = tonnes x km x emission factor of the mode. Spend-based = "
        "cost x monthly kgCO2e/EUR factor.",
        ["The spend-based method follows prices, not physics: it overstates expensive low-emission "
         "services (road express, parcel) and understates cheap high-emission ones per euro (air "
         "freight). Use activity-based figures for decisions."])


# --------------------------------------------------------------------------
# 7. What-if: move a lane to another mode
# --------------------------------------------------------------------------
def simulate_mode_shift(origin: str, destination: str, from_mode: str, to_mode: str) -> dict:
    """Shipments of a lane in `from_mode` that could have used `to_mode` and still been on time."""
    lanes = _lane_table()
    if (origin, destination, to_mode) not in lanes:
        return _result(f"{to_mode} is not available on {origin}-{destination}.", None, "lanes table", [])
    lead, dist = lanes[(origin, destination, to_mode)]
    s = _shipments({"origin": origin, "destination": destination, "mode": from_mode}).dropna(subset=["weight_kg"])
    ok = []
    for r in s.itertuples():
        if to_mode == "navette" and r.product_family in rules.SHUTTLE["excluded_families"]:
            continue
        if _effective_lead(to_mode, lead, r.actual_ready_date) <= r.slack_days:
            new_cost = _ref_cost(to_mode, r.weight_kg, dist)
            ok.append(dict(cost=r.cost_eur, new_cost=new_cost, co2=r.co2_activity_kg,
                           new_co2=r.weight_kg / 1000 * dist * _ef(to_mode, dist)))
    df = pd.DataFrame(ok)
    if df.empty:
        return _result(f"No {from_mode} shipment on {origin}-{destination} could have used {to_mode} on time.",
                       {"eligible": 0, "total": len(s)}, "", [])
    data = {"eligible": len(df), "total": len(s), "current_cost_eur": df.cost.sum(), "new_cost_eur": df.new_cost.sum(),
            "saving_eur": df.cost.sum() - df.new_cost.sum(), "co2_saved_t": (df.co2.sum() - df.new_co2.sum()) / 1000}
    return _result(
        f"{len(df)}/{len(s)} {from_mode} shipments on {origin}-{destination} could go by {to_mode}: "
        f"~{data['saving_eur']:,.0f} EUR and {data['co2_saved_t']:,.1f} t CO2e saved.",
        {k: (round(v, 1) if isinstance(v, float) else v) for k, v in data.items()},
        f"Eligible if the {to_mode} lead time fits in the order slack; new cost from the rate card.",
        ["Estimate: negotiated prices and capacity are not modelled."])


TOOLS = {
    "data_overview": data_overview, "run_sql": run_sql, "kpi": kpi,
    "analyze_premium_transport": analyze_premium_transport,
    "production_delay_impact": production_delay_impact, "find_consolidation": find_consolidation,
    "simulate_shuttle_extension": simulate_shuttle_extension, "carrier_performance": carrier_performance,
    "detect_carrier_degradation": detect_carrier_degradation, "lane_price_benchmark": lane_price_benchmark,
    "compare_carbon_methods": compare_carbon_methods, "simulate_mode_shift": simulate_mode_shift,
}
