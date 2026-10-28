"""Write tests/test_model_generated.py from the TLC state graph.

Dev-time only. The generated module does not import this file and does not
run a model checker. Set TLC_JAR to the path of tla2tools.jar.

Each edge is a change of repository contents. The generator reads that
change and emits an application call. It does not map program traces back
onto the spec; that mapping is not implemented.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "TodoTask.tla"
CFG = ROOT / "spec" / "TodoTask.cfg"
OUTPUT = ROOT / "tests" / "test_model_generated.py"

_FIELDS = (
    "state",
    "title",
    "listed",
    "started_at",
    "completed_at",
    "wake_at",
    "deleted_at",
    "submitted",
)

_NODE = re.compile(r'(?m)^(-?\d+) \[label="((?:\\.|[^"\\])*)"')
_EDGE = re.compile(r'(?m)^(-?\d+) -> (-?\d+) \[label="([^"]*)"')


def main() -> None:
    jar = os.environ.get("TLC_JAR")
    if not jar:
        sys.exit("TLC_JAR is not set")
    id_limit = _max_tasks(CFG.read_text())
    dot, summary = _run_tlc(jar)
    graph = _graph(dot, id_limit)
    OUTPUT.write_text(_render(graph))
    print(f"wrote {OUTPUT.relative_to(ROOT)} states={len(graph.states)} edges={len(graph.edges)}")
    for line in summary:
        print(line)


class _Graph:
    def __init__(self, states: list[tuple], parent: list, edges: list[tuple]) -> None:
        self.states = states
        self.parent = parent
        self.edges = edges


def _max_tasks(cfg: str) -> int:
    match = re.search(r"^MaxTasks\s*=\s*(\d+)\s*$", cfg, re.MULTILINE)
    if match is None:
        sys.exit("spec/TodoTask.cfg does not set MaxTasks")
    return int(match.group(1))


def _run_tlc(jar: str) -> tuple[str, tuple[str, ...]]:
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        dot_path = folder / "states.dot"
        completed = subprocess.run(
            [
                "java",
                "-XX:+UseParallelGC",
                "-jar",
                jar,
                "-dump",
                "dot,actionlabels",
                str(dot_path),
                "-metadir",
                str(folder / "meta"),
                str(SPEC),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        if completed.returncode != 0:
            sys.exit(completed.returncode)
        summary = tuple(
            line
            for line in completed.stdout.splitlines()
            if "states generated" in line or line.startswith("The depth of the complete")
        )
        return dot_path.read_text(), summary


def _unescape(value: str) -> str:
    characters: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            characters.append("\n" if nxt == "n" else nxt)
            index += 2
            continue
        characters.append(value[index])
        index += 1
    return "".join(characters)


class _Parser:
    def __init__(self, text: str) -> None:
        self._text = text
        self._index = 0

    def parse(self) -> object:
        self._skip()
        if self._starts("TRUE"):
            self._index += 4
            return True
        if self._starts("FALSE"):
            self._index += 5
            return False
        current = self._peek()
        if current == "{":
            return self._parse_set()
        if current == "[":
            return self._parse_record()
        if current == '"':
            return self._parse_string()
        if current.isdigit():
            return self._parse_number()
        raise ValueError(f"cannot parse {self._text[self._index:self._index + 40]!r}")

    def _parse_set(self) -> list[object]:
        self._expect("{")
        self._skip()
        if self._peek() == "}":
            self._index += 1
            return []
        items: list[object] = []
        while True:
            items.append(self.parse())
            self._skip()
            if self._peek() == ",":
                self._index += 1
                continue
            self._expect("}")
            return items

    def _parse_record(self) -> dict[str, object]:
        self._expect("[")
        fields: dict[str, object] = {}
        self._skip()
        if self._peek() == "]":
            self._index += 1
            return fields
        while True:
            self._skip()
            name = self._parse_name()
            self._skip()
            self._expect("|->")
            fields[name] = self.parse()
            self._skip()
            if self._peek() == ",":
                self._index += 1
                continue
            self._expect("]")
            return fields

    def _parse_string(self) -> str:
        self._expect('"')
        characters: list[str] = []
        while True:
            current = self._peek()
            if current == "":
                raise ValueError("unterminated string")
            if current == '"':
                self._index += 1
                return "".join(characters)
            characters.append(current)
            self._index += 1

    def _parse_number(self) -> int:
        start = self._index
        while self._peek().isdigit():
            self._index += 1
        return int(self._text[start:self._index])

    def _parse_name(self) -> str:
        start = self._index
        while self._peek().isalnum() or self._peek() == "_":
            self._index += 1
        if start == self._index:
            raise ValueError("expected a name")
        return self._text[start:self._index]

    def _starts(self, literal: str) -> bool:
        return self._text.startswith(literal, self._index)

    def _peek(self) -> str:
        if self._index >= len(self._text):
            return ""
        return self._text[self._index]

    def _skip(self) -> None:
        while self._peek() in " \n\t\r":
            self._index += 1

    def _expect(self, literal: str) -> None:
        self._skip()
        if not self._starts(literal):
            raise ValueError(f"expected {literal!r} at {self._text[self._index:self._index + 40]!r}")
        self._index += len(literal)


def _conjuncts(label: str) -> dict[str, object]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(label):
        if label.startswith("/\\", index) and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            index += 2
            continue
        character = label[index]
        if character in "{[":
            depth += 1
        elif character in "}]":
            depth -= 1
        current.append(character)
        index += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    parsed: dict[str, object] = {}
    for part in parts:
        name, raw_value = part.split("=", 1)
        parsed[name.strip()] = _Parser(raw_value.strip()).parse()
    return parsed


def _task(record: object) -> dict[str, object]:
    if not isinstance(record, dict):
        raise ValueError("task record is not a record")
    task = record.get("task")
    if not isinstance(task, dict):
        raise ValueError("stored task is not a record")
    return task


def _repository(label: str) -> dict[int, dict[str, object]]:
    fields = _conjuncts(label)
    raw_tasks = fields["tasks"]
    if not isinstance(raw_tasks, list):
        raise ValueError("tasks is not a set")
    repository: dict[int, dict[str, object]] = {}
    for record in raw_tasks:
        if not isinstance(record, dict):
            raise ValueError("stored record is not a record")
        task_id = record["id"]
        if not isinstance(task_id, int):
            raise ValueError("id is not an int")
        repository[task_id] = _task(record)
    return repository


def _visible(repository: dict[int, dict[str, object]]) -> tuple:
    return tuple(
        (
            task_id,
            task["state"],
            task["title"],
            task["listed"],
            task["started_at"] == "set",
            task["completed_at"] == "set",
            task["wake_at"] == "set",
            task["deleted_at"] == "set",
        )
        for task_id, task in sorted(repository.items())
    )


def _only(before: dict[str, object], after: dict[str, object], changed: set[str]) -> bool:
    return all(before[field] == after[field] for field in _FIELDS if field not in changed)


def _operation(before: dict[str, object], after: dict[str, object]) -> str:
    matches: list[str] = []
    if (
        before["state"] == "draft"
        and after["state"] == "todo"
        and after["listed"] is True
        and after["submitted"] is True
        and after["title"] != ""
        and _only(before, after, {"state", "listed", "submitted"})
    ):
        matches.append("submit")
    if (
        before["state"] in {"draft", "todo"}
        and after["state"] == "deleted"
        and after["deleted_at"] == "set"
        and _only(before, after, {"state", "deleted_at"})
    ):
        matches.append("delete")
    if (
        before["state"] == "todo"
        and after["state"] == "inProgress"
        and after["started_at"] == "set"
        and _only(before, after, {"state", "started_at"})
    ):
        matches.append("start")
    if (
        before["state"] in {"todo", "inProgress"}
        and after["state"] == "done"
        and after["completed_at"] == "set"
        and _only(before, after, {"state", "completed_at"})
    ):
        matches.append("complete")
    if (
        before["state"] in {"todo", "inProgress"}
        and after["state"] == "snoozed"
        and after["wake_at"] == "set"
        and _only(before, after, {"state", "wake_at"})
    ):
        matches.append("snooze")
    if (
        before["state"] == "snoozed"
        and after["state"] == "inProgress"
        and after["wake_at"] == "unset"
        and _only(before, after, {"state", "wake_at"})
    ):
        matches.append("resume")
    if (
        before["state"] == "done"
        and after["state"] == "todo"
        and after["completed_at"] == "unset"
        and _only(before, after, {"state", "completed_at"})
    ):
        matches.append("reopen")
    if (
        before["state"] == "done"
        and after["state"] == "archived"
        and after["listed"] is False
        and before["completed_at"] == "set"
        and _only(before, after, {"state", "listed"})
    ):
        matches.append("archive")
    if (
        before["state"] == "archived"
        and after["state"] == "done"
        and after["listed"] is True
        and _only(before, after, {"state", "listed"})
    ):
        matches.append("unarchive")
    if (
        before["state"] == "deleted"
        and after["state"] == "todo"
        and after["deleted_at"] == "unset"
        and _only(before, after, {"state", "deleted_at"})
    ):
        matches.append("restore")
    if len(matches) != 1:
        raise ValueError(f"expected one change, found {matches}: {before} -> {after}")
    return matches[0]


def _draft_title(task: dict[str, object]) -> str:
    expected = {
        "state": "draft",
        "listed": False,
        "started_at": "unset",
        "completed_at": "unset",
        "wake_at": "unset",
        "deleted_at": "unset",
        "submitted": False,
    }
    for field, value in expected.items():
        if task[field] != value:
            raise ValueError(f"new task is not a draft: {task}")
    title = task["title"]
    if not isinstance(title, str):
        raise ValueError("title is not a string")
    return title


def _diff(
    before: dict[int, dict[str, object]], after: dict[int, dict[str, object]]
) -> tuple[str, object]:
    added = after.keys() - before.keys()
    removed = before.keys() - after.keys()
    changed = [task_id for task_id in before.keys() & after.keys() if before[task_id] != after[task_id]]
    if removed or len(added) > 1 or (added and changed) or len(changed) > 1:
        raise ValueError(f"unsupported repository change {before} -> {after}")
    if len(added) == 1:
        task_id = next(iter(added))
        return "add", _draft_title(after[task_id])
    if len(changed) == 1:
        task_id = changed[0]
        return _operation(before[task_id], after[task_id]), task_id
    raise ValueError("edge did not change the repository")


def _graph(dot: str, id_limit: int) -> _Graph:
    repositories = {
        fingerprint: _repository(_unescape(label)) for fingerprint, label in _NODE.findall(dot)
    }
    if not repositories:
        raise ValueError("state graph has no states")
    projected: list[tuple[tuple, str, object, tuple]] = []
    for source, destination, _label in _EDGE.findall(dot):
        before = repositories[source]
        after = repositories[destination]
        operation, argument = _diff(before, after)
        projected.append((_visible(before), operation, argument, _visible(after)))

    grouped: dict[tuple, tuple] = {}
    for source, operation, argument, destination in projected:
        key = (source, operation, argument)
        previous = grouped.get(key)
        if previous is not None and previous != (operation, argument, destination):
            raise ValueError(f"one call leaves {source} in two ways")
        grouped[key] = (operation, argument, destination)

    by_source: dict[tuple, list[tuple]] = defaultdict(list)
    nodes = {()}
    for (source, operation, argument), (_operation, _argument, destination) in grouped.items():
        by_source[source].append((operation, argument, destination))
        nodes.add(source)
        nodes.add(destination)
    for successors in by_source.values():
        successors.sort(key=lambda item: (item[0], "" if isinstance(item[1], str) else f"{item[1]:09d}", item[1], item[2]))

    if () not in nodes:
        raise ValueError("state graph has no empty repository")
    states: list[tuple] = [()]
    parent: list = [None]
    index_of = {(): 0}
    seen = {()}
    queue: deque[tuple] = deque([()])
    while queue:
        source = queue.popleft()
        for operation, argument, destination in by_source.get(source, ()):
            if destination not in index_of:
                index_of[destination] = len(states)
                states.append(destination)
                parent.append((index_of[source], operation, argument))
            if destination not in seen:
                seen.add(destination)
                queue.append(destination)
    missing = nodes - seen
    if missing:
        raise ValueError(f"{len(missing)} repository states are not reachable")
    for state in states:
        ids = [row[0] for row in state]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError(f"ids are not dense: {state}")
        if ids and ids[-1] > id_limit:
            raise ValueError(f"id exceeds the bound: {state}")

    edges = []
    for source, operation, argument, destination in projected:
        edges.append((index_of[source], operation, argument, index_of[destination]))
    unique = sorted(set(edges))
    return _Graph(states, parent, unique)


def _render(graph: _Graph) -> str:
    state_lines = "\n".join(f"    {state!r}," for state in graph.states)
    parent_lines = "\n".join(f"    {step!r}," for step in graph.parent)
    edge_lines = "\n".join(f"    {edge!r}," for edge in graph.edges)
    return f'''"""Generated repository traces.

Produced by tools/generate_model_tests.py. Do not edit by hand.
The checks call the application only.
"""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from todo_app import Status, TaskNotFound, in_memory_app

WAKE = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
ID_LIMIT = {_max_tasks(CFG.read_text())}

STATES = (
{state_lines}
)

PARENT = (
{parent_lines}
)

EDGES = (
{edge_lines}
)

_CACHE: dict[int, object] = {{}}


def _apply(service: object, operation: str, argument: object) -> None:
    if operation == "add":
        service.add_task(argument)  # type: ignore[attr-defined]
        return
    if operation == "submit":
        service.submit(argument)  # type: ignore[attr-defined]
        return
    if operation == "delete":
        service.delete(argument)  # type: ignore[attr-defined]
        return
    if operation == "start":
        service.start(argument)  # type: ignore[attr-defined]
        return
    if operation == "complete":
        service.complete(argument)  # type: ignore[attr-defined]
        return
    if operation == "snooze":
        service.snooze(argument, WAKE)  # type: ignore[attr-defined]
        return
    if operation == "resume":
        service.resume(argument)  # type: ignore[attr-defined]
        return
    if operation == "reopen":
        service.reopen(argument)  # type: ignore[attr-defined]
        return
    if operation == "archive":
        service.archive(argument)  # type: ignore[attr-defined]
        return
    if operation == "unarchive":
        service.unarchive(argument)  # type: ignore[attr-defined]
        return
    if operation == "restore":
        service.restore(argument)  # type: ignore[attr-defined]
        return
    raise AssertionError(operation)


def _view(service: object, status: Status | None = None) -> tuple:
    return tuple(
        (
            task.id,
            task.status.value,
            task.title,
            task.listed,
            task.started_at is not None,
            task.completed_at is not None,
            task.wake_at is not None,
            task.deleted_at is not None,
        )
        for task in service.list_tasks(status)  # type: ignore[attr-defined]
    )


def _materialize(index: int) -> object:
    cached = _CACHE.get(index)
    if cached is not None:
        return copy.deepcopy(cached)
    if PARENT[index] is None:
        service = in_memory_app()
    else:
        parent, operation, argument = PARENT[index]
        service = _materialize(parent)
        _apply(service, operation, argument)
    _CACHE[index] = service
    return copy.deepcopy(service)


class GeneratedRepositoryTests(unittest.TestCase):
    def test_empty_repository(self) -> None:
        self._check(in_memory_app(), STATES[0])

    def test_transitions(self) -> None:
        for source, operation, argument, destination in EDGES:
            with self.subTest(source=source, operation=operation, argument=argument):
                service = _materialize(source)
                _apply(service, operation, argument)
                if operation == "snooze":
                    self.assertEqual(service.get_task(argument).wake_at, WAKE)  # type: ignore[attr-defined]
                self._check(service, STATES[destination])

    def _check(self, service: object, state: tuple) -> None:
        self.assertEqual(_view(service), state)
        present = {{row[0] for row in state}}
        for task_id in range(1, ID_LIMIT + 1):
            if task_id in present:
                expected = next(row for row in state if row[0] == task_id)
                self.assertEqual(self._view_one(service, task_id), expected)
            else:
                with self.assertRaises(TaskNotFound):
                    service.get_task(task_id)  # type: ignore[attr-defined]
        for status in Status:
            expected = tuple(row for row in state if row[1] == status.value)
            self.assertEqual(_view(service, status), expected)

    def _view_one(self, service: object, task_id: int) -> tuple:
        task = service.get_task(task_id)  # type: ignore[attr-defined]
        return (
            task.id,
            task.status.value,
            task.title,
            task.listed,
            task.started_at is not None,
            task.completed_at is not None,
            task.wake_at is not None,
            task.deleted_at is not None,
        )
'''


if __name__ == "__main__":
    main()
