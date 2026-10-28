"""Use cases, repository storage, and refused status changes."""

from __future__ import annotations

import pathlib
import unittest
from datetime import datetime, timezone

from todo_app import (
    InMemoryTaskRepository,
    Status,
    Task,
    TaskNotFound,
    TaskRejected,
    in_memory_app,
)

WAKE = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)

_ACTIONS = (
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

_ALLOWED = {
    Status.DRAFT: {"submit", "delete"},
    Status.TODO: {"start", "complete", "snooze", "delete"},
    Status.IN_PROGRESS: {"complete", "snooze"},
    Status.SNOOZED: {"resume"},
    Status.DONE: {"reopen", "archive"},
    Status.ARCHIVED: {"unarchive"},
    Status.DELETED: {"restore"},
}


def _call(service, action: str, task_id: int) -> None:
    if action == "submit":
        service.submit(task_id)
    elif action == "delete":
        service.delete(task_id)
    elif action == "start":
        service.start(task_id)
    elif action == "complete":
        service.complete(task_id)
    elif action == "snooze":
        service.snooze(task_id, WAKE)
    elif action == "resume":
        service.resume(task_id)
    elif action == "reopen":
        service.reopen(task_id)
    elif action == "archive":
        service.archive(task_id)
    elif action == "unarchive":
        service.unarchive(task_id)
    elif action == "restore":
        service.restore(task_id)
    else:
        raise AssertionError(action)


def _reach(status: Status):
    service = in_memory_app()
    task_id = service.add_task("milk").id
    if status is Status.DRAFT:
        return service, task_id
    service.submit(task_id)
    if status is Status.TODO:
        return service, task_id
    if status is Status.IN_PROGRESS:
        service.start(task_id)
    elif status is Status.SNOOZED:
        service.snooze(task_id, WAKE)
    elif status is Status.DONE:
        service.complete(task_id)
    elif status is Status.ARCHIVED:
        service.complete(task_id)
        service.archive(task_id)
    elif status is Status.DELETED:
        service.delete(task_id)
    else:
        raise AssertionError(status)
    return service, task_id


def _view(task: Task) -> tuple:
    return (
        task.id,
        task.status,
        task.title,
        task.listed,
        task.started_at,
        task.completed_at,
        task.wake_at,
        task.deleted_at,
    )


class TaskServiceTests(unittest.TestCase):
    def test_add_get_and_filter(self) -> None:
        service = in_memory_app()
        first = service.add_task("milk")
        second = service.add_task("")
        submitted = service.submit(first.id)

        self.assertEqual(first.id, 1)
        self.assertEqual(second.id, 2)
        self.assertEqual(submitted.status, Status.TODO)
        self.assertTrue(submitted.listed)
        self.assertEqual(service.get_task(first.id).title, "milk")
        self.assertEqual(
            [task.id for task in service.list_tasks()],
            [first.id, second.id],
        )
        self.assertEqual(
            [task.id for task in service.list_tasks(Status.TODO)],
            [first.id],
        )
        self.assertEqual(
            [task.id for task in service.list_tasks(Status.DRAFT)],
            [second.id],
        )
        self.assertEqual(service.list_tasks(Status.DONE), ())

    def test_missing_task(self) -> None:
        service = in_memory_app()
        service.add_task("milk")
        with self.assertRaises(TaskNotFound) as caught:
            service.get_task(4)
        self.assertEqual(caught.exception.task_id, 4)
        self.assertEqual(str(caught.exception), "task 4 not found")

    def test_callers_do_not_share_the_stored_object(self) -> None:
        service = in_memory_app()
        returned = service.add_task("milk")
        returned.submit()
        self.assertEqual(service.get_task(returned.id).status, Status.DRAFT)

    def test_empty_title_stays_a_draft(self) -> None:
        service = in_memory_app()
        task_id = service.add_task("").id
        with self.assertRaises(TaskRejected) as caught:
            service.submit(task_id)
        self.assertEqual(caught.exception.action, "submit")
        self.assertEqual(caught.exception.status, "draft")
        self.assertEqual(str(caught.exception), "submit rejected in draft")
        task = service.get_task(task_id)
        self.assertEqual(task.status, Status.DRAFT)
        self.assertFalse(task.listed)

    def test_restore_of_a_draft_is_an_unlisted_todo(self) -> None:
        service = in_memory_app()
        task_id = service.add_task("milk").id
        service.delete(task_id)
        restored = service.restore(task_id)
        self.assertEqual(restored.status, Status.TODO)
        self.assertFalse(restored.listed)
        self.assertEqual(restored.title, "milk")
        self.assertIsNone(restored.deleted_at)

    def test_resume_without_start_leaves_no_start_time(self) -> None:
        service = in_memory_app()
        task_id = service.add_task("milk").id
        service.submit(task_id)
        service.snooze(task_id, WAKE)
        resumed = service.resume(task_id)
        self.assertEqual(resumed.status, Status.IN_PROGRESS)
        self.assertIsNone(resumed.started_at)
        self.assertIsNone(resumed.wake_at)
        self.assertEqual(service.get_task(task_id).wake_at, None)

    def test_snooze_keeps_the_wake_time(self) -> None:
        service = in_memory_app()
        task_id = service.add_task("milk").id
        service.submit(task_id)
        service.start(task_id)
        snoozed = service.snooze(task_id, WAKE)
        self.assertEqual(snoozed.wake_at, WAKE)
        self.assertIsNotNone(snoozed.started_at)

    def test_reopen_clears_completion_and_archive_hides_the_task(self) -> None:
        service = in_memory_app()
        task_id = service.add_task("milk").id
        service.submit(task_id)
        service.complete(task_id)
        archived = service.archive(task_id)
        self.assertEqual(archived.status, Status.ARCHIVED)
        self.assertFalse(archived.listed)
        self.assertIsNotNone(archived.completed_at)
        service.unarchive(task_id)
        reopened = service.reopen(task_id)
        self.assertEqual(reopened.status, Status.TODO)
        self.assertIsNone(reopened.completed_at)
        self.assertTrue(reopened.listed)

    def test_start_complete_and_delete_stamp_the_current_time(self) -> None:
        service = in_memory_app()
        task_id = service.add_task("milk").id
        service.submit(task_id)
        before_start = datetime.now(timezone.utc)
        service.start(task_id)
        after_start = datetime.now(timezone.utc)
        started = service.get_task(task_id).started_at
        self.assertIsNotNone(started)
        assert started is not None
        self.assertGreaterEqual(started, before_start)
        self.assertLessEqual(started, after_start)
        self.assertIsNotNone(started.tzinfo)

        before_done = datetime.now(timezone.utc)
        service.complete(task_id)
        after_done = datetime.now(timezone.utc)
        completed = service.get_task(task_id).completed_at
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertGreaterEqual(completed, before_done)
        self.assertLessEqual(completed, after_done)

        service.reopen(task_id)
        before_delete = datetime.now(timezone.utc)
        service.delete(task_id)
        after_delete = datetime.now(timezone.utc)
        deleted = service.get_task(task_id).deleted_at
        self.assertIsNotNone(deleted)
        assert deleted is not None
        self.assertGreaterEqual(deleted, before_delete)
        self.assertLessEqual(deleted, after_delete)

    def test_refused_actions_leave_the_task_unchanged(self) -> None:
        for status, allowed in _ALLOWED.items():
            for action in _ACTIONS:
                if action in allowed:
                    continue
                with self.subTest(status=status.value, action=action):
                    service, task_id = _reach(status)
                    before = service.get_task(task_id)
                    with self.assertRaises(TaskRejected) as caught:
                        _call(service, action, task_id)
                    self.assertEqual(caught.exception.task_id, task_id)
                    self.assertEqual(caught.exception.action, action)
                    self.assertEqual(caught.exception.status, before.status.value)
                    self.assertEqual(
                        str(caught.exception),
                        f"{action} rejected in {before.status.value}",
                    )
                    self.assertEqual(_view(service.get_task(task_id)), _view(before))

    def test_bad_arguments(self) -> None:
        service = in_memory_app()
        with self.assertRaises(TypeError) as title:
            service.add_task(1)  # type: ignore[arg-type]
        self.assertEqual(str(title.exception), "title must be a string")
        task_id = service.add_task("milk").id
        service.submit(task_id)
        with self.assertRaises(TypeError) as task_id_error:
            service.get_task("1")  # type: ignore[arg-type]
        self.assertEqual(str(task_id_error.exception), "task_id must be an int")
        with self.assertRaises(TypeError) as boolean_id:
            service.get_task(True)  # type: ignore[arg-type]
        self.assertEqual(str(boolean_id.exception), "task_id must be an int")
        with self.assertRaises(TypeError) as status_error:
            service.list_tasks("todo")  # type: ignore[arg-type]
        self.assertEqual(str(status_error.exception), "status must be a Status or None")
        with self.assertRaises(TypeError) as wake_error:
            service.snooze(task_id, "tomorrow")  # type: ignore[arg-type]
        self.assertEqual(str(wake_error.exception), "wake_at must be a datetime")
        self.assertEqual(service.get_task(task_id).status, Status.TODO)

    def test_archive_requires_a_completion_time(self) -> None:
        task = Task.from_stored(1, "milk", Status.DONE, True, None, None, None, None)
        with self.assertRaises(TaskRejected) as caught:
            task.archive()
        self.assertEqual(task.status, Status.DONE)
        self.assertEqual(str(caught.exception), "archive rejected in done")

    def test_repr_names_the_task(self) -> None:
        task = Task.draft(1, "milk")
        self.assertEqual(repr(task), "Task(id=1, status='draft', title='milk', listed=False)")

    def test_stored_values_are_checked(self) -> None:
        with self.assertRaises(TypeError) as bad_id:
            Task.from_stored(True, "milk", Status.DRAFT, False, None, None, None, None)  # type: ignore[arg-type]
        self.assertEqual(str(bad_id.exception), "task_id must be an int")
        with self.assertRaises(TypeError) as bad_status:
            Task.from_stored(1, "milk", "draft", False, None, None, None, None)  # type: ignore[arg-type]
        self.assertEqual(str(bad_status.exception), "status must be a Status")
        with self.assertRaises(TypeError) as bad_listed:
            Task.from_stored(1, "milk", Status.DRAFT, 0, None, None, None, None)  # type: ignore[arg-type]
        self.assertEqual(str(bad_listed.exception), "listed must be a bool")
        for name in ("started_at", "completed_at", "wake_at", "deleted_at"):
            stamps = {
                "started_at": None,
                "completed_at": None,
                "wake_at": None,
                "deleted_at": None,
                name: "yesterday",
            }
            with self.subTest(name=name):
                with self.assertRaises(TypeError) as bad_stamp:
                    Task.from_stored(
                        1,
                        "milk",
                        Status.DRAFT,
                        False,
                        stamps["started_at"],  # type: ignore[arg-type]
                        stamps["completed_at"],  # type: ignore[arg-type]
                        stamps["wake_at"],  # type: ignore[arg-type]
                        stamps["deleted_at"],  # type: ignore[arg-type]
                    )
                self.assertEqual(str(bad_stamp.exception), f"{name} must be a datetime or None")

    def test_generated_tests_do_not_call_the_checker(self) -> None:
        source = pathlib.Path(__file__).with_name("test_model_generated.py").read_text()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("tla2tools", source)
        self.assertNotIn("TodoTask", source)


class InMemoryRepositoryTests(unittest.TestCase):
    def test_list_is_ordered_by_id_and_filtered(self) -> None:
        repository = InMemoryTaskRepository()
        repository.add(Task.draft(2, "later"))
        repository.add(Task.draft(1, "first"))
        self.assertEqual([task.id for task in repository.list()], [1, 2])

        second = repository.get(2)
        assert second is not None
        second.submit()
        repository.save(second)
        self.assertEqual([task.id for task in repository.list(Status.TODO)], [2])
        self.assertEqual([task.id for task in repository.list(Status.DRAFT)], [1])

    def test_add_and_save_keep_their_jobs(self) -> None:
        repository = InMemoryTaskRepository()
        with self.assertRaises(ValueError) as missing:
            repository.save(Task.draft(1, "milk"))
        self.assertEqual(str(missing.exception), "task 1 is not stored")

        repository.add(Task.draft(1, "milk"))
        with self.assertRaises(ValueError) as duplicate:
            repository.add(Task.draft(1, "other"))
        self.assertEqual(str(duplicate.exception), "task 1 already exists")
        self.assertEqual(repository.get(1).title, "milk")  # type: ignore[union-attr]

    def test_get_returns_a_copy(self) -> None:
        repository = InMemoryTaskRepository()
        repository.add(Task.draft(1, "milk"))
        first = repository.get(1)
        second = repository.get(1)
        assert first is not None and second is not None
        self.assertIsNot(first, second)
        first.submit()
        self.assertEqual(repository.get(1).status, Status.DRAFT)  # type: ignore[union-attr]
