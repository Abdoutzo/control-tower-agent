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

![The agent answering four evaluation questions: each model call, the tools it runs, the verdict](docs/agent_flow.gif)

**At a glance:** asked in French by a transport referent, the agent finds
2.6 MEUR of avoidable express freight (mostly one site and one carrier),
1.1 MEUR of avoidable air freight, 1 MEUR of consolidation savings, an
overpriced local carrier and a degrading one. On 31 scored questions it
passes 29 to 31 depending on the run, refuses every trap, and answers in
about 16 seconds (median) on a free model endpoint.

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

Three full evaluation runs, 31 questions each, all with
`nvidia/nemotron-3.5-super-vl-120b-a12b` on NVIDIA's free endpoint. Raw
answers, reports and animations are in [`eval/results/`](eval/results).

| Run | Reasoning | Fully passed | Median time | Slowest | Output tokens / question | Input tokens / question |
|---|---|---|---|---|---|---|
| [2026-10-18 22:18](eval/results/20261018-2218-nvidia/report.md) | on | 30/31 | 20 s | 154 s | 1,381 | 17,182 |
| [2026-10-18 22:57](eval/results/20261018-2257-nvidia/report.md) | off | 29/31 | 14 s | 110 s | 507 | 15,837 |
| [2026-10-19 20:01](eval/results/20261019-2001-nvidia/report.md) | off | **31/31** | 16 s | 116 s | 588 | 18,871 |

In every run: KPI questions 8/8 and traps 4/4 (forecast, missing data,
delete request, unknown carrier).

**What the runs show**

- Turning the model's hidden reasoning off cuts output tokens by about 60% and
  the median time by 20 to 30%, with no loss of accuracy: 30/31 with
  reasoning, 29 and 31 without.
- The same agent does not answer the same way twice: one or two questions
  change from run to run. A single run is not enough to compare two versions.
- The failures are informative. With reasoning on, asked why air freight from
  Casablanca costs so much, the agent described the spend instead of calling
  the tool that separates avoidable from forced air freight (both later runs
  did, and found that 72% of it, about 1.13 MEUR of savings, was avoidable).
  In the 22:57 run, asked for a transport plan, it gave the analysis but did
  not save the plan; and it picked carrier renegotiation over the Casablanca
  shuttle as third saving lever (defensible, but not what the check expects).

The scorer was corrected twice, in the open (see the commit history): it now
reads "CAS–TLS" and "TLS→BOD" as lane names and accepts per-product-type
delay rates; and the expected figure for the Casablanca question was wrong
(it covered air freight to and from Casablanca across the whole network,
while the question asks about air freight leaving Casablanca). Every change
was re-applied to all runs; the reasoning-on run's Casablanca answer still
fails.

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
