"""Hypothesis state machine on the in-memory fake, bounded by MaxTasks."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from proof.registry import Recorder, Trace, activate, deactivate
from proof.spec import max_tasks
from todo_app.application.errors import TaskNotFound
from todo_app.composition import in_memory_app
from todo_app.domain.errors import TaskRejected
from todo_app.domain.task import Status


WAKE = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
_EXAMPLES = 30
_STEPS = 18
_TITLES = ("", "milk")
_LEGAL = {
    "draft": ("submit", "delete"),
    "todo": ("start", "complete", "snooze", "delete"),
    "inProgress": ("complete", "snooze"),
    "snoozed": ("resume",),
    "done": ("reopen", "archive"),
    "archived": ("unarchive",),
    "deleted": ("restore",),
}
_ALL = (
    "submit",
    "delete",
    "start",
    "complete",
    "snooze",
    "resume",
    "reopen",
    "archive",
    "unarchive",
    "restore",
)


def hypothesis_traces() -> tuple[Trace, ...]:
    found: list[Trace] = []
    bound = max_tasks()

    class TaskMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.service = in_memory_app()
            self._recorder = Recorder()
            self._tokens = activate(self._recorder)

        def teardown(self) -> None:
            deactivate(self._tokens)
            if self._recorder.steps:
                found.append(
                    self._recorder.to_trace(f"h-{len(found) + 1:03d}", "hypothesis")
                )

        @invariant()
        def ids_stay_dense_and_inside_the_bound(self) -> None:
            tasks = self.service.list_tasks()
            assert len(tasks) <= bound
            ids = sorted(task.id for task in tasks)
            assert ids == list(range(1, len(ids) + 1))

        @rule(data=st.data())
        def step(self, data: st.DataObject) -> None:
            tasks = list(self.service.list_tasks())
            choices = ["read"]
            if len(tasks) < bound:
                choices.append("add")
            if tasks:
                choices.extend(("move", "illegal"))
            kind = data.draw(st.sampled_from(choices), label="kind")
            if kind == "add":
                self.service.add_task(data.draw(st.sampled_from(_TITLES), label="title"))
                return
            if kind == "read":
                self.service.list_tasks()
                if not tasks:
                    return
                task_id = data.draw(st.sampled_from([task.id for task in tasks]), label="read")
                try:
                    self.service.get_task(task_id)
                except TaskNotFound:
                    return
                return
            task_id = data.draw(st.sampled_from([task.id for task in tasks]), label="task")
            task = self.service.get_task(task_id)
            if kind == "move":
                legal = _LEGAL[task.status.value]
                if task.title == "" and task.status is Status.DRAFT:
                    legal = tuple(name for name in legal if name != "submit")
                action = data.draw(st.sampled_from(legal), label="action")
            else:
                action = data.draw(st.sampled_from(_ALL), label="action")
            self._call(action, task.id)

        def _call(self, action: str, task_id: int) -> None:
            try:
                if action == "submit":
                    self.service.submit(task_id)
                elif action == "delete":
                    self.service.delete(task_id)
                elif action == "start":
                    self.service.start(task_id)
                elif action == "complete":
                    self.service.complete(task_id)
                elif action == "snooze":
                    self.service.snooze(task_id, WAKE)
                elif action == "resume":
                    self.service.resume(task_id)
                elif action == "reopen":
                    self.service.reopen(task_id)
                elif action == "archive":
                    self.service.archive(task_id)
                elif action == "unarchive":
                    self.service.unarchive(task_id)
                elif action == "restore":
                    self.service.restore(task_id)
                else:
                    raise AssertionError(action)
            except (TaskRejected, TaskNotFound):
                return

    run_state_machine_as_test(
        TaskMachine,
        settings=settings(
            max_examples=_EXAMPLES,
            stateful_step_count=_STEPS,
            derandomize=True,
            database=None,
            deadline=None,
            suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
        ),
    )
    return tuple(found)
