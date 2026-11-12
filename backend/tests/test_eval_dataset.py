"""The benchmark dataset itself is checked: a typo in a scenario would silently make the
benchmark measure nothing (e.g. an expected phrase that isn't in the corpus can never match)."""

import json
from pathlib import Path
from typing import get_args

from app.answers.planning import ToolName
from app.models import MEMORY_KINDS

EVALS = Path(__file__).resolve().parents[1] / "evals"
SCENARIOS = [json.loads(line) for line in (EVALS / "scenarios.jsonl").read_text().splitlines() if line.strip()]
CORPUS = {p.name: p.read_text() for p in (EVALS / "corpus").iterdir()}
MEMORIES = json.loads((EVALS / "memories.json").read_text())


def test_there_are_at_least_50_unique_scenarios_in_every_category() -> None:
    assert len(SCENARIOS) >= 50
    assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS)
    assert {s["category"] for s in SCENARIOS} == {"retrieval", "memory", "planning", "answer"}


def test_retrieval_expectations_exist_in_the_named_document() -> None:
    for s in (s for s in SCENARIOS if s["category"] == "retrieval"):
        assert s["doc"] in CORPUS, s["id"]
        assert s["expect"].lower() in CORPUS[s["doc"]].lower(), s["id"]


def test_memory_expectations_exist_in_the_seed_memories() -> None:
    assert {m["kind"] for m in MEMORIES} <= set(MEMORY_KINDS)
    for s in (s for s in SCENARIOS if s["category"] == "memory"):
        assert any(s["expect"].lower() in m["content"].lower() for m in MEMORIES), s["id"]


def test_planning_scenarios_name_real_tools() -> None:
    for s in (s for s in SCENARIOS if s["category"] == "planning"):
        assert set(s["tools"]) <= set(get_args(ToolName)), s["id"]
        assert 1 <= len(s["tools"]) <= s["max_steps"], s["id"]


def test_answer_expectations_are_findable_somewhere() -> None:
    everything = " ".join(CORPUS.values()) + " " + " ".join(m["content"] for m in MEMORIES)
    for s in (s for s in SCENARIOS if s["category"] == "answer"):
        if s.get("refusal"):
            continue
        assert s.get("expect_all") or s.get("expect_any"), s["id"]
        for keyword in s.get("expect_all", []):
            assert keyword.lower() in everything.lower(), (s["id"], keyword)
        if s.get("expect_any"):
            assert any(k.lower() in everything.lower() for k in s["expect_any"]), s["id"]
