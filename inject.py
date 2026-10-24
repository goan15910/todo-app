"""Systematic injections for the b-full chart, judged by TLC.

Three plans, one edit per cell. The chart fault grid walks every edge
(transfer, missing transition, wrong effect) and every absent state-event
pair, plus each state removed and one added state. The obligation plan
emits one edit for each conjunct of ``TypeOK``, ``TitleNotEmpty``,
``CompletedAtSet``, and deadlock. The lineage plan edits only sites that
``TitleNotEmpty`` reads. Scoring is the same judge as ``mutants.py``: the
fault on a copy of the spec, or a mapped trace checked against the faithful
``Next``. Operator mutants are not rerun. A cell that is the same fault as
one of those mutants is tagged and is not a new claim.

A redundant archive guard, a field no invariant reads, a single missing
edge that leaves another exit, and an error that never becomes a spec
state are not kills. The cfg is unchanged: no temporal ``PROPERTY``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from event_alignment import MappedTrace, SpecState, SpecStep, trace_config, trace_module
from mutants import _classify, _invariants
from todo_app.alignment import ACTIONS, TaskHistory, advance_task, project_task
from todo_app.task import EDGES, Edge, TodoTask, TransitionRejected

ROOT = os.path.dirname(os.path.abspath(__file__))
WAKE = datetime(2026, 10, 25, tzinfo=timezone.utc)
_VERDICTS = ("killed", "survived", "stillborn")

STATES = (
    "draft",
    "todo",
    "inProgress",
    "snoozed",
    "done",
    "archived",
    "deleted",
)
EVENTS = (
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
_METHODS = {
    "SUBMIT": "submit",
    "DELETE": "delete",
    "START": "start",
    "COMPLETE": "complete",
    "SNOOZE": "snooze",
    "RESUME": "resume",
    "REOPEN": "reopen",
    "ARCHIVE": "archive",
    "UNARCHIVE": "unarchive",
    "RESTORE": "restore",
}
_EFFECT_CLAUSE = {
    "markListed": "!.listed = TRUE",
    "clearListed": "!.listed = FALSE",
    "stampStarted": '!.started_at = "set"',
    "stampCompleted": '!.completed_at = "set"',
    "clearCompleted": '!.completed_at = "unset"',
    "setWakeAt": '!.wake_at = "set"',
    "clearWakeAt": '!.wake_at = "unset"',
    "stampDeleted": '!.deleted_at = "set"',
    "clearDeleted": '!.deleted_at = "unset"',
}
_STATE_SLUG = {
    "draft": "draft",
    "todo": "todo",
    "inProgress": "in-progress",
    "snoozed": "snoozed",
    "done": "done",
    "archived": "archived",
    "deleted": "deleted",
}
# Same fault as a JEF-69 mutant. The row is part of this grid; the kill
# or the miss is not a new claim.
_OVERLAP = {
    "transfer-spec-todo-complete": "crp-complete-target",
    "transfer-todo-complete": "trace-complete-stays",
    "missing-todo-snooze": "sdl-snooze-edge",
    "operation-spec-draft-submit": "crp-listed-false",
    "guard-drop-draft-submit": "sdl-submit-guard",
    "guard-flip-draft-submit": "ror-submit-guard",
    "guard-drop-done-archive": "sdl-archive-guard",
    "extra-in-progress-archive": "trace-archive-in-progress",
    "type-state": "crp-submit-state",
    "unstamped-archive": "hom-unstamped-archive",
}


@dataclass(frozen=True, slots=True)
class Cell:
    name: str
    plans: tuple[str, ...]
    kind: str
    inlet: str
    overlap: str
    spec: str | None
    mode: str
    source: str
    event: str
    target: str
    effect: str
    calls: tuple[str, ...]
    title: str


@dataclass(frozen=True, slots=True)
class Score:
    name: str
    plans: str
    kind: str
    inlet: str
    overlap: str
    expect: str
    actual: str
    detail: str
    note: str
    output: str


def _action_block(spec: str, name: str) -> str:
    found = re.search(rf"(?m)^{name} ==\n(?:  .*\n)+", spec)
    if not found:
        raise RuntimeError(f"no action {name}")
    return found.group(0)


def _replace_block(spec: str, name: str, block: str) -> str:
    old = _action_block(spec, name)
    count = spec.count(old)
    if count != 1:
        raise RuntimeError(f"{name} block found {count} times")
    if block == old:
        raise RuntimeError(f"{name} edit did not change the action")
    return spec.replace(old, block, 1)


def _replace_update(block: str, inside: str) -> str:
    updated, count = re.subn(
        r"task' = \[task EXCEPT [^\]]+\]",
        "task' = [task EXCEPT " + inside + "]",
        block,
        count=1,
    )
    if count != 1 or updated == block:
        raise RuntimeError(f"update not rewritten:\n{block}")
    return updated


def _set_next(spec: str, names: tuple[str, ...]) -> str:
    if not names:
        raise RuntimeError("Next would be empty")
    body = "Next ==\n" + "".join(f"  \\/ {name}\n" for name in names)
    updated, count = re.subn(r"(?m)^Next ==\n(?:  \\/ .*\n)+", body, spec, count=1)
    if count != 1:
        raise RuntimeError("Next block not found")
    return updated


def _set_states(spec: str, names: tuple[str, ...]) -> str:
    body = "States == {\n  " + ", ".join(f'"{name}"' for name in names) + "\n}\n"
    updated, count = re.subn(r"States == \{\n  .*\n\}\n", body, spec, count=1)
    if count != 1:
        raise RuntimeError("States block not found")
    return updated


def _edge(source: str, event: str) -> Edge:
    for edge in EDGES:
        if edge.source == source and edge.event == event:
            return edge
    raise RuntimeError(f"no edge {event} from {source}")


def _wrong_effect(effect: str) -> str:
    if effect == "markListed":
        return "clearListed"
    if effect == "clearListed":
        return "markListed"
    return "markListed"


def _update_inside(target: str, effect: str, submitted: bool) -> str:
    inside = f'!.state = "{target}", {_EFFECT_CLAUSE[effect]}'
    if submitted:
        inside += ", !.submitted = TRUE"
    return inside


def _slug(source: str, event: str) -> str:
    return f"{_STATE_SLUG[source]}-{event.lower()}"


def _prefix(source: str) -> tuple[str, ...]:
    if source == "draft":
        return ()
    if source == "todo":
        return ("submit",)
    if source == "inProgress":
        return ("submit", "start")
    if source == "snoozed":
        return ("submit", "snooze")
    if source == "done":
        return ("submit", "complete")
    if source == "archived":
        return ("submit", "complete", "archive")
    if source == "deleted":
        return ("delete",)
    raise RuntimeError(source)


def _borrow(event: str) -> tuple[str, str]:
    for edge in EDGES:
        if edge.event == event:
            return edge.target, edge.effect
    raise RuntimeError(event)


def _transfer_spec(spec: str, edge: Edge) -> str:
    name = ACTIONS[(edge.source, edge.event)]
    block = _action_block(spec, name)
    submitted = edge.source == "draft" and edge.event == "SUBMIT"
    inside = _update_inside(edge.source, edge.effect, submitted)
    return _replace_block(spec, name, _replace_update(block, inside))


def _operation_spec(spec: str, edge: Edge) -> str:
    name = ACTIONS[(edge.source, edge.event)]
    block = _action_block(spec, name)
    submitted = edge.source == "draft" and edge.event == "SUBMIT"
    inside = _update_inside(edge.target, _wrong_effect(edge.effect), submitted)
    return _replace_block(spec, name, _replace_update(block, inside))


def _missing_spec(spec: str, edge: Edge) -> str:
    name = ACTIONS[(edge.source, edge.event)]
    names = tuple(ACTIONS[(item.source, item.event)] for item in EDGES if item != edge)
    return _set_next(spec, names)


def _guard_spec(spec: str, edge: Edge, flip: bool) -> str:
    name = ACTIONS[(edge.source, edge.event)]
    block = _action_block(spec, name)
    if edge.guard == "titleNotEmpty":
        line = '  /\\ task.title /= ""\n'
    elif edge.guard == "completedAtSet":
        line = '  /\\ task.completed_at /= "unset"\n'
    else:
        raise RuntimeError(edge.guard)
    if line not in block:
        raise RuntimeError(f"{name} has no guard line")
    if flip:
        rewritten = block.replace(line, line.replace("/=", "=", 1), 1)
    else:
        rewritten = block.replace(line, "", 1)
    return _replace_block(spec, name, rewritten)


def _missing_state_spec(spec: str, state: str) -> str:
    replacement = STATES[(STATES.index(state) + 1) % len(STATES)]
    spec = _set_states(spec, tuple(name for name in STATES if name != state))
    kept: list[str] = []
    for edge in EDGES:
        name = ACTIONS[(edge.source, edge.event)]
        if edge.source == state:
            continue
        if edge.target == state:
            block = _action_block(spec, name)
            needle = f'!.state = "{state}"'
            if block.count(needle) != 1:
                raise RuntimeError(f"{name} does not target {state} once")
            spec = _replace_block(spec, name, block.replace(needle, f'!.state = "{replacement}"', 1))
        kept.append(name)
    return _set_next(spec, tuple(kept))


def _extra_state_spec(spec: str) -> str:
    spec = spec.replace(
        '"archived", "deleted"\n}',
        '"archived", "deleted", "limbo"\n}',
        1,
    )
    if ' "limbo"' not in spec:
        raise RuntimeError("limbo was not added to States")
    actions = (
        "LimboEnter ==\n"
        '  /\\ task.state = "draft"\n'
        '  /\\ task\' = [task EXCEPT !.state = "limbo"]\n'
        "\n"
        "LimboLeave ==\n"
        '  /\\ task.state = "limbo"\n'
        '  /\\ task\' = [task EXCEPT !.state = "draft"]\n'
        "\n"
    )
    spec = spec.replace("Next ==\n", actions + "Next ==\n", 1)
    spec = spec.replace(
        "  \\/ DraftSubmit\n",
        "  \\/ DraftSubmit\n  \\/ LimboEnter\n  \\/ LimboLeave\n",
        1,
    )
    if spec.count("LimboEnter") != 2 or spec.count("LimboLeave") != 2:
        raise RuntimeError("limbo actions were not wired into Next")
    return spec


def _type_spec(spec: str, field: str) -> str:
    if field == "state":
        name, old, new = "DraftSubmit", '!.state = "todo"', '!.state = "nowhere"'
    elif field == "title":
        name = "DraftSubmit"
        old = '!.state = "todo", !.listed = TRUE, !.submitted = TRUE'
        new = '!.state = "todo", !.title = "other", !.listed = TRUE, !.submitted = TRUE'
    elif field == "listed":
        name, old, new = "DraftSubmit", "!.listed = TRUE", '!.listed = "yes"'
    elif field == "started_at":
        name, old, new = "TodoStart", '!.started_at = "set"', '!.started_at = "yes"'
    elif field == "completed_at":
        name, old, new = "TodoComplete", '!.completed_at = "set"', '!.completed_at = "yes"'
    elif field == "wake_at":
        name, old, new = "TodoSnooze", '!.wake_at = "set"', '!.wake_at = "yes"'
    elif field == "deleted_at":
        name, old, new = "DraftDelete", '!.deleted_at = "set"', '!.deleted_at = "yes"'
    elif field == "submitted":
        name, old, new = "DraftSubmit", "!.submitted = TRUE", '!.submitted = "yes"'
    else:
        raise RuntimeError(field)
    block = _action_block(spec, name)
    if block.count(old) != 1:
        raise RuntimeError(f"{field} anchor found {block.count(old)} times")
    return _replace_block(spec, name, block.replace(old, new, 1))


def _unstamped_archive_spec(spec: str) -> str:
    for name in ("TodoComplete", "InProgressComplete"):
        block = _action_block(spec, name)
        old = ', !.completed_at = "set"'
        if block.count(old) != 1:
            raise RuntimeError(name)
        spec = _replace_block(spec, name, block.replace(old, "", 1))
    block = _action_block(spec, "DoneArchive")
    line = '  /\\ task.completed_at /= "unset"\n'
    if line not in block:
        raise RuntimeError("archive guard missing")
    return _replace_block(spec, "DoneArchive", block.replace(line, "", 1))


def _lineage_spec(spec: str, site: str) -> str:
    if site == "init-submitted":
        old, new = "submitted |-> FALSE", "submitted |-> TRUE"
        if spec.count(old) != 1:
            raise RuntimeError(site)
        return spec.replace(old, new, 1)
    if site == "submit-clears-title":
        block = _action_block(spec, "DraftSubmit")
        old = "!.submitted = TRUE]"
        new = '!.submitted = TRUE, !.title = ""]'
        if block.count(old) != 1:
            raise RuntimeError(site)
        return _replace_block(spec, "DraftSubmit", block.replace(old, new, 1))
    if site == "submit-omits-submitted":
        block = _action_block(spec, "DraftSubmit")
        old = ", !.submitted = TRUE"
        if block.count(old) != 1:
            raise RuntimeError(site)
        return _replace_block(spec, "DraftSubmit", block.replace(old, "", 1))
    if site == "restore-sets-submitted":
        block = _action_block(spec, "DeletedRestore")
        old = '!.deleted_at = "unset"]'
        new = '!.deleted_at = "unset", !.submitted = TRUE]'
        if block.count(old) != 1:
            raise RuntimeError(site)
        return _replace_block(spec, "DeletedRestore", block.replace(old, new, 1))
    raise RuntimeError(site)


def _cfg(name: str, plans: tuple[str, ...], kind: str, spec: str, effect: str = "") -> Cell:
    return Cell(
        name,
        plans,
        kind,
        "cfg",
        _OVERLAP.get(name, ""),
        spec,
        "",
        "",
        "",
        "",
        effect,
        (),
        "",
    )


def _trace(
    name: str,
    plans: tuple[str, ...],
    kind: str,
    mode: str,
    source: str,
    event: str,
    target: str,
    effect: str,
) -> Cell:
    return Cell(
        name,
        plans,
        kind,
        "trace",
        _OVERLAP.get(name, ""),
        None,
        mode,
        source,
        event,
        target,
        effect,
        _prefix(source) + (_METHODS[event],),
        "task",
    )


def _cells(spec: str) -> tuple[Cell, ...]:
    present = {(edge.source, edge.event) for edge in EDGES}
    absent = [(state, event) for state in STATES for event in EVENTS if (state, event) not in present]
    if len(EDGES) != 13 or len(absent) != 57 or len(STATES) != 7 or len(EVENTS) != 10:
        raise RuntimeError("chart size changed; the grid is no longer the one this plan walks")
    cells: list[Cell] = []
    for edge in EDGES:
        slug = _slug(edge.source, edge.event)
        cells.append(
            _trace(
                f"transfer-{slug}",
                ("chart",),
                "transfer",
                "transfer",
                edge.source,
                edge.event,
                edge.source,
                edge.effect,
            )
        )
        cells.append(
            _cfg(
                f"transfer-spec-{slug}",
                ("chart",),
                "transfer-spec",
                _transfer_spec(spec, edge),
                edge.effect,
            )
        )
        plans: tuple[str, ...] = ("chart",)
        if edge.source == "snoozed" and edge.event == "RESUME":
            plans = ("chart", "obligation")
        cells.append(_cfg(f"missing-{slug}", plans, "missing", _missing_spec(spec, edge), edge.effect))
        cells.append(
            _trace(
                f"operation-{slug}",
                ("chart",),
                "operation",
                "operation",
                edge.source,
                edge.event,
                edge.target,
                edge.effect,
            )
        )
        cells.append(
            _cfg(
                f"operation-spec-{slug}",
                ("chart",),
                "operation-spec",
                _operation_spec(spec, edge),
                edge.effect,
            )
        )
        if edge.guard:
            drop_plans: tuple[str, ...] = ("chart",)
            if edge.guard == "titleNotEmpty":
                drop_plans = ("chart", "obligation", "lineage")
            elif edge.guard == "completedAtSet":
                drop_plans = ("chart", "obligation")
            cells.append(
                _cfg(
                    f"guard-drop-{slug}",
                    drop_plans,
                    "guard-drop",
                    _guard_spec(spec, edge, False),
                    edge.guard,
                )
            )
            cells.append(
                _cfg(
                    f"guard-flip-{slug}",
                    ("chart",),
                    "guard-flip",
                    _guard_spec(spec, edge, True),
                    edge.guard,
                )
            )
    for state, event in absent:
        target, effect = _borrow(event)
        cells.append(
            _trace(
                f"extra-{_slug(state, event)}",
                ("chart",),
                "extra",
                "extra",
                state,
                event,
                target,
                effect,
            )
        )
    for state in STATES:
        cells.append(
            _cfg(
                f"missing-state-{_STATE_SLUG[state]}",
                ("chart",),
                "missing-state",
                _missing_state_spec(spec, state),
                state,
            )
        )
    cells.append(_trace("extra-state", ("chart",), "extra-state", "limbo", "draft", "SUBMIT", "limbo", "markListed"))
    cells.append(_cfg("extra-state-spec", ("chart",), "extra-state-spec", _extra_state_spec(spec)))
    for field in (
        "state",
        "title",
        "listed",
        "started_at",
        "completed_at",
        "wake_at",
        "deleted_at",
        "submitted",
    ):
        cells.append(_cfg(f"type-{field.replace('_', '-')}", ("obligation",), "type", _type_spec(spec, field), field))
    cells.append(_cfg("unstamped-archive", ("obligation",), "unstamped-archive", _unstamped_archive_spec(spec)))
    for site in (
        "init-submitted",
        "submit-clears-title",
        "submit-omits-submitted",
        "restore-sets-submitted",
    ):
        cells.append(_cfg(site, ("lineage",), "lineage", _lineage_spec(spec, site), site))
    names = [cell.name for cell in cells]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate cell name")
    return tuple(cells)


class _Transfer(TodoTask):
    """One edge keeps its guard and effect and writes ``target``."""

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


class _Operation(TodoTask):
    """One edge keeps its target and writes the other listed bit, or listed instead of its effect."""

    def __init__(self, title: str, source: str, event: str) -> None:
        super().__init__(title)
        self._source = source
        self._event = event

    def _apply(self, event: str, wake_at: datetime | None = None) -> None:
        if self.state == self._source and event == self._event:
            edge = _edge(self.state, event)
            if edge.guard is not None and not self._guard_holds(edge.guard):
                raise TransitionRejected(self.state, event, edge.guard)
            self._run_effect(_wrong_effect(edge.effect), wake_at)
            self.state = edge.target
            return
        super()._apply(event, wake_at)


class _Extra(TodoTask):
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


def _make(cell: Cell) -> TodoTask:
    if cell.mode == "transfer":
        return _Transfer(cell.title, cell.source, cell.event, cell.target)
    if cell.mode == "limbo":
        return _Transfer(cell.title, cell.source, cell.event, "limbo")
    if cell.mode == "operation":
        return _Operation(cell.title, cell.source, cell.event)
    if cell.mode == "extra":
        return _Extra(cell.title, cell.source, cell.event, cell.target, cell.effect)
    raise RuntimeError(cell.mode)


def _project(task: TodoTask, calls: tuple[str, ...]) -> MappedTrace:
    history: object = TaskHistory()
    current = SpecState(project_task(task, history))
    steps: list[SpecStep] = []
    for method in calls:
        source = task.state
        if method == "snooze":
            task.snooze(WAKE)
        else:
            getattr(task, method)()
        event = _METHODS_BY_NAME[method]
        try:
            action = ACTIONS[(source, event)]
        except KeyError:
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
    return MappedTrace("task", steps[0].pre, tuple(steps))


_METHODS_BY_NAME = {method: event for event, method in _METHODS.items()}


def _run(jar: str, work: str, module_name: str, config: str) -> tuple[int, str]:
    module_path = os.path.join(work, module_name)
    config_path = os.path.join(work, os.path.splitext(module_name)[0] + ".cfg")
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(config)
    proc = subprocess.run(
        [
            "java",
            "-Xmx512m",
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


def _judge(output: str, code: int, depth: int | None) -> tuple[str, str]:
    """``mutants._classify``, plus the two invariant sentences that checker does not match.

    A violation of the initial state is an invariant hit. ``Evaluating
    invariant X failed`` after TLC has built a state is the same hit when
    ``X`` cannot compare the bad value. A missing field with no successor
    stays stillborn.
    """

    actual, detail = _classify(output, code, depth)
    if actual == "killed":
        return actual, detail
    initial = re.search(r"^Error: Invariant (\w+) is violated by the initial state:", output, re.M)
    if initial:
        return "killed", f"invariant {initial.group(1)} at init"
    failed = re.search(r"^Error: Evaluating invariant (\w+) failed\.", output, re.M)
    if failed and "The behavior up to this point is:" in output:
        return "killed", f"invariant {failed.group(1)} did not evaluate"
    return actual, detail


def _note(cell: Cell, actual: str) -> str:
    if actual == "stillborn":
        return "not a kill: the fault never becomes a spec state"
    if actual != "survived":
        return ""
    if cell.kind == "guard-drop" and cell.effect == "completedAtSet":
        return "not a kill: redundant archive guard"
    if cell.kind == "guard-flip" and cell.effect == "completedAtSet":
        return "not a kill: flipped archive guard never enables"
    if cell.kind == "missing":
        return "not a kill: one edge left another exit"
    if cell.kind == "operation-spec":
        if cell.effect in {"stampCompleted", "clearCompleted"}:
            return "not a kill: completed_at is unread on the states this edit reaches"
        return "not a kill: field no invariant reads"
    if cell.kind == "transfer-spec":
        return "cfg does not kill a self-loop that stays well-typed"
    if cell.kind == "missing-state":
        return "cfg accepts the collapsed state"
    if cell.kind == "extra-state-spec":
        return "cfg accepts an added state that has an exit"
    if cell.kind == "lineage" and cell.effect == "submit-omits-submitted":
        return "on the TitleNotEmpty lineage; the antecedent stays false"
    return ""


def _score_cfg(jar: str, cell: Cell, cfg: str) -> tuple[str, str, str]:
    if cell.spec is None or not cfg:
        raise RuntimeError(cell.name)
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "TodoTask.tla")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(cell.spec)
        code, output = _run(jar, work, "TodoTask.tla", cfg)
    actual, detail = _judge(output, code, None)
    return actual, detail, output


def _score_trace(jar: str, cell: Cell, invariants: tuple[str, ...]) -> tuple[str, str, str]:
    try:
        mapped = _project(_make(cell), cell.calls)
    except (TransitionRejected, TypeError, RuntimeError) as exc:
        return "stillborn", f"{type(exc).__name__}: {exc}", ""
    module = trace_module(mapped)
    config = trace_config(invariants)
    with tempfile.TemporaryDirectory() as work:
        spec_from = os.path.join(ROOT, "spec", "TodoTask.tla")
        with open(spec_from, encoding="utf-8") as handle:
            spec = handle.read()
        with open(os.path.join(work, "TodoTask.tla"), "w", encoding="utf-8") as handle:
            handle.write(spec)
        with open(os.path.join(work, "MappedTrace.tla"), "w", encoding="utf-8") as handle:
            handle.write(module)
        code, output = _run(jar, work, "MappedTrace.tla", config)
    actual, detail = _judge(output, code, len(mapped.states()))
    return actual, detail, output


def _score_one(payload: tuple[str, str, tuple[str, ...], Cell]) -> tuple[str, str, str, str]:
    jar, cfg, invariants, cell = payload
    if cell.inlet == "cfg":
        actual, detail, output = _score_cfg(jar, cell, cfg)
    elif cell.inlet == "trace":
        actual, detail, output = _score_trace(jar, cell, invariants)
    else:
        raise RuntimeError(cell.inlet)
    return cell.name, actual, detail, output


def _line(row: Score) -> str:
    mark = "ok" if row.actual == row.expect else "MISMATCH"
    overlap = row.overlap or "-"
    note = row.note or "-"
    return (
        f"{row.name:<36} {row.kind:<18} {row.inlet:<5} "
        f"{row.actual:<9} {row.detail:<32} {overlap:<28} {note} {mark}"
    )


# Locked TLC verdicts. A drift fails this command. Overlap tags are not verdicts.
EXPECT: dict[str, str] = {
    "transfer-draft-submit": "killed",
    "transfer-spec-draft-submit": "survived",
    "missing-draft-submit": "survived",
    "operation-draft-submit": "killed",
    "operation-spec-draft-submit": "survived",
    "guard-drop-draft-submit": "killed",
    "guard-flip-draft-submit": "killed",
    "transfer-draft-delete": "killed",
    "transfer-spec-draft-delete": "survived",
    "missing-draft-delete": "killed",
    "operation-draft-delete": "killed",
    "operation-spec-draft-delete": "survived",
    "transfer-todo-start": "killed",
    "transfer-spec-todo-start": "survived",
    "missing-todo-start": "survived",
    "operation-todo-start": "killed",
    "operation-spec-todo-start": "survived",
    "transfer-todo-complete": "killed",
    "transfer-spec-todo-complete": "survived",
    "missing-todo-complete": "survived",
    "operation-todo-complete": "killed",
    "operation-spec-todo-complete": "survived",
    "transfer-todo-snooze": "killed",
    "transfer-spec-todo-snooze": "survived",
    "missing-todo-snooze": "survived",
    "operation-todo-snooze": "killed",
    "operation-spec-todo-snooze": "survived",
    "transfer-todo-delete": "killed",
    "transfer-spec-todo-delete": "survived",
    "missing-todo-delete": "survived",
    "operation-todo-delete": "killed",
    "operation-spec-todo-delete": "survived",
    "transfer-in-progress-snooze": "killed",
    "transfer-spec-in-progress-snooze": "survived",
    "missing-in-progress-snooze": "survived",
    "operation-in-progress-snooze": "killed",
    "operation-spec-in-progress-snooze": "survived",
    "transfer-in-progress-complete": "killed",
    "transfer-spec-in-progress-complete": "survived",
    "missing-in-progress-complete": "survived",
    "operation-in-progress-complete": "killed",
    "operation-spec-in-progress-complete": "survived",
    "transfer-snoozed-resume": "killed",
    "transfer-spec-snoozed-resume": "survived",
    "missing-snoozed-resume": "killed",
    "operation-snoozed-resume": "killed",
    "operation-spec-snoozed-resume": "survived",
    "transfer-done-reopen": "killed",
    "transfer-spec-done-reopen": "survived",
    "missing-done-reopen": "survived",
    "operation-done-reopen": "killed",
    "operation-spec-done-reopen": "survived",
    "transfer-done-archive": "killed",
    "transfer-spec-done-archive": "survived",
    "missing-done-archive": "survived",
    "operation-done-archive": "killed",
    "operation-spec-done-archive": "survived",
    "guard-drop-done-archive": "survived",
    "guard-flip-done-archive": "survived",
    "transfer-archived-unarchive": "killed",
    "transfer-spec-archived-unarchive": "survived",
    "missing-archived-unarchive": "killed",
    "operation-archived-unarchive": "killed",
    "operation-spec-archived-unarchive": "survived",
    "transfer-deleted-restore": "killed",
    "transfer-spec-deleted-restore": "survived",
    "missing-deleted-restore": "killed",
    "operation-deleted-restore": "killed",
    "operation-spec-deleted-restore": "survived",
    "extra-draft-start": "killed",
    "extra-draft-complete": "killed",
    "extra-draft-snooze": "killed",
    "extra-draft-resume": "killed",
    "extra-draft-reopen": "killed",
    "extra-draft-archive": "killed",
    "extra-draft-unarchive": "killed",
    "extra-draft-restore": "killed",
    "extra-todo-submit": "killed",
    "extra-todo-resume": "killed",
    "extra-todo-reopen": "killed",
    "extra-todo-archive": "killed",
    "extra-todo-unarchive": "killed",
    "extra-todo-restore": "killed",
    "extra-in-progress-submit": "killed",
    "extra-in-progress-delete": "killed",
    "extra-in-progress-start": "killed",
    "extra-in-progress-resume": "killed",
    "extra-in-progress-reopen": "killed",
    "extra-in-progress-archive": "killed",
    "extra-in-progress-unarchive": "killed",
    "extra-in-progress-restore": "killed",
    "extra-snoozed-submit": "killed",
    "extra-snoozed-delete": "killed",
    "extra-snoozed-start": "killed",
    "extra-snoozed-complete": "killed",
    "extra-snoozed-snooze": "killed",
    "extra-snoozed-reopen": "killed",
    "extra-snoozed-archive": "killed",
    "extra-snoozed-unarchive": "killed",
    "extra-snoozed-restore": "killed",
    "extra-done-submit": "killed",
    "extra-done-delete": "killed",
    "extra-done-start": "killed",
    "extra-done-complete": "killed",
    "extra-done-snooze": "killed",
    "extra-done-resume": "killed",
    "extra-done-unarchive": "killed",
    "extra-done-restore": "killed",
    "extra-archived-submit": "killed",
    "extra-archived-delete": "killed",
    "extra-archived-start": "killed",
    "extra-archived-complete": "killed",
    "extra-archived-snooze": "killed",
    "extra-archived-resume": "killed",
    "extra-archived-reopen": "killed",
    "extra-archived-archive": "killed",
    "extra-archived-restore": "killed",
    "extra-deleted-submit": "killed",
    "extra-deleted-delete": "killed",
    "extra-deleted-start": "killed",
    "extra-deleted-complete": "killed",
    "extra-deleted-snooze": "killed",
    "extra-deleted-resume": "killed",
    "extra-deleted-reopen": "killed",
    "extra-deleted-archive": "killed",
    "extra-deleted-unarchive": "killed",
    "missing-state-draft": "killed",
    "missing-state-todo": "survived",
    "missing-state-in-progress": "survived",
    "missing-state-snoozed": "survived",
    "missing-state-done": "survived",
    "missing-state-archived": "survived",
    "missing-state-deleted": "survived",
    "extra-state": "killed",
    "extra-state-spec": "survived",
    "type-state": "killed",
    "type-title": "killed",
    "type-listed": "killed",
    "type-started-at": "killed",
    "type-completed-at": "killed",
    "type-wake-at": "killed",
    "type-deleted-at": "killed",
    "type-submitted": "killed",
    "unstamped-archive": "killed",
    "init-submitted": "killed",
    "submit-clears-title": "killed",
    "submit-omits-submitted": "survived",
    "restore-sets-submitted": "killed",
}


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
        cells = _cells(spec)
        invariants = _invariants(cfg)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    if set(EXPECT) != {cell.name for cell in cells}:
        missing = sorted({cell.name for cell in cells} - set(EXPECT))
        extra = sorted(set(EXPECT) - {cell.name for cell in cells})
        print(f"expect table does not match the grid ({len(missing)} missing, {len(extra)} extra)", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "TodoTask.tla")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(spec)
        code, faithful_output = _run(jar, work, "TodoTask.tla", cfg)
    faithful_actual, faithful_detail = _judge(faithful_output, code, None)
    if faithful_actual != "survived":
        print(f"faithful model {faithful_actual}: {faithful_detail}", file=sys.stderr)
        print(faithful_output, file=sys.stderr)
        return 2

    workers = int(os.environ.get("INJECT_WORKERS", "2"))
    payloads = [(jar, cfg, invariants, cell) for cell in cells]
    found: dict[str, tuple[str, str, str]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for name, actual, detail, output in pool.map(_score_one, payloads):
            found[name] = (actual, detail, output)

    rows: list[Score] = []
    for cell in cells:
        actual, detail, output = found[cell.name]
        rows.append(
            Score(
                cell.name,
                "+".join(cell.plans),
                cell.kind,
                cell.inlet,
                cell.overlap,
                EXPECT[cell.name],
                actual,
                detail,
                _note(cell, actual),
                output,
            )
        )

    log_dir = os.environ.get("INJECT_LOG_DIR")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "faithful.txt"), "w", encoding="utf-8") as handle:
            handle.write(faithful_output)
    version = next((line for line in faithful_output.splitlines() if line.startswith("TLC2 ")), "TLC")
    print(version)
    print(
        f"{'cell':<36} {'kind':<18} {'inlet':<5} "
        f"{'verdict':<9} {'detail':<32} {'overlap':<28} note"
    )
    mismatches: list[Score] = []
    for row in rows:
        print(_line(row))
        if row.actual != row.expect or row.actual not in _VERDICTS:
            mismatches.append(row)
        if log_dir:
            with open(os.path.join(log_dir, f"{row.name}.txt"), "w", encoding="utf-8") as handle:
                handle.write(row.output)
    totals = {name: sum(row.actual == name for row in rows) for name in _VERDICTS}
    fresh = [row for row in rows if not row.overlap]
    fresh_totals = {name: sum(row.actual == name for row in fresh) for name in _VERDICTS}
    print(
        f"cells {len(rows)}, killed {totals['killed']}, "
        f"survived {totals['survived']}, stillborn {totals['stillborn']}"
    )
    print(
        "excluding JEF-69 overlaps: "
        f"killed {fresh_totals['killed']}, survived {fresh_totals['survived']}, "
        f"stillborn {fresh_totals['stillborn']}"
    )
    if any(row.actual not in _VERDICTS for row in rows):
        for row in rows:
            if row.actual not in _VERDICTS:
                print(f"unjudged {row.name}: {row.detail}", file=sys.stderr)
                tail = "\n".join(row.output.splitlines()[-30:])
                print(tail, file=sys.stderr)
        return 1
    by_kind: dict[str, list[Score]] = {}
    for row in rows:
        by_kind.setdefault(row.kind, []).append(row)
    for kind in sorted(by_kind):
        group = by_kind[kind]
        bits = ", ".join(f"{name} {sum(row.actual == name for row in group)}" for name in _VERDICTS)
        print(f"  {kind}: {bits}")
    if mismatches:
        for row in mismatches:
            print(f"\n--- {row.name} expected {row.expect}, TLC said {row.actual} ---", file=sys.stderr)
            tail = "\n".join(row.output.splitlines()[-40:])
            print(tail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
