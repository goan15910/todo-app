"""Captured todo-flow events align onto TodoTask spec states and actions."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

import todo_app.task as tasks
from event_alignment import SpecState, SpecStep, trace, trace_config, trace_module
from todo_app.alignment import ACTIONS, todo_alignment
from todo_app.task import EDGES, TodoTask, TransitionRejected

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVARIANTS = ("TypeOK", "TitleNotEmpty", "CompletedAtSet")

DRAFT = {
    "state": "draft",
    "title": "task",
    "listed": False,
    "started_at": "unset",
    "completed_at": "unset",
    "wake_at": "unset",
    "deleted_at": "unset",
    "submitted": False,
}


def _coverage_count(output: str, name: str) -> int:
    match = re.search(rf"<{name} line [^>]*>: (\d+):\d+", output)
    if match is None:
        raise AssertionError(f"{name} missing from TLC coverage\n{output[-2000:]}")
    return int(match.group(1))


def _assert_actions_covered(case: unittest.TestCase, output: str, actions: tuple[str, ...]) -> None:
    for name in set(ACTIONS.values()):
        case.assertEqual(_coverage_count(output, name), actions.count(name))


def _fields(state: SpecState) -> dict[str, object]:
    return dict(state.fields)


def _with_title(title: str, **updates: object) -> dict[str, object]:
    fields = dict(DRAFT)
    fields["title"] = title
    fields.update(updates)
    return fields


class TLCResult:
    def __init__(self, code: int, output: str) -> None:
        self.code = code
        self.output = output


def _run_tlc(module: str, config: str) -> TLCResult:
    jar = os.environ.get("TLC_JAR")
    if not jar:
        raise AssertionError("TLC_JAR is not set")
    jar = os.path.abspath(jar)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(os.path.join(ROOT, "spec", "TodoTask.tla"), os.path.join(tmp, "TodoTask.tla"))
        with open(os.path.join(tmp, "MappedTrace.tla"), "w", encoding="utf-8") as handle:
            handle.write(module)
        cfg = os.path.join(tmp, "MappedTrace.cfg")
        with open(cfg, "w", encoding="utf-8") as handle:
            handle.write(config)
        meta = os.path.join(tmp, "meta")
        proc = subprocess.run(
            [
                "java",
                "-XX:+UseParallelGC",
                "-jar",
                jar,
                "-workers",
                "1",
                "-coverage",
                "1",
                "-metadir",
                meta,
                "-config",
                cfg,
                "MappedTrace.tla",
            ],
            cwd=tmp,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    return TLCResult(proc.returncode, proc.stdout + proc.stderr)


class AlignmentTests(unittest.TestCase):
    def test_declaration_covers_the_chart_and_the_spec(self) -> None:
        self.assertEqual(
            set(todo_alignment.steps.values()),
            {edge.event for edge in EDGES},
        )
        self.assertEqual(
            set(todo_alignment.actions),
            {(edge.source, edge.event) for edge in EDGES},
        )
        self.assertNotIn("TodoTask.set_title", todo_alignment.steps)
        with open(os.path.join(ROOT, "spec", "TodoTask.tla"), encoding="utf-8") as handle:
            spec = handle.read()
        for name in set(ACTIONS.values()):
            self.assertIn(f"\n{name} ==", spec)

    def test_initial_flow_maps_onto_spec_states(self) -> None:
        when = datetime(2026, 1, 2, tzinfo=timezone.utc)
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("")
            run.bind(item)
            item.set_title("buy milk")
            item.submit()
            item.start()
            item.snooze(when)
            item.resume()
            item.complete()
            item.archive()
        mapped = run.mapped()

        self.assertGreater(len(run.events), len(mapped.steps))
        self.assertTrue(
            any(event.kind == "PY_RETURN" and event.qualname == "TodoTask.set_title" for event in run.events)
        )
        self.assertNotIn("TodoTask.set_title", [step.qualname for step in mapped.steps])
        self.assertTrue(any(event.arg0 and "buy milk" in event.arg0 for event in run.events))
        c_returns = [event for event in run.events if event.kind == "C_RETURN"]
        self.assertTrue(c_returns)
        self.assertTrue(all(event.value is None for event in c_returns))
        self.assertEqual({event.thread_id for event in run.events}, {mapped.steps[0].thread_id})

        expected = [
            ("DraftSubmit", _with_title("task", state="todo", listed=True, submitted=True)),
            (
                "TodoStart",
                _with_title(
                    "task", state="inProgress", listed=True, started_at="set", submitted=True
                ),
            ),
            (
                "InProgressSnooze",
                _with_title(
                    "task",
                    state="snoozed",
                    listed=True,
                    started_at="set",
                    wake_at="set",
                    submitted=True,
                ),
            ),
            (
                "SnoozedResume",
                _with_title(
                    "task", state="inProgress", listed=True, started_at="set", submitted=True
                ),
            ),
            (
                "InProgressComplete",
                _with_title(
                    "task",
                    state="done",
                    listed=True,
                    started_at="set",
                    completed_at="set",
                    submitted=True,
                ),
            ),
            (
                "DoneArchive",
                _with_title(
                    "task",
                    state="archived",
                    listed=False,
                    started_at="set",
                    completed_at="set",
                    submitted=True,
                ),
            ),
        ]
        self.assertEqual(_fields(mapped.init), _with_title("task"))
        self.assertEqual(
            [(step.action, _fields(step.post)) for step in mapped.steps],
            expected,
        )
        self.assertTrue(mapped.states()[-1]["submitted"])
        self.assertFalse(mapped.states()[-1]["listed"])
        names = [sys.monitoring.get_tool(tool_id) for tool_id in range(6)]
        self.assertNotIn("event_alignment", names)
        self.assertNotIn("execution_events", names)

        result = _run_tlc(trace_module(mapped), trace_config(INVARIANTS))
        self.assertEqual(result.code, 0, result.output[-2000:])
        self.assertIn("No error has been found.", result.output)
        self.assertIn(
            f"The depth of the complete state graph search is {len(mapped.states())}.",
            result.output,
        )
        _assert_actions_covered(self, result.output, mapped.actions())

    def test_tlc_rejects_a_post_state_the_action_does_not_produce(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("buy milk")
            run.bind(item)
            item.submit()
            item.start()
            item.complete()
            item.archive()
        mapped = run.mapped()
        last = mapped.steps[-1]
        fields = _fields(last.post)
        fields["listed"] = True
        bad_step = SpecStep(last.action, last.pre, SpecState(fields), last.thread_id, last.qualname)
        bad_steps = mapped.steps[:-1] + (bad_step,)
        bad = type(mapped)(mapped.variable, mapped.init, bad_steps)
        result = _run_tlc(trace_module(bad), trace_config(INVARIANTS))
        match = re.search(r"The depth of the complete state graph search is (\d+).", result.output)
        self.assertIsNotNone(match, result.output[-1500:])
        self.assertLess(int(match.group(1)), len(bad.states()))

    def test_restore_without_submit_stays_unsubmitted(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("")
            run.bind(item)
            item.delete()
            item.restore()
        mapped = run.mapped()
        empty = _with_title("")
        self.assertEqual(_fields(mapped.init), empty)
        self.assertEqual(
            [(step.action, _fields(step.post)) for step in mapped.steps],
            [
                ("DraftDelete", _with_title("", state="deleted", deleted_at="set")),
                ("DeletedRestore", _with_title("", state="todo")),
            ],
        )
        self.assertIsNone(item.deleted_at)
        self.assertFalse(mapped.states()[-1]["submitted"])
        result = _run_tlc(trace_module(mapped), trace_config(INVARIANTS))
        self.assertEqual(result.code, 0, result.output[-2000:])
        _assert_actions_covered(self, result.output, mapped.actions())

    def test_rejected_submit_is_not_a_spec_step(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("")
            run.bind(item)
            with self.assertRaises(TransitionRejected):
                item.submit()
        mapped = run.mapped()
        self.assertEqual(mapped.steps, ())
        self.assertEqual(_fields(mapped.init), _with_title(""))
        self.assertEqual(item.state, "draft")
        self.assertTrue(
            any(event.kind == "PY_UNWIND" and event.qualname == "TodoTask.submit" for event in run.events)
        )
        self.assertFalse(
            any(event.kind == "PY_RETURN" and event.qualname == "TodoTask.submit" for event in run.events)
        )


if __name__ == "__main__":
    unittest.main()
