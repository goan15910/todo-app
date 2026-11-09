"""Mappings that must get stuck, and the line where they get stuck.

Each case records scenario-like steps through the fake with a patched
recorder, then names the step index TLC must stop at and whether the
guard or the state is what fails.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from proof.tracker.mapping import IssueView, ProjectView, View
from proof.tracker.recording import Patch, Trace, recording_stack
from tracker.bootstrap import in_memory
from tracker.domain.errors import Rejected


@dataclass(frozen=True)
class Negative:
    trace: Trace
    stuck_index: int
    kind: str
    method: str


def negative_cases() -> tuple[Negative, ...]:
    return (
        _missing_label(),
        _wrong_name(),
        _wrong_parameter(),
        _refusal_recorded_as_commit(),
        _commit_recorded_as_refusal(),
        _wrong_post_state(),
    )


def _walk(patch: Patch | None) -> Trace:
    services, recorder = recording_stack(in_memory(), patch)
    services.create_project("ENG", "Engineering")
    services.create_issue("ENG", "First")
    services.triage("ENG-1")
    try:
        services.start("ENG-1")
    except Rejected:
        pass
    services.assign("ENG-1", "alice")
    services.start("ENG-1")
    return recorder.to_trace("negative", "negative")


def _missing_label() -> Negative:
    trace = replace(_walk(Patch(drop=frozenset({"triage"}))), name="missing-label")
    return Negative(trace, 2, "guard", "triage")


def _wrong_name() -> Negative:
    trace = replace(_walk(Patch(rename=(("triage", "Start"),))), name="wrong-name")
    return Negative(trace, 2, "guard", "triage")


def _wrong_parameter() -> Negative:
    trace = replace(_walk(Patch(force_number=(("triage", 2),))), name="wrong-parameter")
    return Negative(trace, 2, "guard", "triage")


def _refusal_recorded_as_commit() -> Negative:
    # The refused start at index 3 is relabelled as a commit: the guard is false.
    trace = replace(_walk(Patch(force_kind=(("start", "commit"),))), name="refusal-as-commit")
    return Negative(trace, 3, "guard", "start")


def _commit_recorded_as_refusal() -> Negative:
    # Every assign is relabelled as refused; the one at index 4 was enabled.
    trace = replace(_walk(Patch(force_kind=(("assign", "refused"),))), name="commit-as-refusal")
    return Negative(trace, 4, "guard", "assign")


def _wrong_post_state() -> Negative:
    trace = replace(_walk(None), name="wrong-post")
    step = trace.steps[1]
    project = step.post.projects[0]
    issue = project.issues[0]
    if not isinstance(issue, IssueView) or not isinstance(project, ProjectView):
        raise RuntimeError("post state was not a view")
    tampered = replace(issue, status="todo")
    post = View(projects=(replace(project, issues=(tampered,)),))
    steps = list(trace.steps)
    steps[1] = replace(step, post=post)
    return Negative(replace(trace, steps=tuple(steps)), 1, "state", "create_issue")
