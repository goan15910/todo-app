"""A repository that reconstitutes projects from rows and writes them back."""

from __future__ import annotations

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection

from tracker.adapters.sqlalchemy import orm
from tracker.domain.model import Comment, Issue, Project, Status
from tracker.service_layer.errors import ConcurrencyConflict


class SqlAlchemyRepository:
    """Projects by key over one connection. Remembers what it handed out."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.seen: dict[str, Project] = {}

    def add(self, project: Project) -> None:
        if project.key in self.seen:
            raise ValueError(f"project {project.key} already exists")
        self._connection.execute(
            insert(orm.projects).values(key=project.key, name=project.name, version=project.version)
        )
        self.seen[project.key] = project

    def get(self, key: str) -> Project | None:
        if key in self.seen:
            return self.seen[key]
        row = self._connection.execute(
            select(orm.projects).where(orm.projects.c.key == key)
        ).one_or_none()
        if row is None:
            return None
        project = Project(row.key, row.name, self._issues(key), version=row.version)
        self.seen[key] = project
        return project

    def list(self) -> tuple[Project, ...]:
        keys = self._connection.execute(
            select(orm.projects.c.key).order_by(orm.projects.c.key)
        ).scalars().all()
        found = []
        for key in keys:
            project = self.get(key)
            if project is not None:
                found.append(project)
        return tuple(found)

    def persist(self, project: Project) -> None:
        """Write one project back. Raises ConcurrencyConflict on a stale version."""

        result = self._connection.execute(
            update(orm.projects)
            .where(orm.projects.c.key == project.key)
            .where(orm.projects.c.version == project.version)
            .values(name=project.name, version=project.version + 1)
        )
        if result.rowcount != 1:
            raise ConcurrencyConflict(project.key)
        project.version += 1
        stored = {
            row.number
            for row in self._connection.execute(
                select(orm.issues.c.number).where(orm.issues.c.project_key == project.key)
            )
        }
        for table in (orm.blockers, orm.comments):
            self._connection.execute(delete(table).where(table.c.project_key == project.key))
        for issue in project.issues:
            values = {
                "title": issue.title,
                "status": issue.status.value,
                "assignee": issue.assignee,
                "started_at": issue.started_at,
                "completed_at": issue.completed_at,
                "canceled_at": issue.canceled_at,
            }
            if issue.number in stored:
                self._connection.execute(
                    update(orm.issues)
                    .where(orm.issues.c.project_key == project.key)
                    .where(orm.issues.c.number == issue.number)
                    .values(**values)
                )
            else:
                self._connection.execute(
                    insert(orm.issues).values(project_key=project.key, number=issue.number, **values)
                )
            if issue.blocked_by:
                self._connection.execute(
                    insert(orm.blockers),
                    [
                        {"project_key": project.key, "number": issue.number, "blocker": blocker}
                        for blocker in sorted(issue.blocked_by)
                    ],
                )
            if issue.comments:
                self._connection.execute(
                    insert(orm.comments),
                    [
                        {
                            "project_key": project.key,
                            "number": issue.number,
                            "position": position,
                            "body": comment.body,
                            "created_at": comment.created_at,
                        }
                        for position, comment in enumerate(issue.comments)
                    ],
                )

    def _issues(self, key: str) -> tuple[Issue, ...]:
        blocked: dict[int, set[int]] = {}
        for row in self._connection.execute(
            select(orm.blockers).where(orm.blockers.c.project_key == key)
        ):
            blocked.setdefault(row.number, set()).add(row.blocker)
        commented: dict[int, list[Comment]] = {}
        for row in self._connection.execute(
            select(orm.comments)
            .where(orm.comments.c.project_key == key)
            .order_by(orm.comments.c.number, orm.comments.c.position)
        ):
            commented.setdefault(row.number, []).append(Comment(row.body, row.created_at))
        rows = self._connection.execute(
            select(orm.issues).where(orm.issues.c.project_key == key).order_by(orm.issues.c.number)
        )
        return tuple(
            Issue(
                key,
                row.number,
                row.title,
                Status(row.status),
                row.assignee,
                frozenset(blocked.get(row.number, ())),
                tuple(commented.get(row.number, ())),
                row.started_at,
                row.completed_at,
                row.canceled_at,
            )
            for row in rows
        )
