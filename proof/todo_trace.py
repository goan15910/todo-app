"""Generate a TodoTrace module for one recorded execution and run TLC on it.

The module is generated from ``spec/TodoTask.tla`` and the action map. It is
not hand-written. ``submitted`` stays inside the model state. The recorded
post-state is compared with that field projected away.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from proof.mapping import TaskView, View, action_names, binding_for
from proof.registry import Step, Trace
from proof.spec import SPEC, invariant_names, max_tasks, next_actions


_STEP_VALUE = re.compile(r"/\\\s*step\s*=\s*(\d+)")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9_]*")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    name: str
    line: int | None
    file: str | None
    kind: str
    message: str


def check_registry() -> None:
    mapped = action_names()
    model = next_actions()
    if set(mapped) != set(model) or len(mapped) != len(model):
        raise RuntimeError(
            "ACTION_MAP does not match Next. "
            f"mapping={mapped!r} model={model!r}"
        )


def render_call(step: Step) -> str:
    if step.action == "Add":
        return f'Add("{step.title}")'
    if step.action == "UNMAPPED":
        return f"{step.method}({step.task_id})"
    return f"{step.action}({step.task_id})"


def module_text(trace: Trace) -> str:
    check_registry()
    if not trace.steps:
        raise RuntimeError(f"{trace.name} has no steps")
    clauses = "\n".join(_clause(name) for name in next_actions())
    return f"""\
---- MODULE TodoTrace ----
\\* Generated from spec/TodoTask.tla and proof/mapping.py. Do not edit.
\\* Trace {trace.name}. submitted is auxiliary history inside the model.
EXTENDS TodoTask, Sequences

VARIABLE step

Project(record) ==
  [
    id |-> record.id,
    state |-> record.task.state,
    title |-> record.task.title,
    listed |-> record.task.listed,
    started_at |-> record.task.started_at,
    completed_at |-> record.task.completed_at,
    wake_at |-> record.task.wake_at,
    deleted_at |-> record.task.deleted_at
  ]

Changed(taskId) ==
  /\\ \\E before \\in tasks, after \\in tasks' :
       /\\ before.id = taskId
       /\\ after.id = taskId
       /\\ before.task # after.task
  /\\ \\A before \\in tasks :
       before.id # taskId => \\E after \\in tasks' : after = before

Act(actionName, taskId, taskTitle) ==
{clauses}

Steps == <<
{_steps(trace)}
>>

Posts == <<
{_posts(trace)}
>>

TraceInit ==
  /\\ Init
  /\\ step = 0

TraceNext ==
  /\\ step < Len(Steps)
  /\\ LET n == step + 1
         actionName == Steps[n].action
         taskId == Steps[n].id
         taskTitle == Steps[n].title
     IN /\\ Act(actionName, taskId, taskTitle)
        /\\ {{Project(record) : record \\in tasks'}} = Posts[n].tasks
        /\\ seq' = Posts[n].seq
        /\\ step' = step + 1

Guard ==
  /\\ step < Len(Steps)
  /\\ LET n == step + 1 IN
       Act(Steps[n].action, Steps[n].id, Steps[n].title)

GuardHolds ==
  \\/ step = Len(Steps)
  \\/ ENABLED Guard

TraceAccepted ==
  \\/ step = Len(Steps)
  \\/ ENABLED TraceNext

====
"""


def config_text() -> str:
    lines = [
        "CONSTANT",
        f"MaxTasks = {max_tasks()}",
        "",
        "INIT TraceInit",
        "NEXT TraceNext",
        "CHECK_DEADLOCK FALSE",
        "",
        "INVARIANT GuardHolds",
        "INVARIANT TraceAccepted",
    ]
    lines.extend(f"INVARIANT {name}" for name in invariant_names())
    lines.append("")
    return "\n".join(lines)


def validate_one(trace: Trace, jar: str, timeout: float = 60) -> Verdict:
    with tempfile.TemporaryDirectory(prefix=f"todo-trace-{trace.name}-") as directory:
        folder = Path(directory)
        spec = folder / "TodoTask.tla"
        spec.symlink_to(SPEC)
        (folder / "TodoTrace.tla").write_text(module_text(trace), encoding="utf-8")
        (folder / "TodoTrace.cfg").write_text(config_text(), encoding="utf-8")
        (folder / f"{trace.name}.json").write_text(_trace_json(trace), encoding="utf-8")
        # A private jar keeps TLC's standard-module cache off a shared directory.
        # Parallel checkers otherwise race while writing those modules.
        local_jar = folder / "tla2tools.jar"
        shutil.copy(jar, local_jar)
        try:
            completed = subprocess.run(
                [
                    "java",
                    "-XX:+UseParallelGC",
                    "-jar",
                    str(local_jar),
                    "-workers",
                    "1",
                    "-metadir",
                    str(folder / "meta"),
                    "TodoTrace.tla",
                ],
                cwd=folder,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return _error(trace, "TLC timed out")
        output = completed.stdout + completed.stderr
    if completed.returncode == 0 and "No error has been found." in output:
        return Verdict(
            ok=True,
            name=trace.name,
            line=None,
            file=None,
            kind="accepted",
            message=f"trace {trace.name} accepted",
        )
    return _stuck(trace, output)


def validate_all(traces: tuple[Trace, ...] | list[Trace], jar: str) -> list[Verdict]:
    if not traces:
        return []
    workers = min(len(traces), os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda trace: validate_one(trace, jar), traces))


def _stuck(trace: Trace, output: str) -> Verdict:
    if "Invariant GuardHolds is violated" in output:
        kind = "guard"
    elif "Invariant TraceAccepted is violated" in output:
        kind = "state"
    else:
        tail = "\n".join(output.strip().splitlines()[-12:])
        return _error(trace, tail or "TLC failed")
    values = [int(value) for value in _STEP_VALUE.findall(output)]
    if not values:
        return _error(trace, "TLC did not report step")
    index = values[-1]
    if index < 0 or index >= len(trace.steps):
        return _error(trace, f"TLC reported step {index}")
    step = trace.steps[index]
    return Verdict(
        ok=False,
        name=trace.name,
        line=step.line,
        file=step.file,
        kind=kind,
        message=_message(trace, step, kind),
    )


def _error(trace: Trace, detail: str) -> Verdict:
    return Verdict(
        ok=False,
        name=trace.name,
        line=None,
        file=None,
        kind="error",
        message=f"trace {trace.name} failed: {detail}",
    )


def _message(trace: Trace, step: Step, kind: str) -> str:
    where = f"trace {trace.name}, {step.file} line {step.line}: {render_call(step)}"
    if kind == "guard":
        return f"{where} taken while the guard was false"
    if kind == "state":
        return f"{where} taken but the resulting state does not match"
    return f"{where} was not accepted"


def _clause(action: str) -> str:
    binding = binding_for(action)
    if binding.title_param is not None:
        return "\n".join(
            [
                f'  \\/ /\\ actionName = "{action}"',
                f"     /\\ {action}",
                "     /\\ \\E created \\in tasks' :",
                "          /\\ created.id = seq",
                "          /\\ created.task.title = taskTitle",
                "          /\\ ~\\E old \\in tasks : old.id = created.id",
            ]
        )
    return "\n".join(
        [
            f'  \\/ /\\ actionName = "{action}"',
            f"     /\\ {action}",
            "     /\\ Changed(taskId)",
        ]
    )


def _steps(trace: Trace) -> str:
    rows = []
    for step in trace.steps:
        _plain(step.action)
        _plain(step.title)
        rows.append(
            f'  [action |-> "{step.action}", id |-> {step.task_id}, '
            f'title |-> "{step.title}", line |-> {step.line}]'
        )
    return ",\n".join(rows)


def _posts(trace: Trace) -> str:
    rows = []
    for step in trace.steps:
        rows.append(
            f"  [seq |-> {step.post.seq}, tasks |-> {_task_set(step.post)}]"
        )
    return ",\n".join(rows)


def _task_set(view: View) -> str:
    if not view.tasks:
        return "{}"
    body = ",\n    ".join(_task(task) for task in view.tasks)
    return "{\n    " + body + "\n  }"


def _task(task: TaskView) -> str:
    _plain(task.state)
    _plain(task.title)
    for mark in (task.started_at, task.completed_at, task.wake_at, task.deleted_at):
        if mark not in {"set", "unset"}:
            raise RuntimeError(f"mark {mark!r} is not set or unset")
    listed = "TRUE" if task.listed else "FALSE"
    return (
        f'[id |-> {task.id}, state |-> "{task.state}", title |-> "{task.title}", '
        f"listed |-> {listed}, started_at |-> \"{task.started_at}\", "
        f'completed_at |-> "{task.completed_at}", wake_at |-> "{task.wake_at}", '
        f'deleted_at |-> "{task.deleted_at}"]'
    )


def _plain(value: str) -> None:
    if not _SAFE_TEXT.fullmatch(value):
        raise RuntimeError(f"refusing to embed {value!r} in a trace spec")


def _trace_json(trace: Trace) -> str:
    import json

    payload = {
        "name": trace.name,
        "source": trace.source,
        "steps": [
            {
                "action": step.action,
                "method": step.method,
                "task_id": step.task_id,
                "title": step.title,
                "line": step.line,
                "file": step.file,
                "seq": step.post.seq,
                "tasks": [task.__dict__ for task in step.post.tasks],
            }
            for step in trace.steps
        ],
    }
    return json.dumps(payload, indent=2) + "\n"
