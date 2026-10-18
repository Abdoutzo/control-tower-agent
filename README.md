# Control Tower Agent

An AI agent for the central transport referent of a multi-site manufacturer:
it investigates shipments, finds avoidable express and air freight,
consolidation and mode-shift opportunities, overpriced or degrading
carriers, and compares two carbon accounting methods. Every figure comes
from tested analysis tools; actions (e-mail drafts, transport plans) wait
for human approval.

Data is simulated (fictional group "AeroParts": 9 sites, ~54k shipments,
~12 MEUR of transport per year) with hidden problems and an answer key, so
the agent can be scored.

## Quick start

```bash
pip install -r requirements.txt
python scripts/generate_data.py          # builds data/transport.db (~1 min)
pytest -q                                # tools find the hidden scenarios; agent loop works
cp .env.example .env                     # then choose LLM_PROVIDER and put its API key in .env
python scripts/check_llm.py              # one small request to check the key
python -m control_tower.cli "D'où vient notre express évitable ?"
```

## Evaluation

```bash
python scripts/run_eval.py                 # asks the 31 questions of eval/questions.json, scores every answer
python scripts/run_eval.py --resume eval/results/<run>   # continue after a quota stop
python scripts/animate_run.py eval/results/<run>          # GIF: each question travelling through the graph
```

Scoring is deterministic (no LLM judge): expected figures with a tolerance,
entities to name (site, carrier, lane, cause), tools to call, deliverable or
refusal expected. The scorecard lands in `eval/results/<run>/report.md`.

## Results

First full evaluation run (31 questions, `nvidia/nemotron-3.5-super-vl-120b-a12b` on NVIDIA's free endpoint):

| Category | Passed | Target |
|---|---|---|
| KPI questions | 8/8 | 90% |
| Diagnosis (root cause) | 8/9 | 70% |
| Recommendations (savings in EUR and CO2) | 7/7 | 70% |
| Actions (e-mail drafts, transport plan, human-approved) | 3/3 | 70% |
| Traps (forecast, missing data, delete request, unknown carrier) | 4/4 | 100% |

**30/31 fully passed, mean score 98%.** Median 20 s per question, 2.5 model calls and
about 17k input tokens per question.

The one failure is real and is the next improvement: asked why air freight from
Casablanca costs so much, the agent described the spend instead of calling the
tool that separates avoidable from forced air freight, so it missed the
avoidable part (about 1.9 MEUR).

The scorer was corrected once after this run, in the open (commit history):
it now reads "CAS–TLS" and "TLS→BOD" as lane names, accepts per-product-type
delay rates, and was made stricter on the Casablanca question, where the total
air spend had been mistaken for the avoidable part.

## Monitoring (LangSmith, optional)

Uncomment the `LANGSMITH_*` lines of `.env` and every run becomes a trace in
LangSmith: each model call and tool call with its inputs, outputs, tokens and
time. Evaluation runs are named `eval Q09`, tagged with their category and
run folder, and carry their score as feedback, so failed answers can be
filtered and replayed step by step. With the variables unset, nothing is sent.

## Layout

| path | what |
|---|---|
| `scripts/generate_data.py` | simulated data + hidden scenarios + answer key |
| `control_tower/data.py` | cleaned view of the data (duplicates, carrier names, missing weights) |
| `control_tower/tools.py` | 12 deterministic, read-only analysis tools |
| `control_tower/agent_tools.py` | tools exposed to the LLM, plus 2 action tools needing approval |
| `control_tower/agent.py` | LangGraph agent: LLM, tool node, human approval, recursion limit |
| `control_tower/cli.py` | ask a question from the terminal |
| `docs/` | data dictionary, tool list |
| `eval/ground_truth.json` | expected findings (never shown to the agent) |
| `eval/questions.json` | the 31 evaluation questions and their checks |
| `control_tower/evaluation.py` | deterministic scorer |
| `control_tower/monitoring.py` | optional LangSmith tracing: run names, tags, scores |
| `scripts/run_eval.py` | runs the agent on every question and writes the scorecard |
