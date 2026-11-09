"""The repository and unit-of-work contract, run against the fake and SQLite.

Both adapters must give the service layer the same behaviour: full round
trips of every field, rollback on a refused command, no leaks between units
of work. SQLite also checks the optimistic version.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from tracker.bootstrap import UnitOfWorkFactory, in_memory, sqlite
from tracker.domain.errors import Rejected
from tracker.domain.model import Project, Status
from tracker.service_layer import services
from tracker.service_layer.errors import ConcurrencyConflict


class UnitOfWorkContract:
    """Mixed into one TestCase per adapter."""

    def factory(self) -> UnitOfWorkFactory:
        raise NotImplementedError

    def test_every_field_survives_a_round_trip(self) -> None:
        uow = self.factory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        services.create_issue("ENG", "Second", uow())
        services.create_issue("ENG", "Third", uow())
        services.assign("ENG-1", "alice", uow())
        services.triage("ENG-1", uow())
        services.start("ENG-1", uow())
        services.add_blocker("ENG-1", "ENG-3", uow())
        services.add_blocker("ENG-1", "ENG-2", uow())
        services.comment("ENG-1", "first note", uow())
        services.comment("ENG-1", "second note", uow())
        services.cancel("ENG-2", uow())
        services.triage("ENG-3", uow())
        services.assign("ENG-3", "bob", uow())
        services.start("ENG-3", uow())
        services.submit_review("ENG-3", uow())
        services.approve("ENG-3", uow())

        one = services.get_issue("ENG-1", uow())
        self.assertEqual((one.key, one.number, one.title), ("ENG", 1, "First"))  # type: ignore[attr-defined]
        self.assertIs(one.status, Status.IN_PROGRESS)  # type: ignore[attr-defined]
        self.assertEqual(one.assignee, "alice")  # type: ignore[attr-defined]
        self.assertEqual(one.blocked_by, frozenset({2, 3}))  # type: ignore[attr-defined]
        self.assertEqual([item.body for item in one.comments], ["first note", "second note"])  # type: ignore[attr-defined]
        self.assertIs(one.comments[0].created_at.tzinfo, timezone.utc)  # type: ignore[attr-defined]
        self.assertIs(one.started_at.tzinfo, timezone.utc)  # type: ignore[attr-defined]
        self.assertIsNone(one.completed_at)  # type: ignore[attr-defined]
        self.assertIsNone(one.canceled_at)  # type: ignore[attr-defined]
        two = services.get_issue("ENG-2", uow())
        self.assertIs(two.status, Status.CANCELED)  # type: ignore[attr-defined]
        self.assertIsNotNone(two.canceled_at)  # type: ignore[attr-defined]
        self.assertIsNone(two.assignee)  # type: ignore[attr-defined]
        self.assertEqual(two.blocked_by, frozenset())  # type: ignore[attr-defined]
        self.assertEqual(two.comments, ())  # type: ignore[attr-defined]
        three = services.get_issue("ENG-3", uow())
        self.assertIs(three.status, Status.DONE)  # type: ignore[attr-defined]
        self.assertIsNotNone(three.completed_at)  # type: ignore[attr-defined]
        self.assertEqual(three.completed_at.tzinfo, timezone.utc)  # type: ignore[attr-defined]
        project = services.get_project("ENG", uow())
        self.assertEqual([str(issue.ref) for issue in project.issues], ["ENG-1", "ENG-2", "ENG-3"])  # type: ignore[attr-defined]
        self.assertEqual(project.next_number, 4)  # type: ignore[attr-defined]

    def test_refused_commands_change_nothing(self) -> None:
        uow = self.factory()
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "First", uow())
        before = services.get_issue("ENG-1", uow())
        with self.assertRaises(Rejected):  # type: ignore[attr-defined]
            services.start("ENG-1", uow())
        after = services.get_issue("ENG-1", uow())
        self.assertIs(after.status, before.status)  # type: ignore[attr-defined]
        self.assertEqual(after.started_at, before.started_at)  # type: ignore[attr-defined]

    def test_uncommitted_changes_are_rolled_back(self) -> None:
        factory = self.factory()
        services.create_project("ENG", "Engineering", factory())
        with factory() as uow:
            project = uow.projects.get("ENG")
            assert project is not None
            project.create_issue("Lost")
            project.name = "Renamed"
        self.assertEqual(services.list_issues("ENG", factory()), ())
        self.assertEqual(services.get_project("ENG", factory()).name, "Engineering")  # type: ignore[attr-defined]
        with factory() as uow:
            uow.projects.add(Project("OPS", "Operations"))
            with self.assertRaises(ValueError):  # type: ignore[attr-defined]
                uow.projects.add(Project("OPS", "Again"))
        self.assertEqual([project.key for project in services.list_projects(factory())], ["ENG"])  # type: ignore[attr-defined]

    def test_an_exception_inside_the_unit_of_work_rolls_back(self) -> None:
        factory = self.factory()
        services.create_project("ENG", "Engineering", factory())

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):  # type: ignore[attr-defined]
            with factory() as uow:
                project = uow.projects.get("ENG")
                assert project is not None
                project.create_issue("Lost")
                raise Boom()
        self.assertEqual(services.list_issues("ENG", factory()), ())

    def test_get_inside_one_unit_of_work_returns_the_same_object(self) -> None:
        factory = self.factory()
        services.create_project("ENG", "Engineering", factory())
        with factory() as uow:
            self.assertIs(uow.projects.get("ENG"), uow.projects.get("ENG"))  # type: ignore[attr-defined]
            self.assertIsNone(uow.projects.get("OPS"))  # type: ignore[attr-defined]
            self.assertEqual(uow.projects.list(), (uow.projects.get("ENG"),))  # type: ignore[attr-defined]

    def test_commit_outside_the_unit_of_work_is_an_error(self) -> None:
        uow = self.factory()()
        with self.assertRaises(RuntimeError):  # type: ignore[attr-defined]
            uow.commit()
        uow.rollback()
        self.assertFalse(uow.committed)  # type: ignore[attr-defined]


class FakeUnitOfWorkTests(UnitOfWorkContract, unittest.TestCase):
    def factory(self) -> UnitOfWorkFactory:
        return in_memory()


class SqliteUnitOfWorkTests(UnitOfWorkContract, unittest.TestCase):
    def factory(self) -> UnitOfWorkFactory:
        return sqlite()

    def test_a_file_database_persists_across_factories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{Path(directory) / 'tracker.db'}"
            services.create_project("ENG", "Engineering", sqlite(url)())
            services.create_issue("ENG", "First", sqlite(url)())
            again = sqlite(url)
            self.assertEqual(str(services.get_issue("ENG-1", again()).ref), "ENG-1")

    def test_each_commit_bumps_the_project_version(self) -> None:
        uow = sqlite()
        created = services.create_project("ENG", "Engineering", uow())
        self.assertEqual(created.version, 1)
        self.assertEqual(services.get_project("ENG", uow()).version, 1)
        services.create_issue("ENG", "First", uow())
        self.assertEqual(services.get_project("ENG", uow()).version, 2)
        with self.assertRaises(Rejected):
            services.start("ENG-1", uow())
        self.assertEqual(services.get_project("ENG", uow()).version, 2)

    def test_a_stale_unit_of_work_cannot_commit(self) -> None:
        factory = sqlite()
        services.create_project("ENG", "Engineering", factory())
        services.create_issue("ENG", "First", factory())
        first = factory()
        second = factory()
        with first, second:
            mine = first.projects.get("ENG")
            theirs = second.projects.get("ENG")
            assert mine is not None and theirs is not None
            mine.assign(1, "alice")
            theirs.assign(1, "bob")
            first.commit()
            with self.assertRaises(ConcurrencyConflict) as caught:
                second.commit()
            self.assertEqual(caught.exception.key, "ENG")
            self.assertEqual(str(caught.exception), "project ENG was changed by another unit of work")
        self.assertEqual(services.get_issue("ENG-1", factory()).assignee, "alice")
        self.assertEqual(services.get_project("ENG", factory()).version, 3)
