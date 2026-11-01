"""The five readable lifecycle scenarios, recorded as spec traces.

These are the same walks as ``tests/test_lifecycle_scenarios.py``. The
generated tests stay the readable checks. This module is what the trace
checker runs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from proof.registry import Trace, recording
from todo_app.application.service import TaskService
from todo_app.composition import in_memory_app


WAKE = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def scenario_1(service: TaskService) -> None:
    task_id = service.add_task("Buy oat milk").id
    service.submit(task_id)
    service.start(task_id)
    service.snooze(task_id, WAKE)
    service.resume(task_id)
    service.complete(task_id)
    service.archive(task_id)
    service.unarchive(task_id)
    service.reopen(task_id)
    service.delete(task_id)
    service.restore(task_id)


def scenario_2(service: TaskService) -> None:
    task_id = service.add_task("Buy oat milk").id
    service.delete(task_id)
    service.restore(task_id)


def scenario_3(service: TaskService) -> None:
    task_id = service.add_task("Buy oat milk").id
    service.submit(task_id)
    service.complete(task_id)


def scenario_4(service: TaskService) -> None:
    task_id = service.add_task("Buy oat milk").id
    service.submit(task_id)
    service.snooze(task_id, WAKE)


def scenario_5(service: TaskService) -> None:
    first = service.add_task("Buy oat milk").id
    service.add_task("Walk the dog")
    service.submit(first)


SCENARIOS = (
    ("scenario-1", scenario_1),
    ("scenario-2", scenario_2),
    ("scenario-3", scenario_3),
    ("scenario-4", scenario_4),
    ("scenario-5", scenario_5),
)


def recorded_scenarios() -> tuple[Trace, ...]:
    traces = []
    for name, walk in SCENARIOS:
        service = in_memory_app()
        with recording() as recorder:
            walk(service)
        traces.append(recorder.to_trace(name, "scenario"))
    return tuple(traces)
