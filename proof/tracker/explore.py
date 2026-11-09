"""Hypothesis state machine over the fake, bounded by the model's constants.

One rule reads the repository through the port, lists the commands the
spec would enable in that state, and draws one. Another draws any command
with any small argument, so refusals stay covered. Each example becomes one
trace of commits and refusals.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test

from proof.tracker.recording import Recorder, RecordingServices, Trace, recording_stack
from proof.tracker.spec import max_issues, model_keys, model_users
from tracker.bootstrap import in_memory
from tracker.domain.errors import TrackerError
from tracker.domain.model import ACTIVE, CLOSED, OPEN, Project, Status

_EXAMPLES = 40
_STEPS = 60
_KEYS = ("ENG", "OPS")
_TITLES = ("", "text")

Command = tuple[str, tuple[Any, ...]]


def enabled_commands(projects: tuple[Project, ...], keys: tuple[str, ...], users: tuple[str, ...], bound: int) -> list[Command]:
    """Commands the spec enables in this state, deepest workflow steps first.

    Hypothesis draws low indices more often, so the order is the bias: finish
    reviews before opening new issues or projects.
    """

    deep: list[Command] = []
    wide: list[Command] = []
    for project in projects:
        for issue in project.issues:
            ref = str(issue.ref)
            status = issue.status
            if status is Status.IN_REVIEW:
                if all(project.issue(b).status in CLOSED for b in issue.blocked_by):
                    deep.append(("approve", (ref,)))
                deep.append(("request_changes", (ref,)))
            if status is Status.IN_PROGRESS:
                deep.append(("submit_review", (ref,)))
            if status is Status.TODO and issue.assignee is not None:
                deep.append(("start", (ref,)))
            if status is Status.BACKLOG:
                deep.append(("triage", (ref,)))
            if status in OPEN and issue.assignee is None:
                deep.append(("assign", (ref, users[0])))
            if status in CLOSED:
                deep.append(("reopen", (ref,)))
            for blocker in sorted(issue.blocked_by):
                wide.append(("remove_blocker", (ref, f"{project.key}-{blocker}")))
            if issue.assignee is not None and status not in ACTIVE:
                wide.append(("unassign", (ref,)))
            if status in OPEN:
                for user in users:
                    if issue.assignee is not None and issue.assignee != user:
                        wide.append(("assign", (ref, user)))
            if status is not Status.CANCELED and len(issue.comments) < 2:
                wide.append(("comment", (ref, "note")))
            if status in OPEN:
                for other in project.issues:
                    if other.number != issue.number and other.number not in issue.blocked_by:
                        if issue.number not in project.transitive_blockers(other.number):
                            wide.append(("add_blocker", (ref, str(other.ref))))
                wide.append(("cancel", (ref,)))
    used = {project.key for project in projects}
    for project in projects:
        if len(project.issues) < bound:
            wide.append(("create_issue", (project.key, "text")))
    for key in keys:
        if key not in used:
            wide.append(("create_project", (key, "Project")))
    return deep + wide


def hypothesis_traces() -> tuple[Trace, ...]:
    found: list[Trace] = []
    bound = max_issues()
    keys = tuple(sorted(set(model_keys()) | set(_KEYS)))
    users = model_users()
    refs = tuple(f"{key}-{n}" for key in keys for n in range(1, bound + 2))
    stray: list[Command] = [("create_project", (key, "Project")) for key in keys]
    stray += [("create_issue", (key, title)) for key in keys for title in _TITLES]
    for ref in refs:
        stray += [
            (name, (ref,))
            for name in ("triage", "unassign", "start", "submit_review", "request_changes", "approve", "cancel", "reopen")
        ]
        stray += [("assign", (ref, user)) for user in users]
        stray.append(("comment", (ref, "note")))
        key = ref.split("-")[0]
        stray += [(name, (ref, other)) for other in refs if other.startswith(key) for name in ("add_blocker", "remove_blocker")]

    class TrackerMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.services: RecordingServices
            self.recorder: Recorder
            self.services, self.recorder = recording_stack(in_memory())

        def teardown(self) -> None:
            if self.recorder.steps:
                found.append(self.recorder.to_trace(f"h-{len(found) + 1:03d}", "hypothesis"))

        def _run(self, command: Command) -> None:
            name, args = command
            call: Callable[..., Any] = getattr(self.services, name)
            try:
                call(*args)
            except TrackerError:
                pass

        def _projects(self) -> tuple[Project, ...]:
            with self.services.uow_factory() as uow:
                return uow.projects.list()

        @rule(data=st.data())
        def enabled(self, data: st.DataObject) -> None:
            options = enabled_commands(self._projects(), keys, users, bound)
            if options:
                self._run(data.draw(st.sampled_from(options)))

        @rule(data=st.data())
        def enabled_again(self, data: st.DataObject) -> None:
            self.enabled(data)

        @rule(command=st.sampled_from(stray))
        def any_command(self, command: Command) -> None:
            self._run(command)

    TrackerMachine.TestCase.settings = settings(
        max_examples=_EXAMPLES,
        stateful_step_count=_STEPS,
        derandomize=True,
        deadline=None,
        suppress_health_check=list(HealthCheck),
    )
    run_state_machine_as_test(TrackerMachine, settings=TrackerMachine.TestCase.settings)
    return tuple(found)
