"""Service-layer tests for the tracker, against the fake unit of work.

High gear: state is set up through service functions, never by building
domain objects. Every rule of the aggregate is exercised through a use case.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tracker.bootstrap import in_memory
from tracker.domain.errors import InvalidAssignee, InvalidTitle, IssueNotFound, Rejected
from tracker.domain.model import Status
from tracker.service_layer import services
from tracker.service_layer.errors import InvalidRef, ProjectExists, ProjectNotFound


ACTIONS = (
    "triage",
    "start",
    "submit_review",
    "request_changes",
    "approve",
    "cancel",
    "reopen",
    "assign",
    "unassign",
    "comment",
    "add_blocker",
)

# Allowed actions by status for an issue assigned to alice with a second
# issue available as a blocker.
ALLOWED = {
    Status.BACKLOG: {"triage", "cancel", "assign", "unassign", "comment", "add_blocker"},
    Status.TODO: {"start", "cancel", "assign", "unassign", "comment", "add_blocker"},
    Status.IN_PROGRESS: {"submit_review", "cancel", "assign", "comment", "add_blocker"},
    Status.IN_REVIEW: {"request_changes", "approve", "cancel", "assign", "comment", "add_blocker"},
    Status.DONE: {"reopen", "unassign", "comment"},
    Status.CANCELED: {"reopen", "unassign"},
}


def _call(action: str, ref: str, uow) -> None:
    if action == "triage":
        services.triage(ref, uow())
    elif action == "start":
        services.start(ref, uow())
    elif action == "submit_review":
        services.submit_review(ref, uow())
    elif action == "request_changes":
        services.request_changes(ref, uow())
    elif action == "approve":
        services.approve(ref, uow())
    elif action == "cancel":
        services.cancel(ref, uow())
    elif action == "reopen":
        services.reopen(ref, uow())
    elif action == "assign":
        services.assign(ref, "bob", uow())
    elif action == "unassign":
        services.unassign(ref, uow())
    elif action == "comment":
        services.comment(ref, "note", uow())
    elif action == "add_blocker":
        services.add_blocker(ref, "ENG-2", uow())
    else:
        raise AssertionError(action)


def _reach(status: Status):
    """A project ENG with ENG-1 assigned to alice in ``status`` and ENG-2 in backlog."""

    uow = in_memory()
    services.create_project("ENG", "Engineering", uow())
    services.create_issue("ENG", "First", uow())
    services.create_issue("ENG", "Second", uow())
    services.assign("ENG-1", "alice", uow())
    if status is Status.BACKLOG:
        return uow
    services.triage("ENG-1", uow())
    if status is Status.TODO:
        return uow
    if status is Status.CANCELED:
        services.cancel("ENG-1", uow())
        return uow
    services.start("ENG-1", uow())
    if status is Status.IN_PROGRESS:
        return uow
    services.submit_review("ENG-1", uow())
    if status is Status.IN_REVIEW:
        return uow
    services.approve("ENG-1", uow())
    assert status is Status.DONE
    return uow


def _snapshot(uow) -> tuple:
    rows = []
    for project in services.list_projects(uow()):
        for issue in project.issues:
            rows.append(
                (
                    str(issue.ref),
                    issue.title,
                    issue.status,
                    issue.assignee,
                    issue.blocked_by,
                    len(issue.comments),
                    issue.started_at,
                    issue.completed_at,
                    issue.canceled_at,
                )
            )
        rows.append((project.key, project.name, project.version))
    return tuple(rows)


class ProjectTests(unittest.TestCase):
    def test_create_project_stores_it(self) -> None:
        uow = in_memory()
        created = services.create_project("ENG", "Engineering", uow())
        self.assertEqual(created.key, "ENG")
        self.assertEqual(created.name, "Engineering")
        self.assertEqual(created.version, 0)
        self.assertEqual(created.issues, ())
        self.assertEqual(created.next_number, 1)
        stored = services.get_project("ENG", uow())
        self.assertEqual(stored.key, "ENG")
        self.assertEqual(stored.name, "Engineering")
        self.assertEqual(repr(stored), "Project('ENG', issues=0)")

    def test_second_project_with_the_same_key_is_refused(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        with self.assertRaises(ProjectExists) as caught:
            services.create_project("ENG", "Again", uow())
        self.assertEqual(caught.exception.key, "ENG")
        self.assertEqual(str(caught.exception), "project ENG already exists")
        self.assertEqual(services.get_project("ENG", uow()).name, "Engineering")

    def test_project_keys_are_short_upper_case_identifiers(self) -> None:
        uow = in_memory()
        for key in ("E", "eng", "1ENG", "ENG-1", "A1234567890", "", None, 3):
            with self.subTest(key=key):
                with self.assertRaises(InvalidRef) as caught:
                    services.create_project(key, "x", uow())  # type: ignore[arg-type]
                self.assertEqual(str(caught.exception), f"{key!r} is not a project key like ENG")
        for key in ("EN", "ENG", "A123456789", "OPS2"):
            with self.subTest(key=key):
                self.assertEqual(services.create_project(key, "x", uow()).key, key)
        self.assertEqual(
            [project.key for project in services.list_projects(uow())],
            ["A123456789", "EN", "ENG", "OPS2"],
        )

    def test_project_name_must_be_a_string(self) -> None:
        uow = in_memory()
        with self.assertRaises(TypeError) as caught:
            services.create_project("ENG", 7, uow())  # type: ignore[arg-type]
        self.assertEqual(str(caught.exception), "name must be a string")
        self.assertEqual(services.list_projects(uow()), ())

    def test_missing_project_is_reported(self) -> None:
        uow = in_memory()
        with self.assertRaises(ProjectNotFound) as caught:
            services.get_project("ENG", uow())
        self.assertEqual(caught.exception.key, "ENG")
        self.assertEqual(str(caught.exception), "project ENG not found")
        with self.assertRaises(ProjectNotFound):
            services.create_issue("ENG", "x", uow())
        with self.assertRaises(ProjectNotFound):
            services.list_issues("ENG", uow())
        with self.assertRaises(ProjectNotFound):
            services.get_issue("ENG-1", uow())
        with self.assertRaises(ProjectNotFound):
            services.triage("ENG-1", uow())


class IssueCreationTests(unittest.TestCase):
    def test_issues_are_numbered_from_one_in_order(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_project("OPS", "Operations", uow())
        first = services.create_issue("ENG", "First", uow())
        second = services.create_issue("ENG", "Second", uow())
        other = services.create_issue("OPS", "Other", uow())
        self.assertEqual((str(first.ref), str(second.ref), str(other.ref)), ("ENG-1", "ENG-2", "OPS-1"))
        self.assertEqual(first.ref.key, "ENG")
        self.assertEqual(first.ref.number, 1)
        self.assertEqual(services.get_project("ENG", uow()).next_number, 3)
        self.assertEqual([str(issue.ref) for issue in services.list_issues("ENG", uow())], ["ENG-1", "ENG-2"])
        self.assertEqual(repr(first), "Issue('ENG-1', status='backlog', title='First')")

    def test_a_new_issue_is_an_unassigned_backlog_item(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        issue = services.get_issue("ENG-1", uow())
        self.assertEqual(issue.key, "ENG")
        self.assertEqual(issue.number, 1)
        self.assertEqual(issue.title, "First")
        self.assertIs(issue.status, Status.BACKLOG)
        self.assertIsNone(issue.assignee)
        self.assertEqual(issue.blocked_by, frozenset())
        self.assertEqual(issue.comments, ())
        self.assertIsNone(issue.started_at)
        self.assertIsNone(issue.completed_at)
        self.assertIsNone(issue.canceled_at)

    def test_empty_title_is_refused(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        with self.assertRaises(InvalidTitle) as caught:
            services.create_issue("ENG", "", uow())
        self.assertEqual(caught.exception.key, "ENG")
        self.assertEqual(str(caught.exception), "issue title in ENG must not be empty")
        self.assertEqual(services.list_issues("ENG", uow()), ())
        with self.assertRaises(TypeError) as typed:
            services.create_issue("ENG", None, uow())  # type: ignore[arg-type]
        self.assertEqual(str(typed.exception), "title must be a string")
        with self.assertRaises(InvalidRef) as key:
            services.create_issue("eng", "x", uow())
        self.assertEqual(str(key.exception), "'eng' is not a project key like ENG")
        self.assertEqual(key.exception.detail, "'eng' is not a project key like ENG")

    def test_refs_are_key_dash_number(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        for ref in ("ENG", "ENG-0", "ENG-01", "eng-1", "ENG_1", "ENG-1-2", "", "E-1"):
            with self.subTest(ref=ref):
                with self.assertRaises(InvalidRef) as caught:
                    services.get_issue(ref, uow())
                self.assertEqual(str(caught.exception), f"{ref!r} is not an issue ref like ENG-1")
        with self.assertRaises(InvalidRef) as typed:
            services.get_issue(1, uow())  # type: ignore[arg-type]
        self.assertEqual(str(typed.exception), "issue ref must be a string")
        with self.assertRaises(IssueNotFound) as missing:
            services.get_issue("ENG-10", uow())
        self.assertEqual(missing.exception.ref, "ENG-10")
        self.assertEqual(str(missing.exception), "issue ENG-10 not found")
        self.assertEqual(str(services.get_issue("ENG-1", uow()).ref), "ENG-1")


class StatusTableTests(unittest.TestCase):
    def test_each_status_allows_exactly_its_actions(self) -> None:
        for status, allowed in ALLOWED.items():
            for action in ACTIONS:
                with self.subTest(status=status.value, action=action):
                    uow = _reach(status)
                    before = _snapshot(uow)
                    if action in allowed:
                        _call(action, "ENG-1", uow)
                        self.assertNotEqual(_snapshot(uow), before)
                    else:
                        with self.assertRaises(Rejected) as caught:
                            _call(action, "ENG-1", uow)
                        self.assertEqual(caught.exception.ref, "ENG-1")
                        self.assertEqual(caught.exception.status, status.value)
                        self.assertEqual(caught.exception.action, action)
                        self.assertEqual(
                            str(caught.exception),
                            f"{action} rejected for ENG-1 in {status.value}",
                        )
                        self.assertEqual(_snapshot(uow), before)

    def test_actions_on_a_missing_issue_change_nothing(self) -> None:
        uow = _reach(Status.BACKLOG)
        before = _snapshot(uow)
        for action in ACTIONS:
            with self.subTest(action=action):
                with self.assertRaises(IssueNotFound):
                    _call(action, "ENG-9", uow)
                self.assertEqual(_snapshot(uow), before)


class WorkflowTests(unittest.TestCase):
    def test_review_loop_stamps_and_clears_times(self) -> None:
        uow = _reach(Status.TODO)
        started = services.start("ENG-1", uow())
        self.assertIs(started.status, Status.IN_PROGRESS)
        self.assertIsInstance(started.started_at, datetime)
        self.assertIs(started.started_at.tzinfo, timezone.utc)
        self.assertIsNone(started.completed_at)
        reviewing = services.submit_review("ENG-1", uow())
        self.assertIs(reviewing.status, Status.IN_REVIEW)
        back = services.request_changes("ENG-1", uow())
        self.assertIs(back.status, Status.IN_PROGRESS)
        self.assertEqual(back.started_at, started.started_at)
        services.submit_review("ENG-1", uow())
        done = services.approve("ENG-1", uow())
        self.assertIs(done.status, Status.DONE)
        self.assertIsInstance(done.completed_at, datetime)
        self.assertIsNone(done.canceled_at)
        reopened = services.reopen("ENG-1", uow())
        self.assertIs(reopened.status, Status.TODO)
        self.assertIsNone(reopened.completed_at)
        self.assertIsNone(reopened.canceled_at)
        self.assertEqual(reopened.started_at, started.started_at)
        self.assertEqual(reopened.assignee, "alice")
        stored = services.get_issue("ENG-1", uow())
        self.assertIs(stored.status, Status.TODO)
        self.assertEqual(stored.started_at, started.started_at)

    def test_cancel_and_reopen(self) -> None:
        uow = _reach(Status.IN_PROGRESS)
        canceled = services.cancel("ENG-1", uow())
        self.assertIs(canceled.status, Status.CANCELED)
        self.assertIsInstance(canceled.canceled_at, datetime)
        self.assertIsNone(canceled.completed_at)
        self.assertEqual(canceled.assignee, "alice")
        reopened = services.reopen("ENG-1", uow())
        self.assertIs(reopened.status, Status.TODO)
        self.assertIsNone(reopened.canceled_at)
        self.assertEqual(services.get_issue("ENG-1", uow()).status, Status.TODO)

    def test_triage_moves_backlog_to_todo(self) -> None:
        uow = _reach(Status.BACKLOG)
        self.assertIs(services.triage("ENG-1", uow()).status, Status.TODO)
        self.assertIs(services.get_issue("ENG-1", uow()).status, Status.TODO)

    def test_start_needs_an_assignee(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        services.triage("ENG-1", uow())
        with self.assertRaises(Rejected) as caught:
            services.start("ENG-1", uow())
        self.assertEqual(caught.exception.action, "start")
        self.assertIs(services.get_issue("ENG-1", uow()).status, Status.TODO)
        self.assertIsNone(services.get_issue("ENG-1", uow()).started_at)
        services.assign("ENG-1", "alice", uow())
        self.assertIs(services.start("ENG-1", uow()).status, Status.IN_PROGRESS)

    def test_list_issues_filters_by_status(self) -> None:
        uow = _reach(Status.IN_PROGRESS)
        services.create_issue("ENG", "Third", uow())
        self.assertEqual(
            [str(issue.ref) for issue in services.list_issues("ENG", uow())],
            ["ENG-1", "ENG-2", "ENG-3"],
        )
        self.assertEqual(
            [str(issue.ref) for issue in services.list_issues("ENG", uow(), Status.BACKLOG)],
            ["ENG-2", "ENG-3"],
        )
        self.assertEqual(
            [str(issue.ref) for issue in services.list_issues("ENG", uow(), Status.IN_PROGRESS)],
            ["ENG-1"],
        )
        self.assertEqual(services.list_issues("ENG", uow(), Status.DONE), ())
        with self.assertRaises(TypeError) as caught:
            services.list_issues("ENG", uow(), "done")  # type: ignore[arg-type]
        self.assertEqual(str(caught.exception), "status must be a Status or None")


class AssignmentTests(unittest.TestCase):
    def test_assign_and_unassign(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        self.assertEqual(services.assign("ENG-1", "alice", uow()).assignee, "alice")
        self.assertEqual(services.get_issue("ENG-1", uow()).assignee, "alice")
        self.assertEqual(services.assign("ENG-1", "bob", uow()).assignee, "bob")
        self.assertIsNone(services.unassign("ENG-1", uow()).assignee)
        self.assertIsNone(services.get_issue("ENG-1", uow()).assignee)

    def test_assigning_the_same_user_again_is_refused(self) -> None:
        uow = _reach(Status.TODO)
        with self.assertRaises(Rejected) as caught:
            services.assign("ENG-1", "alice", uow())
        self.assertEqual(caught.exception.action, "assign")
        self.assertEqual(services.get_issue("ENG-1", uow()).assignee, "alice")

    def test_unassigning_an_unassigned_issue_is_refused(self) -> None:
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        with self.assertRaises(Rejected) as caught:
            services.unassign("ENG-1", uow())
        self.assertEqual(caught.exception.action, "unassign")
        self.assertEqual(caught.exception.status, "backlog")

    def test_assignee_must_be_a_non_empty_string(self) -> None:
        uow = _reach(Status.TODO)
        for user in ("", None, 4):
            with self.subTest(user=user):
                with self.assertRaises(InvalidAssignee) as caught:
                    services.assign("ENG-1", user, uow())  # type: ignore[arg-type]
                self.assertEqual(caught.exception.ref, "ENG-1")
                self.assertEqual(str(caught.exception), "assignee for ENG-1 must not be empty")
        self.assertEqual(services.get_issue("ENG-1", uow()).assignee, "alice")


class BlockerTests(unittest.TestCase):
    def _three(self):
        uow = in_memory()
        services.create_project("ENG", "Engineering", uow())
        services.create_project("OPS", "Operations", uow())
        for title in ("One", "Two", "Three"):
            services.create_issue("ENG", title, uow())
        services.create_issue("OPS", "Other", uow())
        return uow

    def test_add_and_remove_blockers(self) -> None:
        uow = self._three()
        self.assertEqual(services.add_blocker("ENG-1", "ENG-2", uow()).blocked_by, frozenset({2}))
        self.assertEqual(services.add_blocker("ENG-1", "ENG-3", uow()).blocked_by, frozenset({2, 3}))
        self.assertEqual(services.get_issue("ENG-1", uow()).blocked_by, frozenset({2, 3}))
        self.assertEqual(services.get_issue("ENG-2", uow()).blocked_by, frozenset())
        self.assertEqual(services.remove_blocker("ENG-1", "ENG-2", uow()).blocked_by, frozenset({3}))
        self.assertEqual(services.get_issue("ENG-1", uow()).blocked_by, frozenset({3}))

    def test_duplicate_self_missing_and_foreign_blockers_are_refused(self) -> None:
        uow = self._three()
        services.add_blocker("ENG-1", "ENG-2", uow())
        with self.assertRaises(Rejected) as duplicate:
            services.add_blocker("ENG-1", "ENG-2", uow())
        self.assertEqual(duplicate.exception.action, "add_blocker")
        with self.assertRaises(Rejected) as itself:
            services.add_blocker("ENG-1", "ENG-1", uow())
        self.assertEqual(itself.exception.action, "add_blocker")
        with self.assertRaises(IssueNotFound) as missing:
            services.add_blocker("ENG-1", "ENG-9", uow())
        self.assertEqual(missing.exception.ref, "ENG-9")
        with self.assertRaises(InvalidRef) as foreign:
            services.add_blocker("ENG-1", "OPS-1", uow())
        self.assertEqual(str(foreign.exception), "OPS-1 is not in project ENG")
        with self.assertRaises(InvalidRef):
            services.remove_blocker("ENG-1", "OPS-1", uow())
        with self.assertRaises(Rejected) as absent:
            services.remove_blocker("ENG-1", "ENG-3", uow())
        self.assertEqual(absent.exception.action, "remove_blocker")
        with self.assertRaises(Rejected) as unblocked:
            services.remove_blocker("ENG-2", "ENG-1", uow())
        self.assertEqual(unblocked.exception.ref, "ENG-2")
        self.assertEqual(services.get_issue("ENG-1", uow()).blocked_by, frozenset({2}))

    def test_cycles_are_refused(self) -> None:
        uow = self._three()
        services.add_blocker("ENG-1", "ENG-2", uow())
        with self.assertRaises(Rejected) as direct:
            services.add_blocker("ENG-2", "ENG-1", uow())
        self.assertEqual(direct.exception.ref, "ENG-2")
        services.add_blocker("ENG-2", "ENG-3", uow())
        with self.assertRaises(Rejected) as transitive:
            services.add_blocker("ENG-3", "ENG-1", uow())
        self.assertEqual(transitive.exception.action, "add_blocker")
        self.assertEqual(services.get_issue("ENG-3", uow()).blocked_by, frozenset())
        project = services.get_project("ENG", uow())
        self.assertEqual(project.transitive_blockers(1), frozenset({2, 3}))
        self.assertEqual(project.transitive_blockers(2), frozenset({3}))
        self.assertEqual(project.transitive_blockers(3), frozenset())
        # ENG-3 may still block ENG-1 directly: 1 <- 2 <- 3 and 1 <- 3 is not a cycle.
        self.assertEqual(services.add_blocker("ENG-1", "ENG-3", uow()).blocked_by, frozenset({2, 3}))

    def test_approval_waits_for_blockers(self) -> None:
        uow = self._three()
        services.add_blocker("ENG-1", "ENG-2", uow())
        services.add_blocker("ENG-1", "ENG-3", uow())
        services.triage("ENG-1", uow())
        services.assign("ENG-1", "alice", uow())
        services.start("ENG-1", uow())
        services.submit_review("ENG-1", uow())
        with self.assertRaises(Rejected) as blocked:
            services.approve("ENG-1", uow())
        self.assertEqual(blocked.exception.action, "approve")
        self.assertIs(services.get_issue("ENG-1", uow()).status, Status.IN_REVIEW)
        services.cancel("ENG-2", uow())
        with self.assertRaises(Rejected):
            services.approve("ENG-1", uow())
        services.triage("ENG-3", uow())
        services.assign("ENG-3", "bob", uow())
        services.start("ENG-3", uow())
        services.submit_review("ENG-3", uow())
        services.approve("ENG-3", uow())
        self.assertIs(services.approve("ENG-1", uow()).status, Status.DONE)

    def test_blockers_cannot_be_added_to_a_closed_issue(self) -> None:
        uow = _reach(Status.DONE)
        with self.assertRaises(Rejected) as caught:
            services.add_blocker("ENG-1", "ENG-2", uow())
        self.assertEqual(caught.exception.status, "done")
        # The blocker side may be closed.
        services.cancel("ENG-2", uow())
        services.reopen("ENG-1", uow())
        self.assertEqual(services.add_blocker("ENG-1", "ENG-2", uow()).blocked_by, frozenset({2}))


class CommentTests(unittest.TestCase):
    def test_comments_accumulate_in_order(self) -> None:
        uow = _reach(Status.BACKLOG)
        first = services.comment("ENG-1", "first", uow())
        self.assertEqual(first.body, "first")
        self.assertIsInstance(first.created_at, datetime)
        services.comment("ENG-1", "second", uow())
        stored = services.get_issue("ENG-1", uow()).comments
        self.assertEqual([item.body for item in stored], ["first", "second"])
        self.assertEqual(stored[0], first)
        self.assertEqual(services.get_issue("ENG-2", uow()).comments, ())

    def test_comment_body_must_be_a_string(self) -> None:
        uow = _reach(Status.BACKLOG)
        with self.assertRaises(TypeError) as caught:
            services.comment("ENG-1", 3, uow())  # type: ignore[arg-type]
        self.assertEqual(str(caught.exception), "comment body must be a string")
        self.assertEqual(services.get_issue("ENG-1", uow()).comments, ())

    def test_canceled_issues_take_no_comments(self) -> None:
        uow = _reach(Status.CANCELED)
        with self.assertRaises(Rejected) as caught:
            services.comment("ENG-1", "late", uow())
        self.assertEqual(caught.exception.action, "comment")
        self.assertEqual(services.get_issue("ENG-1", uow()).comments, ())


class UnitOfWorkTests(unittest.TestCase):
    def test_changes_without_commit_are_dropped(self) -> None:
        factory = in_memory()
        services.create_project("ENG", "Engineering", factory())
        services.create_issue("ENG", "First", factory())
        with factory() as uow:
            project = uow.projects.get("ENG")
            assert project is not None
            project.triage(1)
            project.create_issue("Second")
        self.assertIs(services.get_issue("ENG-1", factory()).status, Status.BACKLOG)
        self.assertEqual(len(services.list_issues("ENG", factory())), 1)

    def test_commit_outside_the_unit_of_work_is_an_error(self) -> None:
        factory = in_memory()
        uow = factory()
        with self.assertRaises(RuntimeError) as caught:
            uow.commit()
        self.assertEqual(str(caught.exception), "commit outside the unit of work")
        self.assertFalse(uow.committed)
        uow.rollback()

    def test_commit_then_more_changes_without_commit(self) -> None:
        factory = in_memory()
        with factory() as uow:
            uow.projects.add(services.Project("ENG", "Engineering"))
            uow.commit()
            self.assertTrue(uow.committed)
            project = uow.projects.get("ENG")
            assert project is not None
            project.create_issue("Lost")
            uow.rollback()
            self.assertEqual(uow.projects.get("ENG").issues, ())  # type: ignore[union-attr]
        self.assertEqual(services.list_issues("ENG", factory()), ())
        with factory() as uow:
            with self.assertRaises(ValueError) as caught:
                uow.projects.add(services.Project("ENG", "Twice"))
            self.assertEqual(str(caught.exception), "project ENG already exists")

    def test_stored_objects_are_not_the_callers(self) -> None:
        factory = in_memory()
        created = services.create_project("ENG", "Engineering", factory())
        created.name = "Changed"
        self.assertEqual(services.get_project("ENG", factory()).name, "Engineering")
