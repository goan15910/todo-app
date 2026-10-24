"""The recorder command's JSON Lines log checks as spec traces."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from event_alignment import AlignmentError, read_jsonl, trace_config, trace_module
from test_alignment import INVARIANTS, ROOT, _assert_actions_covered, _fields, _run_tlc, _with_title
from todo_app.alignment import ACTIONS, task_traces

# One behavior per TodoTask constructed by `python -m execution_events`.
_WALKS = (
    (
        "DraftSubmit",
        "TodoStart",
        "InProgressSnooze",
        "SnoozedResume",
        "InProgressComplete",
        "DoneReopen",
        "TodoComplete",
        "DoneArchive",
        "ArchivedUnarchive",
    ),
    ("DraftSubmit", "TodoComplete"),
    ("DraftSubmit", "TodoSnooze"),
    ("DraftSubmit", "TodoDelete", "DeletedRestore"),
    ("DraftDelete",),
    ("DraftSubmit", "TodoStart", "InProgressComplete"),
    (),
)


class JsonlTraceTests(unittest.TestCase):
    def test_landed_command_log_checks_against_the_spec(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "execution_events"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        logged = read_jsonl(proc.stdout.splitlines())
        self.assertGreater(len(logged), 10)
        for event in logged:
            self.assertTrue(event.file.endswith("todo_app/task.py"))

        traces = task_traces(logged)
        self.assertEqual(tuple(trace.actions() for trace in traces), _WALKS)
        self.assertEqual(
            {action for trace in traces for action in trace.actions()},
            set(ACTIONS.values()),
        )

        self.assertEqual(_fields(traces[0].init), _with_title("task"))
        self.assertEqual(traces[0].states()[-1]["state"], "done")
        self.assertTrue(traces[0].states()[-1]["submitted"])
        self.assertTrue(traces[0].states()[-1]["listed"])
        self.assertEqual(traces[0].states()[-1]["completed_at"], "set")

        self.assertEqual(_fields(traces[4].init), _with_title(""))
        self.assertEqual(traces[4].states()[-1]["state"], "deleted")
        self.assertFalse(traces[4].states()[-1]["submitted"])
        self.assertEqual(traces[4].states()[-1]["deleted_at"], "set")

        self.assertEqual(traces[6].steps, ())
        self.assertEqual(_fields(traces[6].init), _with_title(""))
        self.assertTrue(
            any(
                event.kind == "PY_UNWIND"
                and event.qualname == "TodoTask.submit"
                and event.exception is not None
                and "titleNotEmpty" in event.exception
                for event in logged
            )
        )
        self.assertFalse(
            any(
                event.kind == "PY_RETURN" and event.qualname == "TodoTask.submit" and event.exception
                for event in logged
            )
        )

        for mapped in traces:
            result = _run_tlc(trace_module(mapped), trace_config(INVARIANTS))
            self.assertEqual(result.code, 0, result.output[-2000:])
            self.assertIn("No error has been found.", result.output)
            self.assertIn(
                f"The depth of the complete state graph search is {len(mapped.states())}.",
                result.output,
            )
            _assert_actions_covered(self, result.output, mapped.actions())

    def test_reader_rejects_a_different_shape(self) -> None:
        with self.assertRaises(AlignmentError):
            read_jsonl(['{"kind": "PY_RETURN", "qualname": "TodoTask.submit"}\n'])
        with self.assertRaises(AlignmentError):
            read_jsonl(
                [
                    json.dumps(
                        {
                            "kind": "PY_RETURN",
                            "qualname": "TodoTask.submit",
                            "thread_id": 1,
                            "thread_name": "MainThread",
                            "file": "task.py",
                            "line": 1,
                            "roles": ["return"],
                        }
                    )
                ]
            )

        parsed = read_jsonl(
            [
                json.dumps(
                    {
                        "kind": "CALL",
                        "qualname": "TodoTask.submit",
                        "thread_id": 1,
                        "thread_name": "MainThread",
                        "file": "todo_app/task.py",
                        "line": 4,
                        "arg0": "TodoTask(state='draft', title='task', listed=False)",
                        "callable": "todo_app.task.TodoTask._apply",
                    }
                )
            ]
        )
        self.assertEqual(parsed[0].callable, "todo_app.task.TodoTask._apply")
        self.assertIsNone(parsed[0].value)


if __name__ == "__main__":
    unittest.main()
