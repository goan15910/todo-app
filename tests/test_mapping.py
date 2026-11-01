"""The pinned mapping, without TLC."""

from __future__ import annotations

import hashlib
import inspect
import pathlib
import re
import unittest
from datetime import datetime, timezone

from proof.mapping import ACTION_MAP, abstract, action_names
from proof.registry import Patch, recording
from proof.spec import SPEC, next_actions
from todo_app.adapters.memory import InMemoryTaskRepository
from todo_app.composition import in_memory_app
from todo_app.domain.task import Status, Task


ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = ROOT / "proof" / "MAPPING.sha256"
WAKE = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


class MappingTests(unittest.TestCase):
    def test_mapping_file_matches_its_pin(self) -> None:
        digest = hashlib.sha256((ROOT / "proof" / "mapping.py").read_bytes()).hexdigest()
        self.assertEqual(PIN.read_text(encoding="utf-8").strip(), digest)

    def test_action_map_matches_next(self) -> None:
        text = SPEC.read_text(encoding="utf-8")
        match = re.search(r"(?m)^Next ==\n((?:  \\/ \w+\n)+)", text)
        self.assertIsNotNone(match)
        assert match is not None
        expected = tuple(re.findall(r"\\/ (\w+)", match.group(1)))
        self.assertEqual(set(action_names()), set(expected))
        self.assertEqual(len(action_names()), len(expected))
        self.assertEqual(next_actions(), expected)
        self.assertEqual(len(expected), 14)

    def test_fanout_is_the_three_status_pairs(self) -> None:
        fanout = {
            binding.method: binding.names()
            for binding in ACTION_MAP
            if not isinstance(binding.action, str)
        }
        self.assertEqual(
            fanout,
            {
                "delete": ("DraftDelete", "TodoDelete"),
                "complete": ("TodoComplete", "InProgressComplete"),
                "snooze": ("TodoSnooze", "InProgressSnooze"),
            },
        )

    def test_abstract_collapses_titles_and_times_and_derives_seq(self) -> None:
        repository = InMemoryTaskRepository()
        self.assertEqual(abstract(repository).seq, 1)
        self.assertEqual(abstract(repository).tasks, ())
        repository.add(Task.draft(1, "Buy oat milk"))
        repository.add(Task.draft(2, ""))
        started = repository.get(1)
        assert started is not None
        started.submit()
        started.start()
        repository.save(started)
        view = abstract(repository)
        self.assertEqual(view.seq, 3)
        self.assertEqual(view.tasks[0].title, "task")
        self.assertEqual(view.tasks[0].state, "inProgress")
        self.assertEqual(view.tasks[0].started_at, "set")
        self.assertEqual(view.tasks[0].completed_at, "unset")
        self.assertEqual(view.tasks[1].title, "")
        self.assertEqual(view.tasks[1].state, "draft")
        self.assertNotIn("submitted", view.tasks[0].__dict__)

    def test_keyword_id_selects_the_fanout_action(self) -> None:
        service = in_memory_app()
        service.add_task("milk")
        with recording() as recorder:
            service.delete(task_id=1)
            service.add_task("")
        trace = recorder.to_trace("fanout", "unit")
        self.assertEqual(trace.steps[0].action, "DraftDelete")
        self.assertEqual(trace.steps[0].task_id, 1)
        self.assertEqual(trace.steps[1].action, "Add")
        self.assertEqual(trace.steps[1].title, "")
        self.assertEqual(trace.steps[1].post.seq, 3)

        todo = in_memory_app()
        todo.add_task("milk")
        todo.submit(1)
        with recording() as recorder:
            todo.delete(task_id=1)
        self.assertEqual(recorder.steps[0].action, "TodoDelete")

    def test_signature_bind_rejects_an_unknown_parameter(self) -> None:
        service = in_memory_app()
        service.add_task("milk")
        with self.assertRaises(TypeError):
            service.submit(1, unexpected=True)  # type: ignore[call-arg]
        signature = inspect.signature(type(service).submit)
        bound = signature.bind(service, 1)
        self.assertEqual(bound.arguments["task_id"], 1)

    def test_dropped_mapping_records_an_unmapped_step(self) -> None:
        service = in_memory_app()
        with recording(Patch(drop=frozenset({"submit"}))) as recorder:
            service.add_task("milk")
            service.submit(1)
        self.assertEqual([step.action for step in recorder.steps], ["Add", "UNMAPPED"])
        self.assertEqual(recorder.steps[1].method, "submit")

    def test_snooze_from_todo_and_in_progress_are_different_actions(self) -> None:
        service = in_memory_app()
        service.add_task("milk")
        service.submit(1)
        with recording() as recorder:
            service.snooze(1, WAKE)
        self.assertEqual(recorder.steps[0].action, "TodoSnooze")
        self.assertEqual(recorder.steps[0].post.tasks[0].wake_at, "set")
        other = in_memory_app()
        other.add_task("milk")
        other.submit(1)
        other.start(1)
        with recording() as recorder:
            other.snooze(1, WAKE)
            other.resume(1)
            other.complete(1)
        self.assertEqual(
            [step.action for step in recorder.steps],
            ["InProgressSnooze", "SnoozedResume", "InProgressComplete"],
        )
        self.assertEqual(other.get_task(1).status, Status.DONE)
