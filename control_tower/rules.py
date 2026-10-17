"""Business rules the agent is allowed to know (as a transport referent would).

These describe how the network works, not the hidden problems.
"""

# Weekly consolidated shuttle, road + ferry
SHUTTLE = {
    "lanes": {("TLS", "CAS")},
    "weekday": 2,  # Wednesday (Monday = 0)
    "transit_days": 5,
    "excluded_families": {"FAM-OPT"},
    "carrier_id": "TMR",
}

# Premium modes: fast and expensive
PREMIUM_MODES = ("express", "aerien")

# Customer flows leaving the Maghreb sites go by air by company rule.
MAGHREB_SITES = {"CAS", "TUN"}

# Modes that can be grouped into one truck when they share a lane and a week.
CONSOLIDABLE_MODES = ("routier", "messagerie")

# Minimum sample size before drawing a conclusion on a carrier x lane.
MIN_SAMPLE = 30
