"""Keeps the rule router honest against the eval set: every confident rule decision must be right.
(Low-confidence questions go to the LLM router, which is scored in the Databricks eval task.)"""
import json
from pathlib import Path

import routing as R

ROWS = [json.loads(line) for line in (Path(__file__).resolve().parents[1] / "eval" / "eval_set.jsonl").open()]


def _label(d):
    return f"guardrail:{d.guardrail}" if d.guardrail else "+".join(d.routes)


def test_confident_rule_decisions_match_eval_set():
    wrong = []
    for row in ROWS:
        d = R.rule_route(row["question"])
        if not d.confident:
            continue
        expected = row["expected_route"].split("+")
        actual = _label(d).split("+")
        if actual[0] != expected[0] or not set(expected) <= set(actual):
            wrong.append((row["question"], row["expected_route"], _label(d)))
    assert not wrong, wrong


def test_most_eval_questions_route_without_llm():
    confident = sum(R.rule_route(r["question"]).confident for r in ROWS)
    assert confident / len(ROWS) >= 0.6
