"""Coverage of recorded traces: actions, then status edges, then abstract states."""

from __future__ import annotations

from proof.mapping import View
from proof.registry import Trace
from proof.spec import next_actions


# One status edge per non-Add action. Add creates a task; it is not an edge.
STATUS_EDGES = frozenset(
    {
        ("draft", "todo", "DraftSubmit"),
        ("draft", "deleted", "DraftDelete"),
        ("todo", "inProgress", "TodoStart"),
        ("todo", "done", "TodoComplete"),
        ("todo", "snoozed", "TodoSnooze"),
        ("todo", "deleted", "TodoDelete"),
        ("inProgress", "snoozed", "InProgressSnooze"),
        ("inProgress", "done", "InProgressComplete"),
        ("snoozed", "inProgress", "SnoozedResume"),
        ("done", "todo", "DoneReopen"),
        ("done", "archived", "DoneArchive"),
        ("archived", "done", "ArchivedUnarchive"),
        ("deleted", "todo", "DeletedRestore"),
    }
)


def actions_of(traces: tuple[Trace, ...] | list[Trace]) -> frozenset[str]:
    return frozenset(step.action for trace in traces for step in trace.steps)


def status_edges(traces: tuple[Trace, ...] | list[Trace]) -> frozenset[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for trace in traces:
        previous: dict[int, str] = {}
        for step in trace.steps:
            current = {task.id: task.state for task in step.post.tasks}
            for task_id, state in current.items():
                before = previous.get(task_id)
                if before is not None and before != state:
                    found.add((before, state, step.action))
            previous = current
    return frozenset(found)


def abstract_states(traces: tuple[Trace, ...] | list[Trace]) -> frozenset[tuple[object, ...]]:
    found = {_freeze(_empty())}
    for trace in traces:
        for step in trace.steps:
            found.add(_freeze(step.post))
    return frozenset(found)


def has_empty_title(traces: tuple[Trace, ...] | list[Trace]) -> bool:
    return any(
        task.title == ""
        for trace in traces
        for step in trace.steps
        for task in step.post.tasks
    )


def report(scenarios: tuple[Trace, ...], hypothesis: tuple[Trace, ...]) -> str:
    scenario_actions = actions_of(scenarios)
    scenario_edges = status_edges(scenarios)
    scenario_states = abstract_states(scenarios)
    all_traces = tuple(scenarios) + tuple(hypothesis)
    covered_actions = actions_of(all_traces)
    covered_edges = status_edges(all_traces)
    covered_states = abstract_states(all_traces)
    expected_actions = frozenset(next_actions())
    missing_actions = sorted(expected_actions - covered_actions)
    missing_edges = sorted(STATUS_EDGES - covered_edges)
    lines = [
        f"scenario traces: {len(scenarios)}",
        f"hypothesis traces: {len(hypothesis)}",
        f"actions: {len(covered_actions & expected_actions)}/{len(expected_actions)}",
        f"scenario actions: {len(scenario_actions & expected_actions)}/{len(expected_actions)}",
        f"status edges: {len(covered_edges & STATUS_EDGES)}/{len(STATUS_EDGES)}",
        f"scenario status edges: {len(scenario_edges & STATUS_EDGES)}/{len(STATUS_EDGES)}",
        f"abstract states in the five scenarios: {len(scenario_states)}",
        f"abstract states in hypothesis traces: {len(abstract_states(hypothesis))}",
        f"abstract states covered by traces: {len(covered_states)}",
        f"empty title in hypothesis traces: {has_empty_title(hypothesis)}",
    ]
    if missing_actions:
        lines.append("missing actions: " + ", ".join(missing_actions))
    if missing_edges:
        lines.append(
            "missing status edges: "
            + ", ".join(f"{src}->{dst} {action}" for src, dst, action in missing_edges)
        )
    return "\n".join(lines)


def coverage_holds(scenarios: tuple[Trace, ...], hypothesis: tuple[Trace, ...]) -> None:
    all_traces = tuple(scenarios) + tuple(hypothesis)
    missing_actions = frozenset(next_actions()) - actions_of(all_traces)
    missing_edges = STATUS_EDGES - status_edges(all_traces)
    if missing_actions or missing_edges:
        raise AssertionError(report(scenarios, hypothesis))
    edge_actions = {action for _src, _dst, action in STATUS_EDGES}
    if edge_actions != frozenset(next_actions()) - {"Add"}:
        raise AssertionError("STATUS_EDGES does not match Next")


def _empty() -> View:
    return View(seq=1, tasks=())


def _freeze(view: View) -> tuple[object, ...]:
    return (view.seq, view.tasks)
