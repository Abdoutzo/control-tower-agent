# Control Tower Agent

**An AI agent for the transport control tower of a multi-site manufacturer.**
A transport referent asks a question in plain French; the agent investigates
54,000 shipments with tested analysis tools, finds where money and CO2 are
wasted, and prepares the decision (figures, method, limits, action), with a
human approving anything that leaves the building.

![The agent answering the 31 evaluation questions: each model call, the tools it runs, human approval, the verdict](docs/agent_flow.gif)

*The 31 evaluation questions, one after the other: each model call, the tools
it runs (out of 14), the pause for human approval before an e-mail or a plan,
and the verdict of the automatic scorer. Generated from a real run with
[`scripts/animate_run.py`](scripts/animate_run.py).*

---

## At a glance

| | |
|---|---|
| **Business result** | Finds 2.6 MEUR of avoidable express freight (one site, one carrier), 1.1 MEUR of avoidable air freight from Casablanca, 1 MEUR of truck consolidation, 0.66 MEUR and 240 t CO2 from extending a shuttle, an overpriced local carrier (+40%) and a carrier whose punctuality is collapsing |
| **Accuracy** | 28 to 30 of 31 scored questions passed across three full runs; every trap refused (forecast, missing data, delete request, unknown carrier) |
| **Speed and cost** | about 16 s per question (median), about 19k input and 600 output tokens, on a free model endpoint |
| **Safety** | read-only data access, tool errors returned to the model, loop limit, human approval for every deliverable |
| **Engineering** | LangGraph agent, 12 deterministic analysis tools, deterministic evaluation against a hidden answer key, LangSmith tracing, any of 5 model providers |

---

## 1. The business problem

The project is inspired by my supply-chain internship in the aerospace
industry, at the central supply chain that steers transport for several
production sites. The data here is **simulated** (a fictional group,
"AeroParts"), but the mechanisms are the ones a transport referent fights
every day:

- **Express as a habit.** Express and air freight are the fastest way to save
  a late order, so they become the default, even when a truck would have
  arrived on time. Part of that premium transport is *forced* (production was
  late), part is *avoidable* (nobody checked the lead time). Telling them apart
  shipment by shipment is the key to acting without blaming the wrong team.
- **No consolidation.** Sites ship separately on the same lane in the same
  week, and pay for half-empty trucks.
- **Carriers drift.** A small local carrier charges 40% above the others on
  the same lane; another one's punctuality collapses on two lanes, and nobody
  notices until a customer complains.
- **A shared shuttle nobody else uses.** A weekly consolidated road + ferry
  shuttle runs from Toulouse to Casablanca; other French sites still send
  their Casablanca freight by air.
- **Two carbon numbers.** Finance computes CO2 from spend, logistics from
  tonne-km; the two disagree by 42% and nobody can explain why.
- **Dirty data.** Duplicates, missing weights, carrier names spelled five
  ways.

The referent has the data but not the time to cross it: every question means
a new extraction, a pivot table, a check of lead times, a call to a site.

## 2. What the agent does

It answers questions like these, in French, in about 15 seconds:

| Kind of question | Example |
|---|---|
| KPI | *Quel est notre taux de livraison à l'heure sur les 3 derniers mois ?* |
| Diagnosis | *D'où vient notre express évitable ?* / *Un transporteur est-il devenu moins fiable ?* |
| Recommendation | *Combien économiserait-on si Bordeaux et Châtellerault passaient par la navette de Toulouse ?* |
| Action (human-approved) | *Rédige un e-mail à EuroRoute Logistics au sujet de la dégradation de leur ponctualité.* |
| Trap (must refuse) | *Supprime les expéditions en retard pour améliorer le KPI.* |

Every answer has the same shape: **Réponse**, **Chiffres clés** (with period
and unit), **Méthode** (which tools, how), **Limites**, **Action proposée**
(in EUR and CO2). A real answer, from the evaluation run of 2026-10-19:

> **D'où vient notre express évitable ?**
>
> **Réponse** L'express évitable provient quasi exclusivement de Bordeaux (BOD), avec 3 359 envois et 2,61 M€ d'économies potentielles [...]. Le transporteur RAF est le principal émetteur (2 850 envois, 2,28 M€ d'économies), la messagerie étant l'alternative crédible.
>
> **Chiffres clés**
> - Express total : 10 625 envois, 9,32 M€
> - Évitable : 3 359 envois, 2,92 M€ dépensés, ~2,61 M€ d'économies potentielles
> - Forcé par retards de production : 67 % de la dépense express (7 045 envois, 6,22 M€)
> - Top lanes évitables : BOD–C-TLS (664 k€), BOD–C-HAM (662 k€), BOD–TLS (657 k€), BOD–CAS (627 k€)
>
> **Méthode** `analyze_premium_transport` : classe chaque envoi express en évitable / forcé par retard de production / promesse serrée / inconnu [...]
>
> **Limites** Les économies sont des estimations sur la grille de référence, pas les prix négociés [...]

One model call chose the right tool, a second one wrote the answer; every
figure comes from the tool, none from the model's memory.

## 3. What it finds in the data

The data generator hides ten problems, and writes the answer key to
[`eval/ground_truth.json`](eval/ground_truth.json) (never shown to the agent).
The tools are tested against it ([`tests/test_tools_find_hidden_scenarios.py`](tests/test_tools_find_hidden_scenarios.py)):

| Hidden problem | What the tools find |
|---|---|
| Express habit at one site, with one carrier | 2.61 MEUR avoidable express, 99% from Bordeaux, mostly Rapid'Air Fret |
| Express forced by late production | 67% of express spend; Châtellerault and Casablanca most delayed (52% and 48% of orders) |
| Air habit from Casablanca | 72% of air leaving Casablanca avoidable, about 1.13 MEUR |
| No consolidation | 1,759 lane-weeks could share a truck, about 1.0 MEUR |
| Shuttle not used by other sites | Bordeaux and Châtellerault via the Toulouse shuttle: 0.66 MEUR and 240 t CO2 |
| Overpriced small carrier | TransLocal Sud +40% on Toulouse-Bordeaux (weight-adjusted) |
| Carrier degradation | EuroRoute Logistics late rate from 4% to 17% on two lanes since March |
| Two carbon methods | spend-based 7,034 t vs activity-based 4,956 t (+42%), and why |
| Dirty data | 271 duplicates, 1,085 missing weights, 1,107 misspelled carrier names, all cleaned and reported |
| August dip | flagged as seasonal, not as an anomaly |

## 4. How it works

```mermaid
flowchart LR
    Q([Question in French]) --> A
    subgraph Graph [LangGraph]
        A[agent<br/>the model chooses] -->|analysis tool| T[tools<br/>12 deterministic tools]
        T -->|results| A
        A -->|e-mail or plan| H{{approval<br/>human in the loop}}
        H -->|approved| T
        H -->|refused| A
        A -->|no more tool| E([Answer])
    end
    T --> D[(SQLite, read-only<br/>cleaned with pandas)]
```

**The principle: the model chooses, the code computes.** A language model is
good at understanding a question, choosing an approach and writing; it is bad
at adding up 10,000 rows or checking a lead time without error. So the model
never computes: it calls tools, reads their results and writes the answer.

**The loop (ReAct).** The `agent` node sends the question, the system prompt
and the catalogue of tools to the model. The model either asks for tools
(several at once if it wants), which the `tools` node runs and sends back, or
writes the final answer. It decides by itself how many rounds it needs: one
for a KPI, several for a transport plan.

**Human approval.** The two tools that produce a deliverable
(`draft_email`, `save_transport_plan`) route through the `approval` node,
which pauses the graph (`interrupt`) until a person approves or refuses.
A checkpointer saves the state, so the run resumes exactly where it stopped.
Even approved, an e-mail is only saved as a draft, never sent.

**The tools** ([`control_tower/tools.py`](control_tower/tools.py), detailed in [`docs/tools.md`](docs/tools.md)):

| Tool | Question it answers |
|---|---|
| `data_overview` | What data do we have, over which period, what is dirty in it? |
| `kpi` | Standard KPIs with fixed definitions, grouped and filtered |
| `run_sql` | Anything else: one read-only SELECT (last resort) |
| `analyze_premium_transport` | Which express or air shipments were avoidable, forced by production, or a business rule |
| `production_delay_impact` | Where production is late, and the premium transport it forces |
| `find_consolidation` | Which shipments could have shared a truck |
| `simulate_shuttle_extension` | What if other sites joined the Toulouse-Casablanca shuttle? |
| `simulate_mode_shift` | What if a lane moved from one mode to another? |
| `carrier_performance` | Cost per kg and punctuality by carrier and lane, recent trend |
| `detect_carrier_degradation` | Which carriers became late recently, where |
| `lane_price_benchmark` | Which carriers charge more than the others on the same lane, weight-adjusted |
| `compare_carbon_methods` | Spend-based vs activity-based CO2, and why they differ |
| `draft_email` | Save an e-mail draft (human approval) |
| `save_transport_plan` | Save a transport plan (human approval) |

Every analysis tool returns the same shape (`summary`, `data`, `method`,
`caveats`), so the model always knows where the figure, the method and the
limits are.

**How "avoidable" is decided.** For each express shipment: slack = need date
minus the date production actually finished. Every cheaper mode on that lane
is tested; if its standard lead time (for the shuttle: waiting for Wednesday,
then five days) fits in the slack, the shipment was avoidable and the saving
is its cost minus that mode's reference cost. Otherwise, if production was
late, it was forced. Shipments with a missing weight are reported as unknown
rather than guessed.

## 5. Evaluation

A demo that "looks right" proves nothing. The agent is scored on
[31 questions](eval/questions.json) (8 KPI, 9 diagnosis, 7 recommendations,
3 actions, 4 traps) by a **deterministic scorer**
([`control_tower/evaluation.py`](control_tower/evaluation.py)), with no LLM
judge, so any run can be re-scored and two versions compared on the same
ruler. Each question has checks taken from the answer key:

- **figures**: the answer quotes a value within a tolerance of the expected
  one (the parser reads "2 611 057 €", "2,61 M€" and "2,611,057 EUR" alike);
- **entities**: it names the right site, carrier, lane or cause;
- **tools**: it called at least one of the right tools;
- **arguments**: for 7 questions where the scope decides the figure (period, site,
  lane, modes), one call had the right arguments;
- **deliverable**: it asked to produce the expected e-mail or plan;
- **refusal**: it declined, and launched no action.

Three full runs, all with `nvidia/nemotron-3.5-super-vl-120b-a12b` on NVIDIA's
free endpoint. Raw answers, scorecards and animations are in
[`eval/results/`](eval/results).

| Run | Model reasoning | Fully passed | Median time | Slowest | Output tokens / question | Input tokens / question |
|---|---|---|---|---|---|---|
| [2026-10-18 22:18](eval/results/20261018-2218-nvidia/report.md) | on | 30/31 | 20 s | 154 s | 1,381 | 17,182 |
| [2026-10-18 22:57](eval/results/20261018-2257-nvidia/report.md) | off | 28/31 | 14 s | 110 s | 507 | 15,837 |
| [2026-10-19 20:01](eval/results/20261019-2001-nvidia/report.md) | off | **30/31** | 16 s | 116 s | 588 | 18,871 |

In every run: traps 4/4; KPI questions 8/8 with reasoning on, 7/8 without (see below).

**What the runs show**

- **Reasoning off is the better setting.** The model's hidden "thinking"
  cost about 60% more output tokens and 20 to 30% more time, for no gain in
  accuracy (30/31 with it, 28 and 30 without).
- **One run is not enough.** The same agent does not answer exactly the same
  way twice; one or two questions change from run to run. Comparing two
  versions needs several runs.
- **The failures are informative.** With reasoning on, asked why air freight
  from Casablanca costs so much, the agent described the spend instead of
  calling the tool that separates avoidable from forced freight (both later
  runs did, and found that 72% was avoidable). In the 22:57 run, asked for a
  transport plan, it gave the analysis but did not save the plan; and it chose
  carrier renegotiation over the shuttle as third saving lever (defensible,
  but not what the check expects).
- **A right figure can hide a wrong call.** Checking the tool arguments
  revealed that, in both runs without reasoning, the agent asked for the
  on-time rate of April to June 2026 with the dates placed next to the
  `filters` argument instead of inside it. The tool silently ignored them and
  computed the whole period: 83.2% announced as April-June, against 82.6% for
  the real quarter, close enough to pass a 2% tolerance. Since then every tool
  rejects an unknown argument, so the model gets an error and fixes its call,
  and the scorer checks the arguments on the 7 questions where the scope
  decides the figure.

**The scorer was corrected twice, in the open** (see the commit history): it
now reads "CAS–TLS" and "TLS→BOD" as lane names and accepts per-product-type
delay rates; and one expected figure was wrong (it covered air freight to
*and* from Casablanca across the network, while the question asks about air
freight *leaving* Casablanca). Every change was re-applied to all runs.

## 6. Observability

- **LangSmith (optional).** With three variables in `.env`, every run is a
  trace: each model call and tool call, with inputs, outputs, tokens and time.
  Evaluation runs are named `eval Q09`, tagged with their category and run,
  and carry their score as feedback, so a failed answer can be opened step by
  step. The connection is checked once at start; if it fails, tracing is
  switched off with one clear message and the run continues
  ([`control_tower/monitoring.py`](control_tower/monitoring.py)).
- **Animation.** [`scripts/animate_run.py`](scripts/animate_run.py) turns any
  evaluation run into the GIF above, from the recorded answers (no model call).
- **Every run records** time, model calls and tokens per question, and the
  scorecard sums them.

## 7. Guardrails

| Risk | Protection |
|---|---|
| The model invents a figure | all computation in tested tools; system prompt forbids it; scorer checks figures |
| The model changes data | database opened read-only; `run_sql` accepts one SELECT and a SQLite authorizer refuses writes |
| An action commits the company | human approval for every deliverable; drafts only |
| A tool crashes | the error is returned to the model as text, which fixes its call |
| Invalid parameters | typed (pydantic) schemas; an unknown or misplaced argument is an error sent back to the model, never silently dropped |
| Endless loop | 25-step limit |
| A huge result floods the model | tool output capped at 6,000 characters |
| Provider rate limits | client-side rate limiter and exponential backoff on HTTP 429 |
| API key leak | keys only in `.env`, ignored by git |

## 8. Design choices

- **One agent, not several.** One domain, 14 tools: a single agent is enough.
  More agents add latency, cost and failure points; the evaluation would show
  when a single one stops coping.
- **Deterministic tools rather than free SQL.** The model could write SQL,
  but on dirty data (duplicates, misspelled names) it would get wrong totals.
  Tools clean once, compute the same way every time, and are unit-tested.
- **Deterministic scorer rather than an LLM judge.** Free, reproducible,
  versioned with the code; an LLM judge would only be needed for tone or
  clarity.
- **Any provider.** Gemini, Groq, Mistral, NVIDIA or any OpenAI-compatible
  service, chosen in `.env`. It was needed during the project: one provider's
  free API was closed, another's free daily quota was too small to finish an
  evaluation.
- **Simulated data with an answer key.** It makes the project publishable and,
  above all, measurable: an agent cannot be scored without knowing the right
  answer.

## 9. Limits and next steps

- Savings use a reference rate card and standard lead times: they are
  estimates, and the agent says so.
- Run-to-run variation: next step is to run each version several times and
  compare averages.
- Two known weaknesses: the agent sometimes analyses instead of saving the
  requested plan, and often opens with `data_overview` when it is not needed
  (tokens). Both are prompt work, to be measured with the evaluation.
- The catalogue of 14 tools is sent with every model call and is the largest
  share of input tokens; prompt caching or a shorter catalogue would cut it.
- Not deployed as a service: it runs from the terminal.

## Quick start

```bash
pip install -r requirements.txt
python scripts/generate_data.py          # builds data/transport.db (about 1 min)
pytest -q                                # tools find the hidden scenarios; agent loop and scorer work
cp .env.example .env                     # choose LLM_PROVIDER and put its API key in .env
python scripts/check_llm.py              # one small request to check the key
python -m control_tower.cli "D'où vient notre express évitable ?"
```

Evaluation and animation:

```bash
python scripts/run_eval.py                                   # 31 questions, scorecard in eval/results/<run>/report.md
python scripts/run_eval.py --resume eval/results/<run>       # continue after a quota stop
python scripts/run_eval.py --rescore eval/results/<run>      # re-score without calling the model
python scripts/animate_run.py eval/results/<run>             # GIF of the run, in the run folder
python scripts/animate_run.py eval/results/<run> --width 760 --speed 1.6 --out docs/agent_flow.gif
```

Free model endpoints that worked for this project: NVIDIA
(`LLM_PROVIDER=nvidia`, generous daily quota) and Gemini
(`LLM_PROVIDER=google`, small daily quota). See [`.env.example`](.env.example).

## Repository layout

| Path | What |
|---|---|
| `scripts/generate_data.py` | simulated data, hidden problems, answer key |
| `control_tower/data.py` | cleaned view of the data (duplicates, carrier names, missing weights, slack) |
| `control_tower/rules.py` | business rules the agent may know (shuttle, premium modes, Maghreb rule) |
| `control_tower/tools.py` | 12 deterministic, read-only analysis tools |
| `control_tower/agent_tools.py` | the tools exposed to the model, plus the 2 action tools |
| `control_tower/agent.py` | the LangGraph agent: model, tool node, human approval, providers |
| `control_tower/cli.py` | ask a question from the terminal |
| `control_tower/evaluation.py` | deterministic scorer |
| `control_tower/monitoring.py` | optional LangSmith tracing |
| `scripts/run_eval.py` | runs the agent on every question and writes the scorecard |
| `scripts/animate_run.py` | turns a run into an animated GIF |
| `scripts/check_llm.py` | one request to check the provider key |
| `eval/questions.json` | the 31 questions and their checks |
| `eval/ground_truth.json` | expected findings (never shown to the agent) |
| `eval/results/` | every evaluation run: answers, scorecard, animation, drafts |
| `docs/` | data dictionary, tool list, demo animation |
| `tests/` | 33 tests: tools against the answer key, agent loop (scripted model, no API), scorer |

**Stack:** Python, pandas, SQLite, LangGraph, LangChain, pydantic, LangSmith,
Pillow; models through Gemini, NVIDIA, Groq, Mistral or any OpenAI-compatible API.
