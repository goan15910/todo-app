"""Walk every b-full edge of the Python task machine."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from todo_app.task import EDGES, TodoTask

WAKE = datetime(2026, 10, 25, tzinfo=timezone.utc)

_PREP: dict[tuple[str, str], tuple[str, ...]] = {
    ("draft", "SUBMIT"): (),
    ("draft", "DELETE"): (),
    ("todo", "START"): ("SUBMIT",),
    ("todo", "COMPLETE"): ("SUBMIT",),
    ("todo", "SNOOZE"): ("SUBMIT",),
    ("todo", "DELETE"): ("SUBMIT",),
    ("inProgress", "SNOOZE"): ("SUBMIT", "START"),
    ("inProgress", "COMPLETE"): ("SUBMIT", "START"),
    ("snoozed", "RESUME"): ("SUBMIT", "START", "SNOOZE"),
    ("done", "REOPEN"): ("SUBMIT", "START", "COMPLETE"),
    ("done", "ARCHIVE"): ("SUBMIT", "START", "COMPLETE"),
    ("archived", "UNARCHIVE"): ("SUBMIT", "START", "COMPLETE", "ARCHIVE"),
    ("deleted", "RESTORE"): ("SUBMIT", "DELETE"),
}


def _apply(task: TodoTask, event: str) -> None:
    if event == "SUBMIT":
        task.submit()
    elif event == "DELETE":
        task.delete()
    elif event == "START":
        task.start()
    elif event == "COMPLETE":
        task.complete()
    elif event == "SNOOZE":
        task.snooze(WAKE)
    elif event == "RESUME":
        task.resume()
    elif event == "REOPEN":
        task.reopen()
    elif event == "ARCHIVE":
        task.archive()
    elif event == "UNARCHIVE":
        task.unarchive()
    elif event == "RESTORE":
        task.restore()
    else:
        raise AssertionError(f"unknown event {event}")


def _reach(source: str, event: str) -> TodoTask:
    task = TodoTask("task")
    for step in _PREP[(source, event)]:
        _apply(task, step)
    return task


class EdgeWalkTests(unittest.TestCase):
    def test_all_thirteen_edges_fire(self) -> None:
        self.assertEqual(len(EDGES), 13)
        self.assertEqual(len(_PREP), 13)
        for edge in EDGES:
            with self.subTest(source=edge.source, event=edge.event, target=edge.target):
                task = _reach(edge.source, edge.event)
                self.assertEqual(task.state, edge.source)
                _apply(task, edge.event)
                self.assertEqual(task.state, edge.target)
                if edge.effect == "stampDeleted":
                    self.assertIsNotNone(task.deleted_at)
                if edge.effect == "clearDeleted":
                    self.assertIsNone(task.deleted_at)
