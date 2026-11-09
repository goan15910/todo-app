"""Bounded Hypothesis exploration of the tracker fake, and scenario coverage. No TLC."""

from __future__ import annotations

import unittest

from proof.tracker.coverage import coverage_holds, report
from proof.tracker.explore import hypothesis_traces
from proof.tracker.scenarios import STACKS, recorded_scenarios


class TrackerHypothesisTests(unittest.TestCase):
    def test_scenarios_agree_across_stacks(self) -> None:
        groups = {stack: recorded_scenarios(stack) for stack, _build in STACKS}
        strip = lambda trace: [  # noqa: E731
            (step.action, step.key, step.number, step.arg, step.num, step.kind, step.post)
            for step in trace.steps
        ]
        for fake, real, http in zip(groups["fake"], groups["sqlite"], groups["http"]):
            with self.subTest(scenario=fake.name):
                self.assertEqual(strip(fake), strip(real))
                self.assertEqual(strip(fake), strip(http))
                self.assertEqual(fake.skipped, 0)
                self.assertEqual(http.steps[0].file, "tracker/entrypoints/fastapi_app.py")

    def test_scenarios_and_hypothesis_cover_every_action_both_ways(self) -> None:
        groups = {"fake": recorded_scenarios("fake"), "hypothesis": hypothesis_traces()}
        self.assertGreater(len(groups["hypothesis"]), 0)
        coverage_holds(groups)
        text = report(groups)
        self.assertIn("committed actions: 14/14", text)
        self.assertIn("refused actions: 14/14", text)
        self.assertIn("status edges: 11/11", text)
