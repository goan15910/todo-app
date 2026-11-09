"""Mappings that must get stuck, and the line where they get stuck."""

from __future__ import annotations

from dataclasses import dataclass, replace

from proof.mapping import TaskView
from proof.registry import Patch, Trace, recording
from todo_app.composition import in_memory_app


@dataclass(frozen=True)
class Negative:
    trace: Trace
    stuck_index: int
    kind: str


def negative_cases() -> tuple[Negative, ...]:
    return (
        _missing_decorator(),
        _wrong_name(),
        _wrong_parameter(),
        _wrong_post_state(),
    )


def _missing_decorator() -> Negative:
    service = in_memory_app()
    with recording(Patch(drop=frozenset({"submit"}))) as recorder:
        service.add_task("milk")
        service.submit(1)
    trace = recorder.to_trace("missing-decorator", "negative")
    return Negative(trace, 1, "guard")


def _wrong_name() -> Negative:
    service = in_memory_app()
    with recording(Patch(rename=(("submit", "TodoStart"),))) as recorder:
        service.add_task("milk")
        service.submit(1)
    trace = recorder.to_trace("wrong-name", "negative")
    return Negative(trace, 1, "guard")


def _wrong_parameter() -> Negative:
    service = in_memory_app()
    with recording(Patch(force_task_id=(("submit", 2),))) as recorder:
        service.add_task("milk")
        service.submit(1)
    trace = recorder.to_trace("wrong-parameter", "negative")
    return Negative(trace, 1, "guard")


def _wrong_post_state() -> Negative:
    service = in_memory_app()
    with recording() as recorder:
        service.add_task("milk")
    trace = recorder.to_trace("wrong-post", "negative")
    step = trace.steps[0]
    listed = replace(step.post.tasks[0], listed=True)
    bad_step = replace(step, post=replace(step.post, tasks=(listed,)))
    if not isinstance(listed, TaskView):
        raise RuntimeError("post state was not a task view")
    return Negative(replace(trace, steps=(bad_step,)), 0, "state")
