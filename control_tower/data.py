"""Load and clean the transport data once, so every tool works on the same view.

Cleaning rules (each one is reported, never silent):
- exact duplicate shipment rows are dropped (same shipment_id);
- carrier names are taken from the carriers reference table via carrier_id,
  because free-text names have spelling variants;
- shipments without a weight are kept for cost totals but excluded from any
  per-kg metric.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "transport.db"


def db_path() -> Path:
    return Path(os.environ.get("CT_DB", DEFAULT_DB))


def connect_readonly() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run python scripts/generate_data.py first")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


@dataclass
class Dataset:
    shipments: pd.DataFrame
    orders: pd.DataFrame
    lanes: pd.DataFrame
    rate_card: pd.DataFrame
    carriers: pd.DataFrame
    emission_factors: pd.DataFrame
    spend_factor: pd.DataFrame
    sites: pd.DataFrame
    families: pd.DataFrame
    quality: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def load() -> Dataset:
    with connect_readonly() as con:
        raw = {name: pd.read_sql(f"SELECT * FROM {name}", con) for name in (
            "shipments", "orders", "lanes", "rate_card", "carriers", "emission_factors",
            "spend_carbon_factor", "sites", "product_families")}

    s = raw["shipments"].copy()
    n_raw = len(s)
    s = s.drop_duplicates(subset="shipment_id", keep="first")
    n_dup = n_raw - len(s)

    carriers = raw["carriers"]
    official = carriers.set_index("carrier_id")["carrier_name"]
    variants = (s.loc[s["carrier_name"] != s["carrier_id"].map(official), "carrier_name"]
                .value_counts().to_dict())
    s["carrier_name"] = s["carrier_id"].map(official)

    for col in ("ship_date", "delivery_date", "need_date"):
        s[col] = pd.to_datetime(s[col])
    s["late"] = s["delivery_date"] > s["need_date"]
    s["late_days"] = (s["delivery_date"] - s["need_date"]).dt.days.clip(lower=0)
    s["month"] = s["ship_date"].dt.to_period("M").astype(str)
    iso = s["ship_date"].dt.isocalendar()
    s["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)

    # Carrier punctuality is judged against the standard transit time of the lane,
    # not against the need date (an order can be late because production was late).
    lead = raw["lanes"][["origin", "destination", "mode", "standard_lead_days"]]
    s = s.merge(lead, on=["origin", "destination", "mode"], how="left")
    s["transit_delay_days"] = ((s["delivery_date"] - s["ship_date"]).dt.days - s["standard_lead_days"]).clip(lower=0)
    s["carrier_late"] = s["transit_delay_days"] > 0

    o = raw["orders"].copy()
    for col in ("order_date", "need_date", "planned_ready_date", "actual_ready_date"):
        o[col] = pd.to_datetime(o[col])
    o["slack_days"] = (o["need_date"] - o["actual_ready_date"]).dt.days
    s = s.merge(o[["order_id", "product_family", "product_type", "actual_ready_date",
                   "production_delay_days", "slack_days"]], on="order_id", how="left")

    quality = {
        "raw_shipment_rows": n_raw,
        "duplicate_rows_removed": n_dup,
        "missing_weight_rows": int(s["weight_kg"].isna().sum()),
        "carrier_name_variants": variants,
    }
    return Dataset(shipments=s, orders=o, lanes=raw["lanes"], rate_card=raw["rate_card"],
                   carriers=carriers, emission_factors=raw["emission_factors"],
                   spend_factor=raw["spend_carbon_factor"], sites=raw["sites"],
                   families=raw["product_families"], quality=quality)
