"""Readable scenarios, each recorded through three stacks.

The same walk runs against the fake unit of work, against SQLite through
the service layer, and against SQLite through the FastAPI app. The traces
differ only in where the call was made from. Refused calls are part of the
walk: a scenario says what the tracker must refuse as much as what it must
do.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from proof.tracker.drivers import direct_stack, http_stack
from proof.tracker.recording import Trace
from tracker.bootstrap import in_memory, sqlite
from tracker.domain.errors import TrackerError


def refused(call: Callable[..., Any], *args: Any) -> None:
    """Call a command the tracker must refuse."""

    try:
        call(*args)
    except TrackerError:
        return
    raise AssertionError(f"{call.__name__}{args!r} was not refused")


def scenario_1(s: Any) -> None:
    """A full review loop with a blocker that holds approval back."""

    s.create_project("ENG", "Engineering")
    s.create_issue("ENG", "Design the port")
    s.create_issue("ENG", "Write the spec")
    s.triage("ENG-1")
    refused(s.start, "ENG-1")
    s.assign("ENG-1", "alice")
    s.start("ENG-1")
    s.add_blocker("ENG-1", "ENG-2")
    refused(s.add_blocker, "ENG-2", "ENG-1")
    s.comment("ENG-1", "waiting on the spec")
    s.submit_review("ENG-1")
    refused(s.approve, "ENG-1")
    s.request_changes("ENG-1")
    s.submit_review("ENG-1")
    s.triage("ENG-2")
    s.assign("ENG-2", "bob")
    s.start("ENG-2")
    s.submit_review("ENG-2")
    s.approve("ENG-2")
    s.approve("ENG-1")
    refused(s.unassign, "ENG-9")
    s.unassign("ENG-1")
    s.reopen("ENG-1")
    s.remove_blocker("ENG-1", "ENG-2")


def scenario_2(s: Any) -> None:
    """Cancel from every open status, reopen from both closed ones."""

    s.create_project("ENG", "Engineering")
    for title in ("One", "Two", "Three"):
        s.create_issue("ENG", title)
    s.cancel("ENG-1")
    refused(s.comment, "ENG-1", "too late")
    refused(s.cancel, "ENG-1")
    s.reopen("ENG-1")
    s.triage("ENG-2")
    s.cancel("ENG-2")
    s.assign("ENG-3", "alice")
    s.triage("ENG-3")
    s.start("ENG-3")
    refused(s.unassign, "ENG-3")
    s.cancel("ENG-3")
    s.unassign("ENG-3")
    s.reopen("ENG-3")
    refused(s.start, "ENG-1")
    s.assign("ENG-1", "bob")
    s.start("ENG-1")
    s.submit_review("ENG-1")
    s.cancel("ENG-1")
    s.reopen("ENG-1")
    s.start("ENG-1")
    s.submit_review("ENG-1")
    s.approve("ENG-1")
    s.reopen("ENG-2")
    refused(s.assign, "ENG-1", "carol")
    s.reopen("ENG-1")


def scenario_3(s: Any) -> None:
    """Two projects number independently; a used key is refused."""

    s.create_project("ENG", "Engineering")
    refused(s.create_project, "ENG", "Again")
    s.create_project("OPS", "Operations")
    s.create_issue("ENG", "Engineering one")
    s.create_issue("OPS", "Operations one")
    s.create_issue("OPS", "Operations two")
    refused(s.create_issue, "OPS", "")
    s.add_blocker("OPS-1", "OPS-2")
    s.assign("OPS-2", "alice")
    refused(s.assign, "OPS-2", "alice")
    s.assign("OPS-2", "bob")
    s.triage("ENG-1")
    refused(s.triage, "ENG-1")
    refused(s.submit_review, "ENG-1")
    refused(s.request_changes, "ENG-1")
    refused(s.remove_blocker, "ENG-1", "ENG-1")
    s.comment("OPS-1", "first")
    s.comment("OPS-1", "second")


def scenario_4(s: Any) -> None:
    """A chain of three blockers; the transitive cycle is refused."""

    s.create_project("ENG", "Engineering")
    for title in ("Top", "Middle", "Bottom"):
        s.create_issue("ENG", title)
    s.add_blocker("ENG-1", "ENG-2")
    s.add_blocker("ENG-2", "ENG-3")
    refused(s.add_blocker, "ENG-3", "ENG-1")
    refused(s.add_blocker, "ENG-1", "ENG-2")
    s.add_blocker("ENG-1", "ENG-3")
    s.triage("ENG-1")
    s.assign("ENG-1", "alice")
    s.start("ENG-1")
    s.submit_review("ENG-1")
    refused(s.approve, "ENG-1")
    s.cancel("ENG-2")
    refused(s.approve, "ENG-1")
    s.cancel("ENG-3")
    s.approve("ENG-1")
    refused(s.add_blocker, "ENG-1", "ENG-2")
    s.remove_blocker("ENG-1", "ENG-2")
    refused(s.remove_blocker, "ENG-1", "ENG-2")
    s.reopen("ENG-3")
    s.reopen("ENG-1")
    s.unassign("ENG-1")


SCENARIOS: tuple[tuple[str, Callable[[Any], None]], ...] = (
    ("scenario-1", scenario_1),
    ("scenario-2", scenario_2),
    ("scenario-3", scenario_3),
    ("scenario-4", scenario_4),
)

STACKS = (
    ("fake", lambda: direct_stack(in_memory())),
    ("sqlite", lambda: direct_stack(sqlite())),
    ("http", lambda: http_stack(sqlite())),
)


def recorded_scenarios(stack: str = "fake") -> tuple[Trace, ...]:
    build = dict(STACKS)[stack]
    traces = []
    for name, walk in SCENARIOS:
        driver, recorder = build()
        walk(driver)
        traces.append(recorder.to_trace(f"{name}-{stack}", f"scenario/{stack}"))
    return tuple(traces)


def all_recorded_scenarios() -> tuple[Trace, ...]:
    found: list[Trace] = []
    for stack, _build in STACKS:
        found.extend(recorded_scenarios(stack))
    return tuple(found)
