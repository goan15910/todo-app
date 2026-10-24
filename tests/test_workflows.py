"""Clew's named todo workflows, traced onto spec/TodoTask.tla.

W1–W7 must be legal ``Next`` behaviors. The post-state is read off the
live task, not copied from the spec. W8, every other missing state/event
pair, and both chart guards must be refused and must not be a spec step.
Snooze-from-todo, unarchive, and restoring a deleted empty draft are spec
behaviors the eight workflows do not name.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import todo_app.task as tasks
from event_alignment import MappedTrace, trace, trace_config, trace_module
from test_alignment import INVARIANTS, _assert_actions_covered, _run_tlc
from todo_app.alignment import ACTIONS, todo_alignment
from todo_app.task import EDGES, Edge, TodoTask, TransitionRejected

WAKE = datetime(2026, 10, 25, tzinfo=timezone.utc)

# examples/todo/workflows.yaml in clew @ 80719108. states[0] is the start
# node; states[i + 1] is the target of events[i]. W5 is both spec titles.
SPECIFIED = (
    (
        "W1",
        "task",
        ("SUBMIT", "START", "COMPLETE"),
        ("draft", "todo", "inProgress", "done"),
        ("DraftSubmit", "TodoStart", "InProgressComplete"),
    ),
    (
        "W2",
        "task",
        ("SUBMIT", "START", "SNOOZE", "RESUME", "COMPLETE"),
        ("draft", "todo", "inProgress", "snoozed", "inProgress", "done"),
        ("DraftSubmit", "TodoStart", "InProgressSnooze", "SnoozedResume", "InProgressComplete"),
    ),
    (
        "W3",
        "task",
        ("SUBMIT", "START", "COMPLETE", "REOPEN"),
        ("draft", "todo", "inProgress", "done", "todo"),
        ("DraftSubmit", "TodoStart", "InProgressComplete", "DoneReopen"),
    ),
    (
        "W4",
        "task",
        ("SUBMIT", "START", "COMPLETE", "ARCHIVE"),
        ("draft", "todo", "inProgress", "done", "archived"),
        ("DraftSubmit", "TodoStart", "InProgressComplete", "DoneArchive"),
    ),
    (
        "W5",
        "",
        ("DELETE",),
        ("draft", "deleted"),
        ("DraftDelete",),
    ),
    (
        "W5",
        "task",
        ("DELETE",),
        ("draft", "deleted"),
        ("DraftDelete",),
    ),
    (
        "W6",
        "task",
        ("SUBMIT", "DELETE", "RESTORE"),
        ("draft", "todo", "deleted", "todo"),
        ("DraftSubmit", "TodoDelete", "DeletedRestore"),
    ),
    (
        "W7",
        "task",
        ("SUBMIT", "COMPLETE"),
        ("draft", "todo", "done"),
        ("DraftSubmit", "TodoComplete"),
    ),
    (
        "snooze-from-todo",
        "task",
        ("SUBMIT", "SNOOZE"),
        ("draft", "todo", "snoozed"),
        ("DraftSubmit", "TodoSnooze"),
    ),
    (
        "unarchive",
        "task",
        ("SUBMIT", "START", "COMPLETE", "ARCHIVE", "UNARCHIVE"),
        ("draft", "todo", "inProgress", "done", "archived", "done"),
        (
            "DraftSubmit",
            "TodoStart",
            "InProgressComplete",
            "DoneArchive",
            "ArchivedUnarchive",
        ),
    ),
    (
        "unsubmitted-restore",
        "",
        ("DELETE", "RESTORE"),
        ("draft", "deleted", "todo"),
        ("DraftDelete", "DeletedRestore"),
    ),
)

_EVENTS = (
    "SUBMIT",
    "DELETE",
    "START",
    "COMPLETE",
    "SNOOZE",
    "RESUME",
    "REOPEN",
    "ARCHIVE",
    "UNARCHIVE",
    "RESTORE",
)

_REACH = {
    "draft": (),
    "todo": ("SUBMIT",),
    "inProgress": ("SUBMIT", "START"),
    "snoozed": ("SUBMIT", "START", "SNOOZE"),
    "done": ("SUBMIT", "START", "COMPLETE"),
    "archived": ("SUBMIT", "START", "COMPLETE", "ARCHIVE"),
    "deleted": ("SUBMIT", "DELETE"),
}

_FIELDS = ("state", "title", "listed", "started_at", "completed_at", "wake_at", "deleted_at")

_EFFECT_FIELD = {
    "markListed": "listed",
    "clearListed": "listed",
    "stampStarted": "started_at",
    "stampCompleted": "completed_at",
    "clearCompleted": "completed_at",
    "setWakeAt": "wake_at",
    "clearWakeAt": "wake_at",
    "stampDeleted": "deleted_at",
    "clearDeleted": "deleted_at",
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
        raise AssertionError(event)


def _view(task: TodoTask) -> tuple[object, ...]:
    return tuple(getattr(task, name) for name in _FIELDS)


def _edge(source: str, event: str) -> Edge:
    for edge in EDGES:
        if edge.source == source and edge.event == event:
            return edge
    raise AssertionError(f"no edge {source} {event}")


def _prep_actions(events: tuple[str, ...]) -> tuple[str, ...]:
    source = "draft"
    names: list[str] = []
    for event in events:
        names.append(ACTIONS[(source, event)])
        source = _edge(source, event).target
    return tuple(names)


def _assert_effect(case: unittest.TestCase, task: TodoTask, before: tuple[object, ...], edge: Edge) -> None:
    case.assertEqual(task.state, edge.target)
    field = _EFFECT_FIELD[edge.effect]
    changed = {"state", field}
    after = _view(task)
    for name, old, new in zip(_FIELDS, before, after, strict=True):
        if name in changed:
            continue
        case.assertEqual(new, old, name)
    if edge.effect == "markListed":
        case.assertIs(task.listed, True)
    elif edge.effect == "clearListed":
        case.assertIs(task.listed, False)
    elif edge.effect == "setWakeAt":
        case.assertEqual(task.wake_at, WAKE)
    elif edge.effect in {"clearWakeAt", "clearCompleted", "clearDeleted"}:
        case.assertIsNone(getattr(task, field))
    elif edge.effect in {"stampStarted", "stampCompleted", "stampDeleted"}:
        case.assertIsNone(before[_FIELDS.index(field)])
        case.assertIsInstance(getattr(task, field), datetime)
    else:
        raise AssertionError(edge.effect)


def _check_tlc(case: unittest.TestCase, mapped: MappedTrace) -> None:
    result = _run_tlc(trace_module(mapped), trace_config(INVARIANTS))
    case.assertEqual(result.code, 0, result.output[-2000:])
    case.assertIn("No error has been found.", result.output)
    case.assertIn(
        f"The depth of the complete state graph search is {len(mapped.states())}.",
        result.output,
    )
    _assert_actions_covered(case, result.output, mapped.actions())


class WorkflowTests(unittest.TestCase):
    def _walk(self, title: str, events: tuple[str, ...], states: tuple[str, ...]):
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask(title)
            run.bind(item)
            self.assertEqual(_view(item)[:3], ("draft", title, False))
            self.assertIsNone(item.started_at)
            self.assertIsNone(item.completed_at)
            self.assertIsNone(item.wake_at)
            self.assertIsNone(item.deleted_at)
            for index, event in enumerate(events):
                self.assertEqual(item.state, states[index])
                before = _view(item)
                _apply(item, event)
                _assert_effect(self, item, before, _edge(states[index], event))
                self.assertEqual(item.state, states[index + 1])
        return run.mapped(), run.events

    def test_specified_traces_match_next(self) -> None:
        seen: list[str] = []
        for name, title, events, states, actions in SPECIFIED:
            with self.subTest(workflow=name, title=repr(title)):
                self.assertEqual(len(states), len(events) + 1)
                mapped, _events = self._walk(title, events, states)
                self.assertEqual(mapped.actions(), actions)
                self.assertIs(mapped.init["submitted"], False)
                self.assertEqual(mapped.init["state"], "draft")
                self.assertIs(mapped.init["listed"], False)
                self.assertEqual(mapped.init["title"], "" if title == "" else "task")
                for index, step in enumerate(mapped.steps):
                    self.assertEqual(ACTIONS[(states[index], events[index])], actions[index])
                    self.assertEqual(step.action, actions[index])
                    self.assertEqual(step.pre["state"], states[index])
                    self.assertEqual(step.post["state"], states[index + 1])
                    self.assertEqual(step.post["title"], step.pre["title"])
                    if actions[index] == "DraftSubmit":
                        self.assertIs(step.post["submitted"], True)
                    else:
                        self.assertEqual(step.post["submitted"], step.pre["submitted"])
                _check_tlc(self, mapped)
            seen.extend(actions)
        self.assertEqual(set(seen), set(ACTIONS.values()))

    def test_w8_archive_from_in_progress_is_refused(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("task")
            run.bind(item)
            _apply(item, "SUBMIT")
            _apply(item, "START")
            self.assertEqual(item.state, "inProgress")
            before = _view(item)
            with self.assertRaises(TransitionRejected) as caught:
                _apply(item, "ARCHIVE")
            self.assertEqual(
                (caught.exception.state, caught.exception.event, caught.exception.reason),
                ("inProgress", "ARCHIVE", "no transition"),
            )
            self.assertEqual(_view(item), before)
            self.assertIsNotNone(item.started_at)
            self.assertIsNone(item.completed_at)
        mapped = run.mapped()
        self.assertEqual(mapped.actions(), ("DraftSubmit", "TodoStart"))
        self.assertTrue(
            any(event.kind == "PY_UNWIND" and event.qualname == "TodoTask.archive" for event in run.events)
        )
        self.assertFalse(
            any(event.kind == "PY_RETURN" and event.qualname == "TodoTask.archive" for event in run.events)
        )
        _check_tlc(self, mapped)

    def test_absent_state_event_pairs_are_refused(self) -> None:
        legal = {(edge.source, edge.event) for edge in EDGES}
        reached = {edge.source for edge in EDGES} | {edge.target for edge in EDGES}
        self.assertEqual(set(_REACH), reached)
        self.assertEqual(set(_EVENTS), {edge.event for edge in EDGES})
        absent = [(state, event) for state in _REACH for event in _EVENTS if (state, event) not in legal]
        self.assertEqual(len(absent), 57)
        for state, prep in _REACH.items():
            forbidden = [event for event in _EVENTS if (state, event) not in legal]
            with trace(todo_alignment, files=[tasks.__file__]) as run:
                item = TodoTask("task")
                run.bind(item)
                for event in prep:
                    _apply(item, event)
                self.assertEqual(item.state, state)
                for event in forbidden:
                    with self.subTest(state=state, event=event):
                        before = _view(item)
                        with self.assertRaises(TransitionRejected) as caught:
                            _apply(item, event)
                        self.assertEqual(caught.exception.state, state)
                        self.assertEqual(caught.exception.event, event)
                        self.assertEqual(caught.exception.reason, "no transition")
                        self.assertEqual(_view(item), before)
            mapped = run.mapped()
            self.assertEqual(mapped.actions(), _prep_actions(prep))
            steps = set(todo_alignment.steps)
            unwinds = [event for event in run.events if event.kind == "PY_UNWIND" and event.qualname in steps]
            returns = [event for event in run.events if event.kind == "PY_RETURN" and event.qualname in steps]
            self.assertEqual(len(unwinds), len(forbidden))
            self.assertEqual(len(returns), len(prep))

    def test_chart_guards_refuse_without_a_spec_step(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("")
            run.bind(item)
            before = _view(item)
            with self.assertRaises(TransitionRejected) as caught:
                item.submit()
            self.assertEqual(caught.exception.reason, "titleNotEmpty")
            self.assertEqual(_view(item), before)
        mapped = run.mapped()
        self.assertEqual(mapped.steps, ())
        self.assertEqual(item.state, "draft")
        self.assertTrue(
            any(event.kind == "PY_UNWIND" and event.qualname == "TodoTask.submit" for event in run.events)
        )
        self.assertFalse(
            any(event.kind == "PY_RETURN" and event.qualname == "TodoTask.submit" for event in run.events)
        )

        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("task")
            run.bind(item)
            item.submit()
            item.start()
            item.complete()
            self.assertIsNotNone(item.completed_at)
            item.completed_at = None
            before = _view(item)
            with self.assertRaises(TransitionRejected) as caught:
                item.archive()
            self.assertEqual(caught.exception.reason, "completedAtSet")
            self.assertEqual(caught.exception.state, "done")
            self.assertEqual(_view(item), before)
        mapped = run.mapped()
        self.assertEqual(mapped.actions(), ("DraftSubmit", "TodoStart", "InProgressComplete"))
        self.assertTrue(
            any(event.kind == "PY_UNWIND" and event.qualname == "TodoTask.archive" for event in run.events)
        )
        self.assertFalse(
            any(event.kind == "PY_RETURN" and event.qualname == "TodoTask.archive" for event in run.events)
        )

    def test_set_title_is_not_a_spec_step(self) -> None:
        with trace(todo_alignment, files=[tasks.__file__]) as run:
            item = TodoTask("")
            run.bind(item)
            item.set_title("buy milk")
            item.submit()
            with self.assertRaises(TransitionRejected) as caught:
                item.set_title("other")
            self.assertEqual(caught.exception.reason, "title is fixed after the draft")
            self.assertEqual(item.title, "buy milk")
            self.assertEqual(item.state, "todo")
        mapped = run.mapped()
        self.assertEqual(mapped.actions(), ("DraftSubmit",))
        self.assertEqual(mapped.init["title"], "task")
        self.assertEqual([step.qualname for step in mapped.steps], ["TodoTask.submit"])
        self.assertTrue(
            any(event.kind == "PY_RETURN" and event.qualname == "TodoTask.set_title" for event in run.events)
        )
        self.assertTrue(
            any(event.kind == "PY_UNWIND" and event.qualname == "TodoTask.set_title" for event in run.events)
        )


if __name__ == "__main__":
    unittest.main()
