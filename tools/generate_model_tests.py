"""Write lifecycle scenario tests from the TLC state graph.

Dev-time only. The generated tests do not import this file and do not run a
model checker. Set TLC_JAR to the path of tla2tools.jar.

TLC still runs with ``-dump dot,actionlabels``. Edge labels are the spec
actions. States are collapsed to one task, and the ``submitted`` history
field is ignored. A short deterministic cover of those actions becomes the
committed tests. ``--replay`` walks every TLC edge in a temporary directory
and writes nothing to the repository.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
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
SCENARIOS = ROOT / "tests" / "test_lifecycle_scenarios.py"
TRACES = ROOT / "tests" / "model" / "traces.json"

_FIELDS = (
    "state",
    "title",
    "listed",
    "started_at",
    "completed_at",
    "wake_at",
    "deleted_at",
)
_RANK = {
    "draft": 1,
    "todo": 2,
    "inProgress": 3,
    "snoozed": 4,
    "done": 5,
    "archived": 6,
    "deleted": 7,
}
_STATUS_ORDER = (
    "draft",
    "todo",
    "inProgress",
    "snoozed",
    "done",
    "archived",
    "deleted",
)
_STATUS = {
    "draft": "DRAFT",
    "todo": "TODO",
    "inProgress": "IN_PROGRESS",
    "snoozed": "SNOOZED",
    "done": "DONE",
    "archived": "ARCHIVED",
    "deleted": "DELETED",
}
_VERB = {
    "Add": "add",
    "DraftSubmit": "submit",
    "DraftDelete": "delete",
    "TodoStart": "start",
    "TodoComplete": "complete",
    "TodoSnooze": "snooze",
    "TodoDelete": "delete",
    "InProgressSnooze": "snooze",
    "InProgressComplete": "complete",
    "SnoozedResume": "resume",
    "DoneReopen": "reopen",
    "DoneArchive": "archive",
    "ArchivedUnarchive": "unarchive",
    "DeletedRestore": "restore",
}
_METHOD = {
    "DraftSubmit": "submit",
    "DraftDelete": "delete",
    "TodoStart": "start",
    "TodoComplete": "complete",
    "TodoDelete": "delete",
    "InProgressComplete": "complete",
    "SnoozedResume": "resume",
    "DoneReopen": "reopen",
    "DoneArchive": "archive",
    "ArchivedUnarchive": "unarchive",
    "DeletedRestore": "restore",
}
_SNOOZE = {"TodoSnooze", "InProgressSnooze"}
_TITLES = {1: "Buy oat milk", 2: "Walk the dog"}
_CAP = 8

_NODE = re.compile(r'(?m)^(-?\d+) \[label="((?:\\.|[^"\\])*)"')
_EDGE = re.compile(r'(?m)^(-?\d+) -> (-?\d+) \[label="([^"]*)"')
_NEXT = re.compile(r"(?m)^Next ==\n((?:  \\/ \w+\n)+)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate lifecycle scenario tests from the TLC state graph."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and fail if the committed files differ",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="replay every TLC edge against the service; write nothing",
    )
    args = parser.parse_args()
    jar = os.environ.get("TLC_JAR")
    if not jar:
        sys.exit("TLC_JAR is not set")
    spec_actions = _spec_actions(SPEC.read_text(encoding="utf-8"))
    max_tasks = _max_tasks(CFG.read_text(encoding="utf-8"))
    spec_hash = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    dot, summary = _run_tlc(jar)
    graph = _build(dot, spec_actions)
    scenarios = _cover(graph, spec_actions)
    files = {
        SCENARIOS: _render(scenarios, max_tasks),
        TRACES: _traces(scenarios, spec_hash, max_tasks),
    }
    status = 0
    if args.check:
        status = _diff(files)
        if status == 0:
            print("lifecycle scenarios match")
    else:
        _write(files)
        print(f"wrote {SCENARIOS.relative_to(ROOT)} scenarios={len(scenarios)}")
    for scenario in scenarios:
        print(_trace_id(scenario.actions), ">", " > ".join(_VERB[name] for name in scenario.actions))
    for line in summary:
        print(line)
    if args.replay:
        with tempfile.TemporaryDirectory(prefix="lifecycle-replay-") as directory:
            _replay(graph, max_tasks, Path(directory))
    if status:
        sys.exit(status)


class _Step:
    def __init__(self, action: str, task_id: int, world: tuple[tuple[int, tuple], ...]) -> None:
        self.action = action
        self.task_id = task_id
        self.world = world


class _Scenario:
    def __init__(self, steps: list[_Step], isolation: bool) -> None:
        self.steps = tuple(steps)
        self.actions = tuple(step.action for step in self.steps)
        self.isolation = isolation


class _Graph:
    def __init__(
        self,
        repos: dict[str, dict[int, dict[str, object]]],
        edges: list[tuple[str, str, str]],
        life: dict[tuple | None, list[tuple[str, tuple]]],
    ) -> None:
        self.repos = repos
        self.edges = edges
        self.life = life


def _spec_actions(text: str) -> tuple[str, ...]:
    match = _NEXT.search(text)
    if match is None:
        sys.exit("spec/TodoTask.tla has no Next disjunction")
    names = tuple(re.findall(r"\\/ (\w+)", match.group(1)))
    if not names or len(names) != len(set(names)):
        sys.exit("spec/TodoTask.tla has duplicate Next actions")
    return names


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
        if completed.returncode != 0:
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            sys.exit(completed.returncode)
        summary = tuple(
            line
            for line in completed.stdout.splitlines()
            if "states generated" in line or line.startswith("The depth of the complete")
        )
        return dot_path.read_text(encoding="utf-8"), summary


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


def _project(task: dict[str, object]) -> tuple:
    return tuple(task[field] for field in _FIELDS)


def _subject(before: dict[int, dict[str, object]], after: dict[int, dict[str, object]]) -> int:
    added = after.keys() - before.keys()
    removed = before.keys() - after.keys()
    changed = [
        task_id
        for task_id in before.keys() & after.keys()
        if _project(before[task_id]) != _project(after[task_id])
    ]
    if removed or len(added) > 1 or (added and changed) or len(changed) > 1:
        raise ValueError("edge does not change one task")
    if len(added) == 1:
        return next(iter(added))
    if len(changed) == 1:
        return changed[0]
    raise ValueError("edge did not change a task")


def _repo_key(repository: dict[int, dict[str, object]]) -> tuple:
    return tuple((task_id,) + _project(task) for task_id, task in sorted(repository.items()))


def _life_key(edge: tuple[str, tuple]) -> tuple:
    action, dest = edge
    return (action, dest[1] == "", dest)


def _build(dot: str, spec_actions: tuple[str, ...]) -> _Graph:
    repos = {fingerprint: _repository(_unescape(label)) for fingerprint, label in _NODE.findall(dot)}
    if not repos:
        sys.exit("state graph has no states")
    raw = _EDGE.findall(dot)
    if not raw:
        sys.exit("state graph has no edges")
    life: dict[tuple | None, list[tuple[str, tuple]]] = defaultdict(list)
    seen: set[tuple] = set()
    labels: set[str] = set()
    for source, destination, action in raw:
        if action not in _VERB:
            sys.exit(f"unknown action label {action}")
        labels.add(action)
        before = repos[source]
        after = repos[destination]
        task_id = _subject(before, after)
        pre = None if task_id not in before else _project(before[task_id])
        post = _project(after[task_id])
        key = (pre, action, post)
        if key in seen:
            continue
        seen.add(key)
        life[pre].append((action, post))
    for successors in life.values():
        successors.sort(key=_life_key)
    expected = set(spec_actions)
    if labels != expected:
        missing = ", ".join(sorted(expected - labels))
        extra = ", ".join(sorted(labels - expected))
        sys.exit(f"action labels do not match Next (missing: {missing or '-'}; extra: {extra or '-'})")
    return _Graph(repos, [(source, destination, action) for source, destination, action in raw], life)


def _reachable(life: dict, node: tuple | None) -> set[str]:
    found: set[str] = set()
    seen: set[tuple | None] = set()
    queue: deque[tuple | None] = deque([node])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        for action, dest in life.get(current, ()):
            found.add(action)
            queue.append(dest)
    return found


def _spine(life: dict) -> list[tuple[str, tuple]]:
    """One walk that takes the next forward step and saves side branches.

    Statuses have a fixed rank. From the current task the lowest forward
    rank is the step taken now. The highest forward rank stays available
    for later on this same walk. Ranks in between are deferred to their own
    scenarios. Ties break by action name, then by how many actions the
    destination can still reach, then by the destination itself.
    """

    deferred: set[str] = set()
    used: set[str] = set()
    steps: list[tuple[str, tuple]] = []
    node: tuple | None = None
    while True:
        options = [edge for edge in life.get(node, ()) if edge[0] not in used and edge[0] not in deferred]
        if not options:
            break
        current = 0 if node is None else _RANK[node[0]]
        forward = [edge for edge in options if _RANK[edge[1][0]] > current]
        if forward:
            by_rank: dict[int, list[tuple[str, tuple]]] = defaultdict(list)
            for edge in forward:
                by_rank[_RANK[edge[1][0]]].append(edge)
            ranks = sorted(by_rank)
            low, high = ranks[0], ranks[-1]
            for rank in ranks:
                if rank != low and rank != high:
                    deferred.update(action for action, _dest in by_rank[rank])

            def choice_key(edge: tuple[str, tuple]) -> tuple:
                return (edge[0], -len(_reachable(life, edge[1])), edge[1])

            action, dest = min(by_rank[low], key=choice_key)
        else:
            action, dest = min(options, key=lambda edge: (edge[0], edge[1]))
        steps.append((action, dest))
        used.add(action)
        node = dest
    if not steps:
        sys.exit("lifecycle cover produced an empty path")
    return steps


def _shortest(life: dict, target: str) -> list[tuple[str, tuple]]:
    queue: deque[tuple[tuple | None, tuple[tuple[str, tuple], ...]]] = deque([(None, ())])
    seen: set[tuple | None] = {None}
    while queue:
        node, sofar = queue.popleft()
        for action, dest in life.get(node, ()):
            steps = sofar + ((action, dest),)
            if action == target:
                node = dest
                chosen = list(steps)
                while True:
                    outs = life.get(node, ())
                    if len(outs) != 1:
                        break
                    follow, nxt = outs[0]
                    if follow in {taken for taken, _dest in chosen} or nxt[0] != "todo":
                        break
                    chosen.append((follow, nxt))
                    node = nxt
                return chosen
            if dest not in seen:
                seen.add(dest)
                queue.append((dest, steps))
    sys.exit(f"no path reaches {target}")


def _single(steps: list[tuple[str, tuple]]) -> _Scenario:
    world: tuple[tuple[int, tuple], ...] = ()
    built: list[_Step] = []
    for action, dest in steps:
        world = ((1, dest),)
        built.append(_Step(action, 1, world))
    return _Scenario(built, False)


def _isolation_state(state: tuple) -> bool:
    if len(state) != 2:
        return False
    if any(row[2] != "task" for row in state):
        return False
    return sorted(row[1] for row in state) == ["draft", "todo"]


def _isolation(graph: _Graph) -> _Scenario:
    adjacency: dict[tuple, list[tuple[str, int, tuple]]] = defaultdict(list)
    seen: set[tuple] = set()
    for source, destination, action in graph.edges:
        before = graph.repos[source]
        after = graph.repos[destination]
        origin = _repo_key(before)
        landing = _repo_key(after)
        task_id = _subject(before, after)
        marker = (origin, action, task_id, landing)
        if marker in seen:
            continue
        seen.add(marker)
        adjacency[origin].append((action, task_id, landing))
    for successors in adjacency.values():
        successors.sort(key=lambda edge: (edge[0], edge[1], edge[2]))

    queue: deque[tuple[tuple, tuple[tuple[str, int, tuple], ...]]] = deque([((), ())])
    visited = {()}
    while queue:
        state, sofar = queue.popleft()
        if _isolation_state(state):
            steps = [
                _Step(action, task_id, tuple((row[0], row[1:]) for row in landing))
                for action, task_id, landing in sofar
            ]
            return _Scenario(steps, True)
        for action, task_id, landing in adjacency.get(state, ()):
            if landing in visited:
                continue
            visited.add(landing)
            queue.append((landing, sofar + ((action, task_id, landing),)))
    sys.exit("no two-task path for id isolation")


def _cover(graph: _Graph, spec_actions: tuple[str, ...]) -> list[_Scenario]:
    spine = _spine(graph.life)
    scenarios = [_single(spine)]
    covered = {action for action, _dest in spine}
    for target in spec_actions:
        if target in covered:
            continue
        steps = _shortest(graph.life, target)
        scenarios.append(_single(steps))
        covered.update(action for action, _dest in steps)
    missing = [action for action in spec_actions if action not in covered]
    if missing:
        sys.exit("uncovered spec actions: " + ", ".join(missing))
    scenarios.append(_isolation(graph))
    if len(scenarios) > _CAP:
        sys.exit(f"{len(scenarios)} scenarios exceeds the cap of {_CAP}")
    return scenarios


def _trace_id(actions: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(actions).encode("utf-8")).hexdigest()[:12]


def _traces(scenarios: list[_Scenario], spec_hash: str, max_tasks: int) -> str:
    traces = []
    for scenario in scenarios:
        covers: list[str] = []
        for action in scenario.actions:
            if action not in covers:
                covers.append(action)
        traces.append({"id": _trace_id(scenario.actions), "actions": list(scenario.actions), "covers": covers})
    payload = {"spec_sha256": spec_hash, "max_tasks": max_tasks, "traces": traces}
    return json.dumps(payload, indent=2) + "\n"


def _py(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _title_for(spec_title: object, task_id: int) -> str:
    if spec_title == "":
        return ""
    if spec_title != "task":
        sys.exit(f"unexpected title {spec_title!r}")
    try:
        return _TITLES[task_id]
    except KeyError:
        sys.exit(f"no realistic title for task {task_id}")


def _id_expr(task_id: int, isolation: bool) -> str:
    if not isolation:
        return "task_id"
    if task_id == 1:
        return "first.id"
    if task_id == 2:
        return "second.id"
    return str(task_id)


def _call(action: str, id_expr: str, title: str) -> str:
    if action == "Add":
        return f"service.add_task({_py(title)})"
    if action in _SNOOZE:
        return f"service.snooze({id_expr}, WAKE)"
    return f"service.{_METHOD[action]}({id_expr})"


def _emit_fields(lines: list[str], expr: str, projection: tuple, title: str) -> None:
    state, _spec_title, listed, started, completed, wake, deleted = projection
    lines.append(f"        self.assertEqual({expr}.status, Status.{_STATUS[state]})")
    lines.append(f"        self.assertEqual({expr}.title, {_py(title)})")
    lines.append(f"        self.assert{'True' if listed else 'False'}({expr}.listed)")
    for name, mark, wake_stamp in (
        ("started_at", started, False),
        ("completed_at", completed, False),
        ("wake_at", wake, True),
        ("deleted_at", deleted, False),
    ):
        if mark == "set" and wake_stamp:
            lines.append(f"        self.assertEqual({expr}.{name}, WAKE)")
        elif mark == "set":
            lines.append(f"        self.assertIsNotNone({expr}.{name})")
        elif mark == "unset":
            lines.append(f"        self.assertIsNone({expr}.{name})")
        else:
            sys.exit(f"unexpected stamp {mark!r}")


def _emit_lists(lines: list[str], world: tuple[tuple[int, tuple], ...], previous: tuple, isolation: bool) -> None:
    present = "[" + ", ".join(_id_expr(task_id, isolation) for task_id, _proj in world) + "]"
    lines.append(f"        self.assertEqual([item.id for item in service.list_tasks()], {present})")
    interesting = {proj[0] for _task_id, proj in world}
    interesting.update(proj[0] for _task_id, proj in previous)
    for state in _STATUS_ORDER:
        if state not in interesting:
            continue
        current = tuple((task_id, proj) for task_id, proj in world if proj[0] == state)
        attr = _STATUS[state]
        if current:
            ids = "[" + ", ".join(_id_expr(task_id, isolation) for task_id, _proj in current) + "]"
            lines.append(
                f"        self.assertEqual([item.id for item in service.list_tasks(Status.{attr})], {ids})"
            )
        else:
            lines.append(f"        self.assertEqual(service.list_tasks(Status.{attr}), ())")


def _emit_step(
    lines: list[str],
    step: _Step,
    isolation: bool,
    previous: tuple,
    last: bool,
    max_tasks: int,
) -> None:
    world = dict(step.world)
    projection = world[step.task_id]
    title = _title_for(projection[1], step.task_id)
    if step.action == "Add":
        if isolation:
            var = "first" if step.task_id == 1 else "second"
            lines.append(f"        {var} = {_call(step.action, '', title)}")
            lines.append(f"        self.assertEqual({var}.id, {step.task_id})")
            ret = var
        else:
            lines.append(f"        task = {_call(step.action, '', title)}")
            lines.append("        task_id = task.id")
            lines.append("        self.assertEqual(task_id, 1)")
            ret = "task"
    else:
        lines.append(f"        task = {_call(step.action, _id_expr(step.task_id, isolation), title)}")
        ret = "task"
    _emit_fields(lines, ret, projection, title)
    lines.append(f"        stored = service.get_task({_id_expr(step.task_id, isolation)})")
    lines.append(f"        self.assertEqual(stored.status, {ret}.status)")
    lines.append(f"        self.assertEqual(stored.title, {ret}.title)")
    lines.append(f"        self.assertEqual(stored.listed, {ret}.listed)")
    for name in ("started_at", "completed_at", "wake_at", "deleted_at"):
        lines.append(f"        self.assertEqual(stored.{name}, {ret}.{name})")
    for other_id, other in step.world:
        if other_id == step.task_id:
            continue
        lines.append(f"        untouched = service.get_task({_id_expr(other_id, isolation)})")
        _emit_fields(lines, "untouched", other, _title_for(other[1], other_id))
    _emit_lists(lines, step.world, previous, isolation)
    if not isolation:
        return
    present = {task_id for task_id, _proj in step.world}
    missing = []
    if max_tasks >= 2 and 2 not in present:
        missing.append(2)
    if last and (max_tasks + 1) not in present:
        missing.append(max_tasks + 1)
    for task_id in missing:
        lines.append("        with self.assertRaises(TaskNotFound) as caught:")
        lines.append(f"            service.get_task({task_id})")
        lines.append(f"        self.assertEqual(caught.exception.task_id, {task_id})")


def _method_name(scenario: _Scenario, trace_id: str, used: set[str]) -> str:
    if scenario.isolation:
        name = "test_two_tasks_stay_isolated_by_id"
    else:
        name = "test_" + "_".join(_VERB[action] for action in scenario.actions)
    if name in used:
        name = f"{name}_{trace_id}"
    used.add(name)
    return name


def _render(scenarios: list[_Scenario], max_tasks: int) -> str:
    needs_wake = any(action in _SNOOZE for scenario in scenarios for action in scenario.actions)
    needs_missing = any(scenario.isolation for scenario in scenarios)
    imported = ["Status", "in_memory_app"]
    if needs_missing:
        imported.insert(1, "TaskNotFound")
    lines = [
        '"""Lifecycle scenarios generated from the TLC state graph.',
        "",
        "Produced by tools/generate_model_tests.py. Do not edit by hand.",
        "Each test calls the application only. The trace id is a key in",
        "tests/model/traces.json.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import unittest",
    ]
    if needs_wake:
        lines.append("from datetime import datetime, timezone")
    lines.append("")
    lines.append(f"from todo_app import {', '.join(imported)}")
    lines.append("")
    if needs_wake:
        lines.append("WAKE = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)")
        lines.append("")
    lines.append("")
    lines.append("class LifecycleScenarioTests(unittest.TestCase):")
    used: set[str] = set()
    methods: list[str] = []
    for scenario in scenarios:
        trace_id = _trace_id(scenario.actions)
        verbs = " > ".join(_VERB[action] for action in scenario.actions)
        body = [
            f"    def {_method_name(scenario, trace_id, used)}(self) -> None:",
            f'        """Trace: {trace_id} {verbs}"""',
            "        service = in_memory_app()",
        ]
        previous: tuple = ()
        for index, step in enumerate(scenario.steps):
            if index:
                body.append("")
            _emit_step(body, step, scenario.isolation, previous, index == len(scenario.steps) - 1, max_tasks)
            previous = step.world
        methods.append("\n".join(body))
    lines.append("\n\n".join(methods))
    lines.append("")
    return "\n".join(lines)


def _write(files: dict[Path, str]) -> None:
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _diff(files: dict[Path, str]) -> int:
    failed = False
    for path, text in files.items():
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == text:
            continue
        failed = True
        relative = path.relative_to(ROOT)
        print(f"differs: {relative}", file=sys.stderr)
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=str(relative),
            tofile=f"{relative} (regenerated)",
        )
        sys.stderr.writelines(list(diff)[:240])
    return 1 if failed else 0


def _replay(graph: _Graph, max_tasks: int, directory: Path) -> None:
    import copy
    from datetime import datetime, timezone

    sys.path.insert(0, str(ROOT))
    from todo_app import Status, TaskNotFound, in_memory_app

    wake = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    outgoing: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source, destination, action in graph.edges:
        outgoing[source].append((action, destination))
    empties = [fingerprint for fingerprint, repo in graph.repos.items() if not repo]
    if len(empties) != 1:
        sys.exit("expected one empty repository")
    initial = empties[0]
    parent: dict[str, tuple[str, str] | None] = {initial: None}
    queue: deque[str] = deque([initial])
    while queue:
        source = queue.popleft()
        for action, destination in outgoing[source]:
            if destination in parent:
                continue
            parent[destination] = (source, action)
            queue.append(destination)
    if len(parent) != len(graph.repos):
        sys.exit(f"{len(graph.repos) - len(parent)} states are not reachable")

    cache: dict[str, object] = {}

    def materialize(fingerprint: str) -> object:
        cached = cache.get(fingerprint)
        if cached is not None:
            return copy.deepcopy(cached)
        link = parent[fingerprint]
        if link is None:
            service = in_memory_app()
        else:
            origin, action = link
            service = materialize(origin)
            _apply(service, action, graph.repos[origin], graph.repos[fingerprint], wake)
        cache[fingerprint] = copy.deepcopy(service)
        return copy.deepcopy(service)

    checked = 0
    for source, destination, action in graph.edges:
        service = materialize(source)
        _apply(service, action, graph.repos[source], graph.repos[destination], wake)
        _expect(service, graph.repos[destination], max_tasks, Status, TaskNotFound, wake)
        checked += 1
    (directory / "replay.txt").write_text(f"edges={checked}\n", encoding="utf-8")
    print(f"replayed edges={checked}")


def _apply(service: object, action: str, before: dict, after: dict, wake: object) -> None:
    task_id = _subject(before, after)
    if action == "Add":
        created = service.add_task(after[task_id]["title"])  # type: ignore[attr-defined]
        if created.id != task_id:
            raise RuntimeError(f"Add produced id {created.id}, TLC has {task_id}")
        return
    if action in _SNOOZE:
        service.snooze(task_id, wake)  # type: ignore[attr-defined]
        return
    method = _METHOD.get(action)
    if method is None:
        raise RuntimeError(action)
    getattr(service, method)(task_id)


def _expect(service: object, repository: dict, max_tasks: int, status_enum: object, missing: type, wake: object) -> None:
    for task_id, record in sorted(repository.items()):
        task = service.get_task(task_id)  # type: ignore[attr-defined]
        if task.status.value != record["state"] or task.title != record["title"] or task.listed is not record["listed"]:
            raise RuntimeError(
                f"task {task_id} is {task.status.value}/{task.title}, expected {record['state']}/{record['title']}"
            )
        _expect_stamp(task.started_at, record["started_at"], wake, False, task_id, "started_at")
        _expect_stamp(task.completed_at, record["completed_at"], wake, False, task_id, "completed_at")
        _expect_stamp(task.wake_at, record["wake_at"], wake, True, task_id, "wake_at")
        _expect_stamp(task.deleted_at, record["deleted_at"], wake, False, task_id, "deleted_at")
    present = set(repository)
    for task_id in range(1, max_tasks + 1):
        if task_id in present:
            continue
        try:
            service.get_task(task_id)  # type: ignore[attr-defined]
        except missing:
            continue
        raise RuntimeError(f"task {task_id} should be missing")
    got = [task.id for task in service.list_tasks()]  # type: ignore[attr-defined]
    if got != sorted(present):
        raise RuntimeError(f"list ids {got} != {sorted(present)}")
    for status in status_enum:  # type: ignore[operator]
        expect = sorted(task_id for task_id, record in repository.items() if record["state"] == status.value)
        got = [task.id for task in service.list_tasks(status)]  # type: ignore[attr-defined]
        if got != expect:
            raise RuntimeError(f"list {status.value} {got} != {expect}")


def _expect_stamp(value: object, mark: object, wake: object, is_wake: bool, task_id: int, name: str) -> None:
    if mark == "set":
        if is_wake:
            if value != wake:
                raise RuntimeError(f"task {task_id} {name} != wake")
        elif value is None:
            raise RuntimeError(f"task {task_id} {name} is unset")
    elif mark == "unset":
        if value is not None:
            raise RuntimeError(f"task {task_id} {name} is set")
    else:
        raise RuntimeError(f"task {task_id} {name} mark {mark!r}")


if __name__ == "__main__":
    main()
