"""Bounded Hypothesis exploration of the in-memory fake. No TLC."""

from __future__ import annotations

import unittest

from proof.coverage import coverage_holds, has_empty_title, report
from proof.explore import hypothesis_traces
from proof.scenarios import recorded_scenarios


class HypothesisModelTests(unittest.TestCase):
    def test_scenarios_and_hypothesis_cover_actions_and_edges(self) -> None:
        scenarios = recorded_scenarios()
        explored = hypothesis_traces()
        self.assertGreaterEqual(len(scenarios), 5)
        self.assertGreater(len(explored), 0)
        self.assertTrue(has_empty_title(explored))
        coverage_holds(scenarios, explored)
        text = report(scenarios, explored)
        self.assertIn("actions: 14/14", text)
        self.assertIn("status edges: 13/13", text)