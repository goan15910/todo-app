"""TLC accepts a recorded scenario and rejects a wrong mapping.

Skipped unless TLC_JAR is set, so the plain unit-test job does not need Java.
"""

from __future__ import annotations

import os
import pathlib
import unittest

from proof.negatives import negative_cases
from proof.scenarios import recorded_scenarios
from proof.todo_trace import validate_one


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("TLC_JAR"), "TLC_JAR is not set")
class TraceValidationTests(unittest.TestCase):
    def test_scenario_1_is_accepted(self) -> None:
        jar = os.environ["TLC_JAR"]
        scenarios = recorded_scenarios()
        self.assertEqual(scenarios[0].name, "scenario-1")
        verdict = validate_one(scenarios[0], jar)
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
                self.assertIn(case.trace.name, verdict.message)
                source = (ROOT / step.file).read_text(encoding="utf-8").splitlines()
                self.assertIn("service.", source[step.line - 1])
                if case.kind == "guard":
                    self.assertIn("taken while the guard was false", verdict.message)
                    self.assertIn("submit", source[step.line - 1])
                else:
                    self.assertIn("resulting state does not match", verdict.message)
