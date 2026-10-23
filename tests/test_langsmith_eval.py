"""The LangSmith bridge, tested offline: scorecard as feedback, dataset sync with a fake client."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import langsmith_eval as L  # noqa: E402

from control_tower.evaluation import load_questions  # noqa: E402


def q(qid):
    return next(x for x in load_questions() if x["id"] == qid)


def test_feedback_has_one_key_per_kind_of_check():
    run = {"answer": "83,2 %", "tool_calls": ["kpi"], "actions": [],
           "steps": [{"tool": "kpi", "args": {"metric": "on_time_rate", "start": "2026-04-01"}}]}
    fb = {r["key"]: r for r in L.feedback(q("Q02"), run)}
    assert fb["passed"]["score"] == 0 and "bons arguments" in fb["passed"]["comment"]
    assert (fb["figures"]["score"], fb["tool"]["score"], fb["arguments"]["score"]) == (0, 1, 0)


def test_pass_rate_by_category():
    good = {"answer": "Je ne peux pas prévoir l'avenir.", "tool_calls": [], "actions": []}
    bad = {"answer": "Rien.", "tool_calls": [], "actions": []}
    examples = [SimpleNamespace(inputs={"question": q(i)["question"]}) for i in ("Q28", "Q01")]
    summary = {r["key"]: r["score"] for r in L.pass_rate([good, bad], examples)["results"]}
    assert summary["pass_rate"] == 0.5 and summary["pass_rate_piege"] == 1 and summary["pass_rate_kpi"] == 0


class FakeClient:
    def __init__(self):
        self.examples: dict = {}

    def has_dataset(self, dataset_name):
        return bool(self.examples)

    def read_dataset(self, dataset_name):
        return SimpleNamespace(id="ds")

    def create_dataset(self, name, description):
        return SimpleNamespace(id="ds")

    def list_examples(self, dataset_id):
        return list(self.examples.values())

    def create_example(self, inputs, outputs, metadata, split, dataset_id):
        i = uuid.uuid4()
        self.examples[i] = SimpleNamespace(id=i, inputs=inputs, outputs=outputs, metadata=metadata, split=split)

    def update_example(self, example_id, inputs, outputs, metadata, split):
        self.examples[example_id] = SimpleNamespace(id=example_id, inputs=inputs, outputs=outputs,
                                                    metadata=metadata, split=split)

    def delete_example(self, example_id):
        del self.examples[example_id]


def test_sync_is_idempotent_and_follows_the_question_file(capsys):
    client, questions = FakeClient(), load_questions()
    L.sync(client, questions)
    assert len(client.examples) == len(questions)
    L.sync(client, questions)
    assert "0 created, 0 updated, 0 deleted" in capsys.readouterr().out.splitlines()[-1]

    edited = [dict(x, checks={"refusal": True}) if x["id"] == "Q01" else x for x in questions[:-1]]
    L.sync(client, edited)
    assert "0 created, 1 updated, 1 deleted" in capsys.readouterr().out
    assert len(client.examples) == len(questions) - 1
