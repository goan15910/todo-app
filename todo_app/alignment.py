"""Alignment of one ``TodoTask`` onto ``spec/TodoTask.tla``.

Edit ``_METHODS``, ``ACTIONS``, ``project_task``, ``advance_task``, and
``effect_task`` to change the correspondence. ``effect_task`` is how a
JSON Lines log is replayed. ``task_traces`` reads the log from
``python -m execution_events``: one trace per constructed task. The chart
call's ``arg0`` repr supplies ``state``, ``title``, and ``listed``;
timestamps and ``submitted`` are not in that repr. ``set_title`` is
not a step: the spec chooses
the draft title in ``Init``. A non-empty title is the spec value ``"task"``.
Timestamps are ``"unset"`` or ``"set"``. ``submitted`` is not a field of
the task; ``advance_task`` remembers it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from event_alignment import Alignment, AlignmentError, LogEvent, MappedTrace, SpecState, map_log
from todo_app.task import TodoTask

_METHODS = {
    "submit": "SUBMIT",
    "delete": "DELETE",
    "start": "START",
    "complete": "COMPLETE",
    "snooze": "SNOOZE",
    "resume": "RESUME",
    "reopen": "REOPEN",
    "archive": "ARCHIVE",
    "unarchive": "UNARCHIVE",
    "restore": "RESTORE",
}

ACTIONS = {
    ("draft", "SUBMIT"): "DraftSubmit",
    ("draft", "DELETE"): "DraftDelete",
    ("todo", "START"): "TodoStart",
    ("todo", "COMPLETE"): "TodoComplete",
    ("todo", "SNOOZE"): "TodoSnooze",
    ("todo", "DELETE"): "TodoDelete",
    ("inProgress", "SNOOZE"): "InProgressSnooze",
    ("inProgress", "COMPLETE"): "InProgressComplete",
    ("snoozed", "RESUME"): "SnoozedResume",
    ("done", "REOPEN"): "DoneReopen",
    ("done", "ARCHIVE"): "DoneArchive",
    ("archived", "UNARCHIVE"): "ArchivedUnarchive",
    ("deleted", "RESTORE"): "DeletedRestore",
}


@dataclass(frozen=True, slots=True)
class TaskHistory:
    submitted: bool = False


def project_task(task: TodoTask, history: object) -> dict[str, object]:
    if not isinstance(history, TaskHistory):
        raise TypeError("history must be TaskHistory")

    def mark(value: object) -> str:
        return "unset" if value is None else "set"

    title = task.title
    return {
        "state": task.state,
        "title": "" if title == "" else "task",
        "listed": task.listed,
        "started_at": mark(task.started_at),
        "completed_at": mark(task.completed_at),
        "wake_at": mark(task.wake_at),
        "deleted_at": mark(task.deleted_at),
        "submitted": history.submitted,
    }


def effect_task(fields: Mapping[str, object], action: str) -> dict[str, object]:
    """Successor record for ``action``. Matches the assignments in the spec."""

    updated = dict(fields)
    if action == "DraftSubmit":
        updated["state"] = "todo"
        updated["listed"] = True
        updated["submitted"] = True
    elif action == "DraftDelete":
        updated["state"] = "deleted"
        updated["deleted_at"] = "set"
    elif action == "TodoStart":
        updated["state"] = "inProgress"
        updated["started_at"] = "set"
    elif action == "TodoComplete":
        updated["state"] = "done"
        updated["completed_at"] = "set"
    elif action == "TodoSnooze":
        updated["state"] = "snoozed"
        updated["wake_at"] = "set"
    elif action == "TodoDelete":
        updated["state"] = "deleted"
        updated["deleted_at"] = "set"
    elif action == "InProgressSnooze":
        updated["state"] = "snoozed"
        updated["wake_at"] = "set"
    elif action == "InProgressComplete":
        updated["state"] = "done"
        updated["completed_at"] = "set"
    elif action == "SnoozedResume":
        updated["state"] = "inProgress"
        updated["wake_at"] = "unset"
    elif action == "DoneReopen":
        updated["state"] = "todo"
        updated["completed_at"] = "unset"
    elif action == "DoneArchive":
        updated["state"] = "archived"
        updated["listed"] = False
    elif action == "ArchivedUnarchive":
        updated["state"] = "done"
        updated["listed"] = True
    elif action == "DeletedRestore":
        updated["state"] = "todo"
        updated["deleted_at"] = "unset"
    else:
        raise AlignmentError(f"no effect for {action}")
    return updated


def advance_task(history: object, action: str) -> TaskHistory:
    if not isinstance(history, TaskHistory):
        raise TypeError("history must be TaskHistory")
    if action == "DraftSubmit":
        return TaskHistory(submitted=True)
    return history


todo_alignment = Alignment(
    variable="task",
    steps={f"TodoTask.{name}": event for name, event in _METHODS.items()},
    actions=ACTIONS,
    project=project_task,
    advance=advance_task,
    initial_history=TaskHistory(),
    effect=effect_task,
)

_INIT = "TodoTask.__init__"
_APPLY_SUFFIX = "TodoTask._apply"
_TASK_REPR = re.compile(
    r"^TodoTask\(state=(?P<state>'(?:\\.|[^'])*'), "
    r"title=(?P<title>'(?:\\.|[^'])*'), "
    r"listed=(?P<listed>True|False)\)$"
)


def task_traces(events: Iterable[LogEvent]) -> tuple[MappedTrace, ...]:
    """Spec traces for each ``TodoTask`` in a recorder-command log.

    A ``PY_START`` of ``TodoTask.__init__`` opens a task. A successful
    return of a declared method is one step. An unwind is not. The
    ``_apply`` call's ``arg0`` is the pre-state repr; it has to agree with
    the record ``effect_task`` produced for that step.
    """

    segments = _task_segments(tuple(events))
    traces: list[MappedTrace] = []
    for segment in segments:
        attempts = _chart_attempts(segment)
        initial = _draft_from_repr(attempts[0][2])
        mapped = map_log(segment, todo_alignment, initial)
        _require_reprs(attempts, mapped)
        traces.append(mapped)
    return tuple(traces)


def _task_segments(events: tuple[LogEvent, ...]) -> tuple[tuple[LogEvent, ...], ...]:
    segments: list[list[LogEvent]] = []
    current: list[LogEvent] | None = None
    for event in events:
        if event.qualname == _INIT and event.kind == "PY_START":
            if current:
                segments.append(current)
            current = [event]
        elif current is not None:
            current.append(event)
    if current:
        segments.append(current)
    if not segments:
        raise AlignmentError("log constructs no TodoTask")
    return tuple(tuple(segment) for segment in segments)


def _chart_attempts(segment: tuple[LogEvent, ...]) -> tuple[tuple[str, str, str], ...]:
    """``(qualname, "return"|"unwind", repr)`` for each chart call."""

    steps = todo_alignment.steps
    stack: list[list[object]] = []
    found: list[tuple[str, str, str]] = []
    for event in segment:
        if event.qualname not in steps:
            continue
        if event.kind == "PY_START":
            stack.append([event.qualname, None])
            continue
        if event.kind == "CALL" and (event.callable or "").endswith(_APPLY_SUFFIX):
            if not stack or stack[-1][0] != event.qualname:
                raise AlignmentError(f"{event.qualname} called _apply outside its chart call")
            if stack[-1][1] is not None:
                raise AlignmentError(f"{event.qualname} called _apply twice")
            if event.arg0 is None:
                raise AlignmentError(f"{event.qualname} chart call has no arg0")
            stack[-1][1] = event.arg0
            continue
        if event.kind not in {"PY_RETURN", "PY_UNWIND"}:
            continue
        if not stack or stack[-1][0] != event.qualname:
            raise AlignmentError(f"{event.qualname} {event.kind} without a start")
        _name, arg0 = stack.pop()
        if not isinstance(arg0, str):
            raise AlignmentError(f"{event.qualname} had no TodoTask repr")
        outcome = "return" if event.kind == "PY_RETURN" else "unwind"
        found.append((event.qualname, outcome, arg0))
    if stack:
        raise AlignmentError("chart call did not return")
    if not found:
        raise AlignmentError("constructed task has no chart call")
    return tuple(found)


def _draft_from_repr(arg0: str) -> SpecState:
    state, title, listed = _parse_task_repr(arg0)
    if state != "draft" or listed:
        raise AlignmentError("task does not start as an unlisted draft")
    return SpecState(
        {
            "state": "draft",
            "title": "" if title == "" else "task",
            "listed": False,
            "started_at": "unset",
            "completed_at": "unset",
            "wake_at": "unset",
            "deleted_at": "unset",
            "submitted": False,
        }
    )


def _require_reprs(
    attempts: tuple[tuple[str, str, str], ...], mapped: MappedTrace
) -> None:
    state = mapped.init
    index = 0
    for qualname, outcome, arg0 in attempts:
        if not _repr_agrees(state, arg0):
            raise AlignmentError(f"{qualname} pre-state does not match the log repr")
        if outcome == "unwind":
            continue
        if index >= len(mapped.steps):
            raise AlignmentError("log has a chart return that was not replayed")
        step = mapped.steps[index]
        if step.qualname != qualname or step.pre != state:
            raise AlignmentError("replayed step does not match the log")
        state = step.post
        index += 1
    if index != len(mapped.steps):
        raise AlignmentError("replay produced a step the log did not return")


def _repr_agrees(state: SpecState, arg0: str) -> bool:
    observed_state, title, listed = _parse_task_repr(arg0)
    spec_title = "" if title == "" else "task"
    return (
        state["state"] == observed_state
        and state["listed"] == listed
        and state["title"] == spec_title
    )


def _parse_task_repr(arg0: str) -> tuple[str, str, bool]:
    match = _TASK_REPR.match(arg0)
    if match is None:
        raise AlignmentError("chart call arg0 is not a TodoTask repr")
    py_state = ast.literal_eval(match.group("state"))
    title = ast.literal_eval(match.group("title"))
    if not isinstance(py_state, str) or not isinstance(title, str):
        raise AlignmentError("chart call arg0 is not a TodoTask repr")
    return py_state, title, match.group("listed") == "True"
