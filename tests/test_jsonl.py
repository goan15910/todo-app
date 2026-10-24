"""A JSON Lines log of the b-full flow checks as a spec trace."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import todo_app.task as tasks
from event_alignment import map_log, read_jsonl, record_from_event, trace, trace_config, trace_module
from event_alignment import AlignmentError, SpecState
from test_alignment import INVARIANTS, _assert_actions_covered, _fields, _run_tlc, _with_title
from todo_app.alignment import todo_alignment
from todo_app.task import TodoTask, TransitionRejected

_KEYS = {"kind", "qualname", "thread_id", "thread_name", "file", "line"}
_OPTIONAL = {"value", "exception", "arg0", "task"}


def _b_full(item: TodoTask) -> None:
    when = datetime(2026, 1, 2, tzinfo=timezone.utc)
    try:
        item.submit()
    except TransitionRejected:
        pass
    else:
        raise AssertionError("empty submit should be rejected")
    item.set_title("buy milk")
    item.submit()
    item.start()
    item.snooze(when)
    item.resume()
    item.complete()
    item.archive()
    item.unarchive()
    item.reopen()
    item.delete()
    item.restore()


class JsonlTraceTests(unittest.TestCase):
    def test_b_full_log_checks_against_the_spec(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("")
            run.bind(item)
            _b_full(item)
        lines = [json.dumps(record_from_event(event)) for event in run.events]
        self.assertGreater(len(lines), 10)
        for line in lines:
            payload = json.loads(line)
            self.assertTrue(_KEYS <= set(payload) <= _KEYS | _OPTIONAL)
        logged = read_jsonl(lines)
        initial = SpecState(_with_title("task"))
        mapped = map_log(logged, todo_alignment, initial)
        live = run.mapped()

        self.assertEqual(
            [(step.action, _fields(step.post)) for step in mapped.steps],
            [(step.action, _fields(step.post)) for step in live.steps],
        )
        self.assertEqual(_fields(mapped.init), _fields(live.init))
        self.assertEqual(
            mapped.actions(),
            (
                "DraftSubmit",
                "TodoStart",
                "InProgressSnooze",
                "SnoozedResume",
                "InProgressComplete",
                "DoneArchive",
                "ArchivedUnarchive",
                "DoneReopen",
                "TodoDelete",
                "DeletedRestore",
            ),
        )
        self.assertTrue(mapped.states()[-1]["submitted"])
        self.assertEqual(mapped.states()[-1]["state"], "todo")
        self.assertEqual(mapped.states()[-1]["deleted_at"], "unset")
        self.assertTrue(
            any(event.kind == "PY_UNWIND" and event.qualname == "TodoTask.submit" for event in logged)
        )

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
                            "callable": "submit",
                        }
                    )
                ]
            )


if __name__ == "__main__":
    unittest.main()
