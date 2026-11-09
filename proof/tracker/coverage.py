"""Coverage of recorded traces: committed actions, refused actions, status edges, abstract states."""

from __future__ import annotations

from proof.tracker.mapping import View
from proof.tracker.recording import Trace
from proof.tracker.spec import next_actions


STATUS_EDGES = frozenset(
    {
        ("backlog", "todo", "Triage"),
        ("todo", "inProgress", "Start"),
        ("inProgress", "inReview", "SubmitReview"),
        ("inReview", "inProgress", "RequestChanges"),
        ("inReview", "done", "Approve"),
        ("backlog", "canceled", "Cancel"),
        ("todo", "canceled", "Cancel"),
        ("inProgress", "canceled", "Cancel"),
        ("inReview", "canceled", "Cancel"),
        ("done", "todo", "Reopen"),
        ("canceled", "todo", "Reopen"),
    }
)

Traces = tuple[Trace, ...] | list[Trace]


def committed_actions(traces: Traces) -> frozenset[str]:
    return frozenset(step.action for trace in traces for step in trace.steps if step.kind == "commit")


def refused_actions(traces: Traces) -> frozenset[str]:
    return frozenset(step.action for trace in traces for step in trace.steps if step.kind == "refused")


def status_edges(traces: Traces) -> frozenset[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for trace in traces:
        previous: dict[tuple[str, int], str] = {}
        for step in trace.steps:
            current = {
                (project.key, issue.number): issue.status
                for project in step.post.projects
                for issue in project.issues
            }
            for ident, status in current.items():
                before = previous.get(ident)
                if before is not None and before != status:
                    found.add((before, status, step.action))
            previous = current
    return frozenset(found)


def abstract_states(traces: Traces) -> frozenset[View]:
    found = {View(projects=())}
    for trace in traces:
        for step in trace.steps:
            found.add(step.post)
    return frozenset(found)


def report(groups: dict[str, Traces]) -> str:
    everything: list[Trace] = [trace for traces in groups.values() for trace in traces]
    expected = frozenset(next_actions())
    committed = committed_actions(everything)
    refused = refused_actions(everything)
    edges = status_edges(everything)
    lines = [f"{name} traces: {len(traces)}" for name, traces in groups.items()]
    lines.extend(
        [
            f"steps: {sum(len(trace.steps) for trace in everything)}",
            f"committed actions: {len(committed & expected)}/{len(expected)}",
            f"refused actions: {len(refused & expected)}/{len(expected)}",
            f"status edges: {len(edges & STATUS_EDGES)}/{len(STATUS_EDGES)}",
            f"abstract states covered by traces: {len(abstract_states(everything))}",
        ]
    )
    for name, traces in groups.items():
        lines.append(f"abstract states in {name} traces: {len(abstract_states(traces))}")
    missing_committed = sorted(expected - committed)
    missing_refused = sorted(expected - refused)
    missing_edges = sorted(STATUS_EDGES - edges)
    if missing_committed:
        lines.append("never committed: " + ", ".join(missing_committed))
    if missing_refused:
        lines.append("never refused: " + ", ".join(missing_refused))
    if missing_edges:
        lines.append(
            "missing status edges: "
            + ", ".join(f"{src}->{dst} {action}" for src, dst, action in missing_edges)
        )
    return "\n".join(lines)


def coverage_holds(groups: dict[str, Traces]) -> None:
    everything: list[Trace] = [trace for traces in groups.values() for trace in traces]
    expected = frozenset(next_actions())
    if expected - committed_actions(everything) or expected - refused_actions(everything):
        raise AssertionError(report(groups))
    if STATUS_EDGES - status_edges(everything):
        raise AssertionError(report(groups))
    edge_actions = {action for _src, _dst, action in STATUS_EDGES}
    if not edge_actions <= expected:
        raise AssertionError("STATUS_EDGES names an action that is not in Next")
