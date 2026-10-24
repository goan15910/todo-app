"""Operator mutants of the b-full task, judged by TLC.

One fault per mutant. Spec faults are edits of ``spec/TodoTask.tla``,
checked with ``spec/TodoTask.cfg``. Python faults are projected onto spec
states and checked against the faithful ``Next``. Model-checking that
faithful spec does not see a Python-only edit.

``killed`` means TLC reported an invariant violation or a deadlock, or the
trace search depth was not the number of states. A name error that never
becomes a spec state is ``stillborn``, not a kill. The expected verdict is
the recorded score: a drift fails this command.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from event_alignment import MappedTrace, SpecState, SpecStep, trace_config, trace_module
from todo_app.alignment import ACTIONS, TaskHistory, advance_task, project_task, todo_alignment
from todo_app.task import EDGES, Edge, TodoTask, TransitionRejected

ROOT = os.path.dirname(os.path.abspath(__file__))
_VERDICTS = ("killed", "survived", "stillborn")


@dataclass(frozen=True, slots=True)
class SpecMutant:
    name: str
    operator: str
    expect: str
    edits: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class TraceMutant:
    name: str
    operator: str
    expect: str
    calls: tuple[str, ...]
    make: Callable[[], TodoTask]


@dataclass(frozen=True, slots=True)
class Score:
    name: str
    operator: str
    inlet: str
    expect: str
    actual: str
    detail: str
    output: str


def _edge(state: str, event: str) -> Edge:
    for edge in EDGES:
        if edge.source == state and edge.event == event:
            return edge
    raise TransitionRejected(state, event, "no transition")


class GuardForced(TodoTask):
    """One chart guard always holds. The edge table is unchanged."""

    def __init__(self, title: str, guard: str) -> None:
        super().__init__(title)
        self._forced = guard

    def _guard_holds(self, guard: str) -> bool:
        if guard == self._forced:
            return True
        return super()._guard_holds(guard)


class Retarget(TodoTask):
    """One edge keeps its guard and effect and writes a different target."""

    def __init__(self, title: str, source: str, event: str, target: str) -> None:
        super().__init__(title)
        self._source = source
        self._event = event
        self._target = target

    def _apply(self, event: str, wake_at: datetime | None = None) -> None:
        if self.state == self._source and event == self._event:
            edge = _edge(self.state, event)
            if edge.guard is not None and not self._guard_holds(edge.guard):
                raise TransitionRejected(self.state, event, edge.guard)
            self._run_effect(edge.effect, wake_at)
            self.state = self._target
            return
        super()._apply(event, wake_at)


class Extra(TodoTask):
    """One state-event pair the chart does not list."""

    def __init__(self, title: str, source: str, event: str, target: str, effect: str) -> None:
        super().__init__(title)
        self._source = source
        self._event = event
        self._target = target
        self._effect = effect

    def _apply(self, event: str, wake_at: datetime | None = None) -> None:
        if self.state == self._source and event == self._event:
            self._run_effect(self._effect, wake_at)
            self.state = self._target
            return
        super()._apply(event, wake_at)


def _submit_guard() -> TodoTask:
    return GuardForced("", "titleNotEmpty")


def _archive_guard() -> TodoTask:
    return GuardForced("task", "completedAtSet")


def _complete_stays() -> TodoTask:
    return Retarget("task", "todo", "COMPLETE", "todo")


def _archive_early() -> TodoTask:
    return Extra("task", "inProgress", "ARCHIVE", "archived", "clearListed")


# Anchors are unique in spec/TodoTask.tla. Each mutant starts from that file.
_SUBMIT = r"""DraftSubmit ==
  /\ task.state = "draft"
  /\ task.title /= ""
  /\ task' = [task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE]
"""

_SUBMIT_OPEN = r"""DraftSubmit ==
  /\ task.state = "draft"
  /\ task' = [task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE]
"""

_SUBMIT_EMPTY = r"""DraftSubmit ==
  /\ task.state = "draft"
  /\ task.title = ""
  /\ task' = [task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE]
"""

_SUBMIT_NAMED = r"""DraftSubmit ==
  /\ task.state = "draft"
  /\ task.titel /= ""
  /\ task' = [task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE]
"""

_TODO_COMPLETE = r"""TodoComplete ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "done", !.completed_at = "set"]
"""

_TODO_COMPLETE_BARE = r"""TodoComplete ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "done"]
"""

_TODO_COMPLETE_STAYS = r"""TodoComplete ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "todo", !.completed_at = "set"]
"""

_PROGRESS_COMPLETE = r"""InProgressComplete ==
  /\ task.state = "inProgress"
  /\ task' = [task EXCEPT !.state = "done", !.completed_at = "set"]
"""

_PROGRESS_COMPLETE_BARE = r"""InProgressComplete ==
  /\ task.state = "inProgress"
  /\ task' = [task EXCEPT !.state = "done"]
"""

_ARCHIVE = r"""DoneArchive ==
  /\ task.state = "done"
  /\ task.completed_at /= "unset"
  /\ task' = [task EXCEPT !.state = "archived", !.listed = FALSE]
"""

_ARCHIVE_OPEN = r"""DoneArchive ==
  /\ task.state = "done"
  /\ task' = [task EXCEPT !.state = "archived", !.listed = FALSE]
"""

_SNOOZE_EDGE = r"""  \/ TodoComplete
  \/ TodoSnooze
  \/ TodoDelete
"""

_SNOOZE_GONE = r"""  \/ TodoComplete
  \/ TodoDelete
"""

_DRAFT_EXITS = r"""Next ==
  \/ DraftSubmit
  \/ DraftDelete
  \/ TodoStart
"""

_DRAFT_STUCK = r"""Next ==
  \/ TodoStart
"""

_BEFORE_NEXT = r"""DeletedRestore ==
  /\ task.state = "deleted"
  /\ task' = [task EXCEPT !.state = "todo", !.deleted_at = "unset"]

Next ==
"""

_EXTRA_ACTION = r"""InProgressArchive ==
  /\ task.state = "inProgress"
  /\ task' = [task EXCEPT !.state = "archived", !.listed = FALSE]

DeletedRestore ==
  /\ task.state = "deleted"
  /\ task' = [task EXCEPT !.state = "todo", !.deleted_at = "unset"]

Next ==
"""

_LAST_DISJUNCT = r"""  \/ ArchivedUnarchive
  \/ DeletedRestore
"""

_WITH_EXTRA = r"""  \/ ArchivedUnarchive
  \/ InProgressArchive
  \/ DeletedRestore
"""

_LISTED = '[task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE]'
_UNLISTED = '[task EXCEPT !.state = "todo", !.listed = FALSE, !.submitted = TRUE]'
_NOWHERE = '[task EXCEPT !.state = "nowhere", !.listed = TRUE, !.submitted = TRUE]'

SPEC_MUTANTS: tuple[SpecMutant, ...] = (
    SpecMutant("sdl-submit-guard", "SDL", "killed", ((_SUBMIT, _SUBMIT_OPEN),)),
    SpecMutant("ror-submit-guard", "ROR", "killed", ((_SUBMIT, _SUBMIT_EMPTY),)),
    SpecMutant("sdl-archive-guard", "SDL", "survived", ((_ARCHIVE, _ARCHIVE_OPEN),)),
    SpecMutant("sdl-complete-stamp", "SDL", "survived", ((_TODO_COMPLETE, _TODO_COMPLETE_BARE),)),
    # Neither stamp deletion nor the archive guard alone falsifies CompletedAtSet.
    SpecMutant(
        "hom-unstamped-archive",
        "HOM",
        "killed",
        (
            (_TODO_COMPLETE, _TODO_COMPLETE_BARE),
            (_PROGRESS_COMPLETE, _PROGRESS_COMPLETE_BARE),
            (_ARCHIVE, _ARCHIVE_OPEN),
        ),
    ),
    SpecMutant("crp-submit-state", "CRP", "killed", ((_LISTED, _NOWHERE),)),
    SpecMutant("crp-listed-false", "CRP", "survived", ((_LISTED, _UNLISTED),)),
    SpecMutant("sdl-snooze-edge", "SDL", "survived", ((_SNOOZE_EDGE, _SNOOZE_GONE),)),
    # Deleting one draft edge leaves the other, so both disjuncts go.
    SpecMutant("hom-draft-exits", "HOM", "killed", ((_DRAFT_EXITS, _DRAFT_STUCK),)),
    SpecMutant("crp-complete-target", "CRP", "survived", ((_TODO_COMPLETE, _TODO_COMPLETE_STAYS),)),
    SpecMutant(
        "etr-archive-in-progress",
        "ETR",
        "killed",
        ((_BEFORE_NEXT, _EXTRA_ACTION), (_LAST_DISJUNCT, _WITH_EXTRA)),
    ),
    # Missing field: TLC never builds a successor state. Not a kill.
    SpecMutant("nam-submit-title", "NAM", "stillborn", ((_SUBMIT, _SUBMIT_NAMED),)),
)

TRACE_MUTANTS: tuple[TraceMutant, ...] = (
    TraceMutant("trace-submit-guard", "SDL", "killed", ("submit",), _submit_guard),
    TraceMutant(
        "trace-archive-guard",
        "SDL",
        "survived",
        ("submit", "complete", "archive"),
        _archive_guard,
    ),
    TraceMutant(
        "trace-complete-stays",
        "CRP",
        "killed",
        ("submit", "complete"),
        _complete_stays,
    ),
    TraceMutant(
        "trace-archive-in-progress",
        "ETR",
        "killed",
        ("submit", "start", "archive"),
        _archive_early,
    ),
)


def _apply(source: str, edits: tuple[tuple[str, str], ...]) -> str:
    rendered = source
    for old, new in edits:
        found = rendered.count(old)
        if found != 1:
            raise RuntimeError(f"anchor found {found} times:\n{old}")
        rendered = rendered.replace(old, new, 1)
    if rendered == source:
        raise RuntimeError("edit did not change the spec")
    return rendered


def _invariants(cfg: str) -> tuple[str, ...]:
    names = tuple(re.findall(r"(?m)^INVARIANT\s+(\w+)\s*$", cfg))
    if not names:
        raise RuntimeError("cfg names no invariant")
    return names


def _run(jar: str, work: str, module_name: str, config: str) -> tuple[int, str]:
    module_path = os.path.join(work, module_name)
    config_path = os.path.join(work, os.path.splitext(module_name)[0] + ".cfg")
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(config)
    proc = subprocess.run(
        [
            "java",
            "-XX:+UseParallelGC",
            "-jar",
            os.path.abspath(jar),
            "-workers",
            "1",
            "-metadir",
            os.path.join(work, "meta"),
            "-config",
            config_path,
            module_path,
        ],
        cwd=work,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _states_found(output: str) -> str:
    found = re.findall(r"(\d+) distinct states found", output)
    if not found:
        return "no state count"
    return f"{found[-1]} distinct"


def _classify(output: str, code: int, depth: int | None) -> tuple[str, str]:
    invariant = re.search(r"^Error: Invariant (\w+) is violated\.", output, re.M)
    if invariant:
        return "killed", f"invariant {invariant.group(1)}"
    if re.search(r"^Error: Deadlock reached\.?\s*$", output, re.M):
        return "killed", "deadlock"
    if depth is not None:
        reported = re.search(r"The depth of the complete state graph search is (\d+)\.", output)
        clean = code == 0 and "No error has been found" in output
        if reported and int(reported.group(1)) != depth:
            return "killed", f"depth {reported.group(1)} != {depth}"
        if clean and reported and int(reported.group(1)) == depth:
            return "survived", f"depth {depth}"
        if clean:
            return "unjudged", "no depth line"
    if code == 0 and "No error has been found" in output:
        return "survived", _states_found(output)
    missing = re.search(r'Attempted to select nonexistent field "([^"]+)"', output)
    if missing:
        return "stillborn", f'no field "{missing.group(1)}"'
    error = next((line.strip() for line in output.splitlines() if line.startswith("Error:")), "")
    if error or code != 0:
        return "stillborn", (error or f"exit {code}")[:160]
    return "unjudged", f"exit {code}"


def _project(task: TodoTask, calls: tuple[str, ...]) -> MappedTrace:
    history: object = TaskHistory()
    current = SpecState(project_task(task, history))
    steps: list[SpecStep] = []
    variable = todo_alignment.variable
    for method in calls:
        source = task.state
        getattr(task, method)()
        event = todo_alignment.steps[f"TodoTask.{method}"]
        try:
            action = ACTIONS[(source, event)]
        except KeyError:
            # No chart action. The states are still the mutant's; Next decides.
            action = "Unspecified"
        history = advance_task(history, action)
        post = SpecState(project_task(task, history))
        steps.append(
            SpecStep(
                action=action,
                pre=current,
                post=post,
                thread_id=1,
                qualname=f"TodoTask.{method}",
            )
        )
        current = post
    init = steps[0].pre if steps else current
    return MappedTrace(variable, init, tuple(steps))


def _score_spec(
    jar: str, name: str, operator: str, expect: str, module: str, cfg: str
) -> Score:
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "TodoTask.tla")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(module)
        code, output = _run(jar, work, "TodoTask.tla", cfg)
    actual, detail = _classify(output, code, None)
    return Score(name, operator, "cfg", expect, actual, detail, output)


def _score_trace(jar: str, mutant: TraceMutant, cfg_invariants: tuple[str, ...]) -> Score:
    mapped = _project(mutant.make(), mutant.calls)
    module = trace_module(mapped)
    config = trace_config(cfg_invariants)
    with tempfile.TemporaryDirectory() as work:
        spec_from = os.path.join(ROOT, "spec", "TodoTask.tla")
        spec_to = os.path.join(work, "TodoTask.tla")
        with open(spec_from, encoding="utf-8") as handle:
            spec = handle.read()
        with open(spec_to, "w", encoding="utf-8") as handle:
            handle.write(spec)
        path = os.path.join(work, "MappedTrace.tla")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(module)
        code, output = _run(jar, work, "MappedTrace.tla", config)
    actual, detail = _classify(output, code, len(mapped.states()))
    return Score(mutant.name, mutant.operator, "trace", mutant.expect, actual, detail, output)


def _line(row: Score) -> str:
    mark = "ok" if row.actual == row.expect else "MISMATCH"
    return (
        f"{row.name:<28} {row.operator:<4} {row.inlet:<5} "
        f"{row.actual:<9} {row.detail:<28} {mark}"
    )


def main() -> int:
    jar = os.environ.get("TLC_JAR", "")
    if not jar or not os.path.isfile(jar):
        print("TLC_JAR must be the path to tla2tools.jar", file=sys.stderr)
        return 2
    spec_path = os.path.join(ROOT, "spec", "TodoTask.tla")
    cfg_path = os.path.join(ROOT, "spec", "TodoTask.cfg")
    with open(spec_path, encoding="utf-8") as handle:
        spec = handle.read()
    with open(cfg_path, encoding="utf-8") as handle:
        cfg = handle.read()
    try:
        rendered = [(item, _apply(spec, item.edits)) for item in SPEC_MUTANTS]
        invariants = _invariants(cfg)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2

    rows = [_score_spec(jar, "faithful", "-", "survived", spec, cfg)]
    rows.extend(_score_spec(jar, item.name, item.operator, item.expect, module, cfg) for item, module in rendered)
    try:
        rows.extend(_score_trace(jar, item, invariants) for item in TRACE_MUTANTS)
    except (TransitionRejected, TypeError, KeyError, RuntimeError) as exc:
        print(f"trace mutant did not run: {exc}", file=sys.stderr)
        return 2

    log_dir = os.environ.get("MUTATION_LOG_DIR")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    version = next((line for line in rows[0].output.splitlines() if line.startswith("TLC2 ")), "TLC")
    print(version)
    print(f"{'mutant':<28} {'op':<4} {'inlet':<5} {'verdict':<9} {'detail':<28} check")
    mismatches: list[Score] = []
    for row in rows:
        print(_line(row))
        if row.actual != row.expect or row.actual not in _VERDICTS:
            mismatches.append(row)
        if log_dir:
            with open(os.path.join(log_dir, f"{row.name}.txt"), "w", encoding="utf-8") as handle:
                handle.write(row.output)
    counted = [row for row in rows if row.name != "faithful"]
    totals = {name: sum(row.actual == name for row in counted) for name in _VERDICTS}
    print(
        f"killed {totals['killed']}, survived {totals['survived']}, stillborn {totals['stillborn']}"
    )
    if mismatches:
        for row in mismatches:
            print(f"\n--- {row.name} expected {row.expect}, TLC said {row.actual} ---", file=sys.stderr)
            tail = "\n".join(row.output.splitlines()[-40:])
            print(tail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
