"""Generate the simulated transport history of AeroParts.

AeroParts is a fictional aerospace manufacturer: 9 production sites
(7 in France, 2 in the Maghreb), intersite and customer flows, every
transport mode and carrier type. 18 months of orders and shipments.

The data hides known problems (see HIDDEN SCENARIOS below). The agent has
to find them; eval/ground_truth.json holds the answers so we can score it.

Outputs
  data/*.csv and data/transport.db   what the agent can see
  eval/shipments_truth.csv           hidden labels, never given to the agent
  eval/ground_truth.json             expected findings, computed from the truth

Run:  python scripts/generate_data.py   (deterministic, seed 42)
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
VOLUME_SCALE = 6.0  # orders per week multiplier, tuned for ~11-12 MEUR of transport per year
START = pd.Timestamp("2025-01-01")
END = pd.Timestamp("2026-06-30")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVAL = ROOT / "eval"

rng = np.random.default_rng(SEED)

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
SITES = [
    # id, name, country, lat, lon
    ("TLS", "AeroParts Toulouse", "FR", 43.60, 1.44),
    ("BOD", "AeroParts Bordeaux", "FR", 44.84, -0.58),
    ("EVR", "AeroParts Evry", "FR", 48.63, 2.44),
    ("LEH", "AeroParts Le Havre", "FR", 49.49, 0.11),
    ("CHT", "AeroParts Chatellerault", "FR", 46.82, 0.55),
    ("MEL", "AeroParts Melun", "FR", 48.54, 2.66),
    ("NTE", "AeroParts Nantes", "FR", 47.22, -1.55),
    ("CAS", "AeroParts Casablanca", "MA", 33.57, -7.59),
    ("TUN", "AeroParts Tunis", "TN", 36.81, 10.18),
]

CUSTOMERS = [
    ("C-HAM", "Customer Hamburg", "DE", 53.55, 9.99),
    ("C-SEV", "Customer Seville", "ES", 37.39, -5.98),
    ("C-BRS", "Customer Bristol", "GB", 51.45, -2.59),
    ("C-MUC", "Customer Munich", "DE", 48.14, 11.58),
    ("C-GET", "Customer Getafe", "ES", 40.31, -3.73),
    ("C-TLS", "Customer Toulouse Final Assembly", "FR", 43.63, 1.37),
    ("C-MTL", "Customer Montreal", "CA", 45.50, -73.57),
    ("C-SIN", "Customer Singapore", "SG", 1.35, 103.82),
]

# Carriers: id, display name, type, modes, price multiplier, base on-time rate
CARRIERS = [
    ("GLX", "GlobeX Express", "integrateur", ["express"], 1.00, 0.97),
    ("SKP", "SkyParcel", "integrateur", ["express"], 1.04, 0.96),
    ("RAF", "Rapid'Air Fret", "expressiste", ["express"], 1.18, 0.95),
    ("MAT", "Messagerie Atlantique", "messagerie", ["messagerie"], 1.00, 0.93),
    ("FCP", "France Colis Pro", "messagerie", ["messagerie"], 0.97, 0.91),
    ("TGA", "Transports Garonne", "routier", ["routier"], 1.00, 0.94),
    ("ERL", "EuroRoute Logistics", "routier", ["routier"], 0.98, 0.94),
    ("TMR", "Trans-Maghreb Road", "routier", ["routier", "navette"], 1.02, 0.90),
    ("OAF", "Ocean & Air Forwarding", "commissionnaire", ["aerien", "maritime"], 1.00, 0.92),
    ("MDF", "MedFreight", "commissionnaire", ["aerien", "maritime"], 1.03, 0.91),
    ("RCE", "RailCargo Europa", "ferroviaire", ["ferroviaire"], 1.00, 0.90),
    ("TLSUD", "TransLocal Sud", "petit_local", ["routier"], 1.38, 0.93),
    ("NAV", "Navettes Normandes", "petit_local", ["routier"], 1.05, 0.95),
]
CARRIER_BY_ID = {c[0]: c for c in CARRIERS}

# Cost model per mode: fixed EUR + weight_kg * (a + b * distance_km / 1000)
COST = {
    "express": (30.0, 2.0, 2.0),
    "messagerie": (18.0, 0.25, 0.35),
    "routier": (80.0, 0.05, 0.12),
    "ferroviaire": (120.0, 0.04, 0.08),
    "aerien": (150.0, 1.2, 0.9),
    "maritime": (250.0, 0.08, 0.02),
    # Weekly consolidated shuttle Toulouse -> Casablanca (road + ferry): the
    # truck is shared, so each shipment only pays a small share.
    "navette": (10.0, 0.30, 0.0),
}

# Activity-based emission factors, kg CO2e per tonne-km (GLEC-style orders
# of magnitude). Express above 800 km flies.
EF_TKM = {
    "express_road": 0.250,
    "express_air": 0.900,
    "messagerie": 0.120,
    "routier": 0.090,
    "ferroviaire": 0.025,
    "aerien": 1.000,
    "maritime": 0.015,
    "navette": 0.060,  # full, shared truck + ferry
}

MAGHREB = {"MA", "TN"}
INTERCONTINENTAL = {"CA", "SG"}


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def lane_modes(c_from: str, c_to: str, dist: float):
    """Available modes and standard door-to-door lead time (days) for a lane."""
    countries = {c_from, c_to}
    if countries & INTERCONTINENTAL:
        return {"express": 4, "aerien": 6, "maritime": 32}
    if countries & MAGHREB:
        # Road + ferry, air freight, short-sea, express by air.
        return {"express": 3, "aerien": 4, "routier": 6, "maritime": 9}
    if countries == {"FR"}:
        modes = {"express": 1, "messagerie": 2 if dist < 500 else 3, "routier": 1 if dist < 400 else 2}
        if dist > 550:
            modes["ferroviaire"] = 4
        return modes
    # Intra-Europe
    return {"express": 2, "messagerie": 4, "routier": 3 if dist < 1200 else 4, "ferroviaire": 5}


def mode_cost(mode: str, weight: float, dist: float, mult: float) -> float:
    fixed, a, b = COST[mode]
    return (fixed + weight * (a + b * dist / 1000.0)) * mult


def co2_activity(mode: str, weight: float, dist: float) -> float:
    if mode == "express":
        key = "express_air" if dist > 800 else "express_road"
    else:
        key = mode
    # The lane distance already depends on the mode: great-circle for air,
    # x1.25 detour for road and rail, x1.4 for sea.
    return weight / 1000.0 * dist * EF_TKM[key]


# --------------------------------------------------------------------------
# Lanes: who ships to whom, and how much
# --------------------------------------------------------------------------
places = {s[0]: s for s in SITES} | {c[0]: c for c in CUSTOMERS}

LANE_SPECS = [
    # origin, destination, flow type, orders per week (base)
    ("TLS", "BOD", "intersite", 9), ("BOD", "TLS", "intersite", 6),
    ("TLS", "EVR", "intersite", 5), ("EVR", "MEL", "intersite", 8),
    ("MEL", "EVR", "intersite", 5), ("CHT", "TLS", "intersite", 6),
    ("CHT", "BOD", "intersite", 4), ("NTE", "LEH", "intersite", 4),
    ("LEH", "EVR", "intersite", 5), ("CAS", "TLS", "intersite", 7),
    ("TLS", "CAS", "intersite", 5), ("TUN", "CHT", "intersite", 4),
    ("CAS", "BOD", "intersite", 3), ("BOD", "CAS", "intersite", 3),
    ("CHT", "CAS", "intersite", 2), ("BOD", "C-TLS", "client", 6),
    ("TLS", "C-TLS", "client", 10), ("TLS", "C-HAM", "client", 7),
    ("BOD", "C-HAM", "client", 3), ("EVR", "C-MUC", "client", 4),
    ("MEL", "C-BRS", "client", 3), ("NTE", "C-SEV", "client", 3),
    ("CHT", "C-GET", "client", 3), ("LEH", "C-MTL", "client", 2),
    ("EVR", "C-SIN", "client", 1), ("CAS", "C-SEV", "client", 2),
]

PRODUCT_FAMILIES = [
    ("FAM-MEC", "Pieces mecaniques", "serie"),
    ("FAM-MTR", "Sous-ensembles mecatroniques", "serie"),
    ("FAM-ELE", "Cartes et harnais electroniques", "serie"),
    ("FAM-OPT", "Equipements optiques et optroniques", "commande"),
    ("FAM-SPC", "Equipements specifiques", "commande"),
]

# Site habits and problems ------------------------------------------------
# HIDDEN SCENARIOS
#  S1 avoidable express: BOD ships express by habit with Rapid'Air Fret
#  S2 no consolidation: every order ships alone (all sites)
#  S3 overpriced small carrier: TransLocal Sud on TLS->BOD
#  S4 air to/from Casablanca although road/sea was on time
#  S5 carbon methods diverge (structural, from the two methods)
#  S6 production delays concentrated on CHT and CAS, worse on "commande"
#  S7 EuroRoute Logistics degrades from 2026-03 on TLS->C-HAM and EVR->C-MUC
#  S8 dirty data: missing weights, carrier name variants, duplicates
#  S9 August dip (seasonal, NOT an anomaly)
EXPRESS_HABIT = {"BOD": 0.45}
#  S10 the Toulouse->Casablanca shuttle exists but other French sites ship to
#      Casablanca alone; optronic products are not allowed on the shuttle
SHUTTLE_LANES = {("TLS", "CAS")}
SHUTTLE_TRANSIT_DAYS = 5
SHUTTLE_WEEKDAY = 2  # Wednesday
SHUTTLE_EXCLUDED_FAMILIES = {"FAM-OPT"}
#  Maghreb -> customers: air is the business rule (sea is rare), not a habit
CLIENT_AIR_FROM_MAGHREB = 0.85
AIR_HABIT_CAS = 0.60
SMALL_CARRIER_LANE = ("TLS", "BOD")
DEGRADED = {"carrier": "ERL", "lanes": {("TLS", "C-HAM"), ("EVR", "C-MUC")},
            "from": pd.Timestamp("2026-03-01"), "on_time": 0.55}
DELAY_PROFILE = {  # site: (probability of a production delay, mean delay days)
    "CHT": (0.45, 8.0), "CAS": (0.40, 7.0),
}
DEFAULT_DELAY = (0.15, 4.0)
PREFERRED = {  # (origin, mode) -> carrier habit
    ("BOD", "express"): "RAF",
    ("LEH", "routier"): "NAV", ("NTE", "routier"): "NAV",
    ("CAS", "routier"): "TMR", ("TUN", "routier"): "TMR",
}


def seasonality(day: pd.Timestamp) -> float:
    if day.month == 8:
        return 0.55
    if day.month == 12 and day.day > 20:
        return 0.6
    return 1.0


def pick_carrier(origin: str, dest: str, mode: str) -> str:
    if mode == "routier" and (origin, dest) == SMALL_CARRIER_LANE and rng.random() < 0.6:
        return "TLSUD"
    pref = PREFERRED.get((origin, mode))
    if pref and rng.random() < 0.8:
        return pref
    options = [c[0] for c in CARRIERS if mode in c[3] and c[2] != "petit_local"
               and not (c[0] == "TMR" and not ({places[origin][2], places[dest][2]} & MAGHREB))]
    return str(rng.choice(options))


def main() -> None:
    DATA.mkdir(exist_ok=True)
    EVAL.mkdir(exist_ok=True)

    # Lanes table with distances, modes, lead times
    lanes = []
    for o, d, flow, per_week in LANE_SPECS:
        po, pd_ = places[o], places[d]
        gc = haversine(po[3], po[4], pd_[3], pd_[4])
        road = gc * 1.25
        modes = lane_modes(po[2], pd_[2], road)
        if (o, d) in SHUTTLE_LANES:
            modes["navette"] = SHUTTLE_TRANSIT_DAYS
        for mode, lead in modes.items():
            dist = gc if mode in ("aerien",) or (mode == "express" and road > 800) else (
                gc * 1.4 if mode == "maritime" else road)
            lanes.append(dict(lane_id=f"{o}-{d}", origin=o, destination=d, flow_type=flow,
                              mode=mode, standard_lead_days=lead, distance_km=round(dist, 1),
                              orders_per_week=per_week))
    lanes_df = pd.DataFrame(lanes)

    # Orders start before the window so that shipments cover it fully;
    # only shipments leaving inside [START, END] are kept.
    days = pd.date_range(START - pd.Timedelta(days=75), END, freq="D")
    orders, ships = [], []
    order_n = ship_n = 0
    for o, d, flow, per_week in LANE_SPECS:
        lane = lanes_df[lanes_df.lane_id == f"{o}-{d}"].set_index("mode")
        for day in days:
            if day.weekday() >= 5:
                continue
            n = rng.poisson(per_week * VOLUME_SCALE / 5)
            for _ in range(n):
                fam = PRODUCT_FAMILIES[rng.integers(len(PRODUCT_FAMILIES))]
                ptype = fam[2]
                weight = float(np.clip(rng.lognormal(math.log(140), 1.0), 2, 6000))
                cheapest_ok = lane["standard_lead_days"].max()
                promise = int(rng.integers(20, 41) if ptype == "serie" else rng.integers(30, 61))
                need = day + pd.Timedelta(days=promise)
                # Planned ready date leaves time for the slowest regular mode
                # used on this lane (excluding the 32-day sea legs), plus margin.
                plan_transport = min(cheapest_ok, 9) + 2
                planned_ready = need - pd.Timedelta(days=plan_transport)
                p_delay, mean_delay = DELAY_PROFILE.get(o, DEFAULT_DELAY)
                if ptype == "commande":
                    p_delay, mean_delay = min(p_delay * 1.5, 0.85), mean_delay * 1.4
                delay = int(rng.exponential(mean_delay)) + 1 if rng.random() < p_delay else 0
                actual_ready = planned_ready + pd.Timedelta(days=delay)
                slack = (need - actual_ready).days
                # Mode choice ---------------------------------------------
                def effective_lead(m, r):  # called right away, in this iteration
                    if m != "navette":
                        return r.standard_lead_days
                    wait = (SHUTTLE_WEEKDAY - actual_ready.weekday()) % 7  # noqa: B023
                    return wait + r.standard_lead_days
                feasible = {m: r for m, r in lane.iterrows() if effective_lead(m, r) <= slack
                            and not (m == "navette" and fam[0] in SHUTTLE_EXCLUDED_FAMILIES)}
                costs = {m: mode_cost(m, weight, r.distance_km, 1.0) for m, r in lane.iterrows()}
                cheapest_feasible = min(feasible, key=lambda m: costs[m]) if feasible else None
                reason = "cheapest_on_time"
                if cheapest_feasible is None:
                    mode = "express" if "express" in lane.index else lane["standard_lead_days"].idxmin()
                    reason = "production_delay"  # S6 -> express subi
                else:
                    mode = cheapest_feasible
                    if o in EXPRESS_HABIT and mode != "express" and rng.random() < EXPRESS_HABIT[o]:
                        mode, reason = "express", "habit_express"  # S1
                    elif flow == "intersite" and ("CAS" in (o, d)) and mode in ("routier", "maritime") \
                            and rng.random() < AIR_HABIT_CAS:
                        mode, reason = "aerien", "habit_air"  # S4
                    elif flow == "client" and places[o][2] in MAGHREB and "aerien" in lane.index \
                            and mode != "aerien" and rng.random() < CLIENT_AIR_FROM_MAGHREB:
                        mode, reason = "aerien", "client_air_rule"
                carrier = pick_carrier(o, d, mode)
                mult = CARRIER_BY_ID[carrier][4] * float(rng.normal(1.0, 0.04))
                r = lane.loc[mode]
                cost = mode_cost(mode, weight, r.distance_km, mult)

                # Delivery ------------------------------------------------
                if mode == "navette":
                    ship_date = actual_ready + pd.Timedelta(days=(SHUTTLE_WEEKDAY - actual_ready.weekday()) % 7)
                else:
                    ship_date = actual_ready + pd.Timedelta(days=int(rng.integers(0, 2)))
                # Keep only the observation window; thin out shipments in the
                # seasonal dips (S9: factories slow down in August).
                if not (START <= ship_date <= END) or rng.random() > seasonality(ship_date):
                    continue
                order_n += 1
                orders.append(dict(order_id=f"ORD-{order_n:06d}", order_date=day.date(),
                                   origin=o, destination=d, flow_type=flow, product_family=fam[0],
                                   product_type=ptype, weight_kg=round(weight, 1),
                                   need_date=need.date(), planned_ready_date=planned_ready.date(),
                                   actual_ready_date=actual_ready.date(), production_delay_days=delay))
                on_time_p = CARRIER_BY_ID[carrier][5]
                if carrier == DEGRADED["carrier"] and (o, d) in DEGRADED["lanes"] and ship_date >= DEGRADED["from"]:
                    on_time_p = DEGRADED["on_time"]  # S7
                extra = 0 if rng.random() < on_time_p else int(rng.integers(1, 5))
                delivered = ship_date + pd.Timedelta(days=int(r.standard_lead_days) + extra)

                avoidable_cost = 0.0
                if reason in ("habit_express", "habit_air"):
                    alt = lane.loc[cheapest_feasible]
                    avoidable_cost = cost - mode_cost(cheapest_feasible, weight, alt.distance_km, 1.0)
                ship_n += 1
                ships.append(dict(
                    shipment_id=f"SHP-{ship_n:06d}", order_id=f"ORD-{order_n:06d}",
                    origin=o, destination=d, flow_type=flow, mode=mode, carrier_id=carrier,
                    carrier_name=CARRIER_BY_ID[carrier][1], carrier_type=CARRIER_BY_ID[carrier][2],
                    ship_date=ship_date.date(), delivery_date=delivered.date(), need_date=need.date(),
                    weight_kg=round(weight, 1), distance_km=r.distance_km, cost_eur=round(cost, 2),
                    co2_activity_kg=round(co2_activity(mode, weight, r.distance_km), 2),
                    _reason=reason, _cheapest_feasible=cheapest_feasible,
                    _avoidable_cost=round(max(avoidable_cost, 0.0), 2),
                ))

    orders_df = pd.DataFrame(orders)
    ships_df = pd.DataFrame(ships)

    # Spend-based carbon factor (kg CO2e per EUR), drifting over time (S5)
    months = pd.period_range(START, END, freq="M")
    spend_factor = pd.DataFrame({
        "month": months.astype(str),
        "kgco2e_per_eur": np.round(np.linspace(0.42, 0.36, len(months)), 3),
    })
    ships_df["month"] = pd.to_datetime(ships_df["ship_date"]).dt.to_period("M").astype(str)
    ships_df = ships_df.merge(spend_factor, on="month", how="left")
    ships_df["co2_spend_kg"] = (ships_df["cost_eur"] * ships_df["kgco2e_per_eur"]).round(2)
    ships_df = ships_df.drop(columns=["kgco2e_per_eur"])

    # Truth before dirtying
    truth = ships_df.copy()

    # Dirty data (S8) -------------------------------------------------------
    dirty = ships_df.drop(columns=[c for c in ships_df.columns if c.startswith("_")]).copy()
    variants = {
        "GlobeX Express": ["GLOBEX EXPRESS", "Globex Exp.", "GlobeX Express France"],
        "Rapid'Air Fret": ["RAPID AIR FRET", "Rapid Air Fret"],
        "EuroRoute Logistics": ["Euroroute Log.", "EUROROUTE"],
        "TransLocal Sud": ["Trans Local Sud"],
    }
    for name, alts in variants.items():
        idx = dirty.index[dirty.carrier_name == name]
        pick = rng.choice(idx, size=int(len(idx) * 0.08), replace=False)
        dirty.loc[pick, "carrier_name"] = rng.choice(alts, size=len(pick))
    miss = rng.choice(dirty.index, size=int(len(dirty) * 0.02), replace=False)
    dirty.loc[miss, "weight_kg"] = np.nan
    dup = dirty.loc[rng.choice(dirty.index, size=int(len(dirty) * 0.005), replace=False)]
    dirty = pd.concat([dirty, dup]).sort_values(["ship_date", "shipment_id"]).reset_index(drop=True)

    # Reference tables visible to the agent
    sites_df = pd.DataFrame(SITES, columns=["site_id", "name", "country", "lat", "lon"])
    cust_df = pd.DataFrame(CUSTOMERS, columns=["customer_id", "name", "country", "lat", "lon"])
    carriers_df = pd.DataFrame([dict(carrier_id=c[0], carrier_name=c[1], carrier_type=c[2],
                                     modes="|".join(c[3])) for c in CARRIERS])
    rates = pd.DataFrame([dict(mode=m, fixed_eur=v[0], eur_per_kg=v[1], eur_per_kg_per_1000km=v[2])
                          for m, v in COST.items()])
    ef = pd.DataFrame([dict(mode_key=k, kgco2e_per_tkm=v) for k, v in EF_TKM.items()])
    families = pd.DataFrame(PRODUCT_FAMILIES, columns=["product_family", "label", "product_type"])

    visible = {
        "sites": sites_df, "customers": cust_df, "carriers": carriers_df,
        "lanes": lanes_df.drop(columns=["orders_per_week"]), "rate_card": rates,
        "emission_factors": ef, "spend_carbon_factor": spend_factor,
        "product_families": families, "orders": orders_df, "shipments": dirty,
    }
    db = DATA / "transport.db"
    if db.exists():
        db.unlink()
    with sqlite3.connect(db) as con:
        for name, df in visible.items():
            df.to_csv(DATA / f"{name}.csv", index=False)
            df.to_sql(name, con, index=False)

    truth.to_csv(EVAL / "shipments_truth.csv", index=False)
    write_ground_truth(truth, orders_df)
    print(f"orders={len(orders_df)} shipments={len(dirty)} (truth {len(truth)})")


def write_ground_truth(t: pd.DataFrame, orders: pd.DataFrame) -> None:
    t = t.copy()
    t["ship_date"] = pd.to_datetime(t["ship_date"])
    t["late"] = pd.to_datetime(t["delivery_date"]) > pd.to_datetime(t["need_date"])
    express = t[t["mode"] == "express"]
    week = t["ship_date"].dt.isocalendar()
    t["yw"] = week["year"].astype(str) + "-W" + week["week"].astype(str).str.zfill(2)
    small = t[t["mode"].isin(["routier", "messagerie"])]
    groups = small.groupby(["origin", "destination", "yw"]).size()

    deg = t[(t.carrier_id == "ERL") & t.apply(lambda r: (r.origin, r.destination) in DEGRADED["lanes"], axis=1)]
    before = deg[deg.ship_date < DEGRADED["from"]]["late"].mean()
    after = deg[deg.ship_date >= DEGRADED["from"]]["late"].mean()

    tl = t[(t.origin == "TLS") & (t.destination == "BOD") & (t["mode"] == "routier")]
    per_kg = (tl.groupby("carrier_id")["cost_eur"].sum() / tl.groupby("carrier_id")["weight_kg"].sum()).round(3)

    delay_by_site = orders.groupby("origin")["production_delay_days"].apply(lambda s: (s > 0).mean()).round(3)

    gt = {
        "period": [str(START.date()), str(END.date())],
        "totals": {
            "shipments": int(len(t)), "cost_eur": round(float(t.cost_eur.sum()), 0),
            "co2_activity_t": round(float(t.co2_activity_kg.sum()) / 1000, 1),
            "co2_spend_t": round(float(t.co2_spend_kg.sum()) / 1000, 1),
        },
        "S1_avoidable_express": {
            "site": "BOD", "carrier": "RAF",
            "shipments": int((t._reason == "habit_express").sum()),
            "avoidable_cost_eur": round(float(t.loc[t._reason == "habit_express", "_avoidable_cost"].sum()), 0),
            "express_spend_eur": round(float(express.cost_eur.sum()), 0),
            "express_subi_share_of_express_spend": round(float(express.loc[express._reason == "production_delay", "cost_eur"].sum() / express.cost_eur.sum()), 3),
        },
        "S2_consolidation": {
            "road_and_parcel_lane_weeks_with_3plus_shipments": int((groups >= 3).sum()),
            "share_of_road_parcel_shipments_in_those_weeks": round(float(groups[groups >= 3].sum() / groups.sum()), 3),
        },
        "S3_small_carrier": {"lane": "TLS-BOD", "carrier": "TLSUD", "eur_per_kg_by_carrier": per_kg.to_dict()},
        "S4_air_casablanca": {
            "shipments": int((t._reason == "habit_air").sum()),
            "avoidable_cost_eur": round(float(t.loc[t._reason == "habit_air", "_avoidable_cost"].sum()), 0),
        },
        "S6_production_delays": {"share_of_orders_delayed_by_origin": delay_by_site.to_dict(),
                                 "worst_sites": ["CHT", "CAS"]},
        "S7_carrier_degradation": {"carrier": "ERL", "lanes": ["TLS-C-HAM", "EVR-C-MUC"], "from": "2026-03-01",
                                   "late_rate_before": round(float(before), 3), "late_rate_after": round(float(after), 3)},
        "S8_dirty_data": {"missing_weight_share": 0.02, "duplicate_share": 0.005,
                          "carrier_name_variants": True},
        "S9_august_is_seasonal": True,
        "S10_shuttle": {
            "lane": "TLS-CAS", "weekday": "Wednesday", "excluded_family": "FAM-OPT",
            "tls_cas_shipments_by_mode": t[(t.origin == "TLS") & (t.destination == "CAS")]["mode"].value_counts().to_dict(),
            "navette_eur_per_kg": round(float(t.loc[t["mode"] == "navette", "cost_eur"].sum() / t.loc[t["mode"] == "navette", "weight_kg"].sum()), 3),
            "other_fr_to_cas": {
                f"{o}-CAS": {"shipments": int(len(g)), "cost_eur": round(float(g.cost_eur.sum()), 0),
                             "eur_per_kg": round(float(g.cost_eur.sum() / g.weight_kg.sum()), 3)}
                for o, g in t[(t.destination == "CAS") & (t.origin != "TLS")].groupby("origin")
            },
        },
        "client_air_from_maghreb_is_business_rule": True,
    }
    (EVAL / "ground_truth.json").write_text(json.dumps(gt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
