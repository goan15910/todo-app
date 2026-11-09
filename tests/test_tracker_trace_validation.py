"""TLC accepts recorded tracker traces through every stack and rejects wrong mappings.

Skipped unless TLC_JAR is set, so the plain unit-test job does not need Java.
"""

from __future__ import annotations

import os
import pathlib
import unittest

from proof.tracker.negatives import negative_cases
from proof.tracker.scenarios import recorded_scenarios
from proof.tracker.trace import validate_all, validate_one


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("TLC_JAR"), "TLC_JAR is not set")
class TrackerTraceValidationTests(unittest.TestCase):
    def test_scenario_1_is_accepted_through_every_stack(self) -> None:
        jar = os.environ["TLC_JAR"]
        traces = [recorded_scenarios(stack)[0] for stack in ("fake", "sqlite", "http")]
        for verdict in validate_all(traces, jar):
            self.assertTrue(verdict.ok, verdict.message)

    def test_wrong_mappings_stick_at_the_call(self) -> None:
        jar = os.environ["TLC_JAR"]
        for case in negative_cases():
            with self.subTest(trace=case.trace.name):
                verdict = validate_one(case.trace, jar)
                step = case.trace.steps[case.stuck_index]
                self.assertFalse(verdict.ok, verdict.message)
                self.assertEqual(verdict.kind, case.kind)
                self.assertEqual(verdict.line, step.line)
                self.assertEqual(verdict.file, step.file)
                self.assertIn(f"line {step.line}", verdict.message)
                source = (ROOT / step.file).read_text(encoding="utf-8").splitlines()
                self.assertIn(case.method, source[step.line - 1])
