"""Run the TodoTask spec when a TLC jar is configured."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TLCTests(unittest.TestCase):
    def test_todo_task_spec_has_no_error(self) -> None:
        jar = os.environ.get("TLC_JAR")
        if not jar:
            self.skipTest("TLC_JAR is not set")
        spec = os.path.join(ROOT, "spec", "TodoTask.tla")
        with tempfile.TemporaryDirectory() as meta:
            proc = subprocess.run(
                [
                    "java",
                    "-XX:+UseParallelGC",
                    "-jar",
                    os.path.abspath(jar),
                    "-workers",
                    "1",
                    "-metadir",
                    meta,
                    spec,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        output = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn("No error has been found", output)
