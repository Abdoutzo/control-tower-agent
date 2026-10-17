# Agent tools

The agent never computes figures "in its head": it calls these tools
(`control_tower/tools.py`). Each one is deterministic and read-only, and
returns `summary`, `data`, `method` and `caveats`, so every answer can show
how it was obtained and what its limits are.

| tool | question it answers |
|---|---|
| `data_overview` | What data do we have, over which period, and what is dirty in it? |
| `kpi` | Standard KPIs (cost, cost per kg, volume, on-time rate, express share, CO2 by method), grouped and filtered |
| `run_sql` | Anything else, with one read-only SELECT on the raw tables |
| `analyze_premium_transport` | Which express or air shipments were avoidable, which were forced by production delays, which follow a business rule |
| `production_delay_impact` | Where production is late, and what it costs in forced premium transport |
| `find_consolidation` | Which road/parcel shipments could have left together in one truck |
| `simulate_shuttle_extension` | What if other sites joined the weekly Toulouse-Casablanca shuttle? |
| `simulate_mode_shift` | What if a lane moved from one mode to another (e.g. air to road)? |
| `carrier_performance` | Cost per kg and punctuality by carrier and lane, with the recent trend |
| `detect_carrier_degradation` | Which carriers became late recently, on which lanes |
| `lane_price_benchmark` | Which carriers charge more than the others on the same lane, weight-adjusted |
| `compare_carbon_methods` | Spend-based vs activity-based CO2, and why they differ |

`tests/test_tools_find_hidden_scenarios.py` checks that the tools recover
the problems hidden in the data, against `eval/ground_truth.json`.
