"""Generate an IssueTrackerTrace module for one recorded trace and run TLC on it.

The module is generated from ``spec/IssueTracker.tla`` and the action map.
A committed step must be an enabled spec action whose result is the
recorded view. A refused step must be a disabled spec action, and the
recorded view must equal the previous one. The constants ``Keys``,
``Users`` and ``MaxIssues`` are set from the trace itself, so the model is
exactly as large as the execution needed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from proof.tracker.mapping import IssueView, View, action_names, binding_for
from proof.tracker.recording import Step, Trace
from proof.tracker.spec import SPEC, invariant_names, next_actions


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
        raise RuntimeError(f"ACTION_MAP does not match Next. mapping={mapped!r} model={model!r}")


def render_call(step: Step) -> str:
    if step.action == "UNMAPPED":
        return f"{step.method}({step.key!r}, {step.number})"
    binding = binding_for(step.action)
    parts: list[str] = [f'"{step.key}"']
    if binding.ref_param is not None:
        parts.append(str(step.number))
    if binding.title_param is not None or binding.user_param is not None:
        parts.append(f'"{step.arg}"')
    if binding.blocker_param is not None:
        parts.append(str(step.num))
    return f"{step.action}({', '.join(parts)})"


def constants(trace: Trace) -> dict[str, object]:
    keys = {step.key for step in trace.steps if step.key}
    users = {step.arg for step in trace.steps if step.action == "Assign" and step.arg}
    issues = 1
    comments = 0
    for step in trace.steps:
        for project in step.post.projects:
            keys.add(project.key)
            issues = max(issues, len(project.issues))
            for issue in project.issues:
                comments = max(comments, issue.comments)
                if issue.assignee != "unset":
                    users.add(issue.assignee)
    return {
        "Keys": tuple(sorted(keys)) or ("ENG",),
        "Users": tuple(sorted(users)) or ("nobody",),
        "MaxIssues": issues,
        "MaxComments": comments,
    }


def module_text(trace: Trace) -> str:
    check_registry()
    if not trace.steps:
        raise RuntimeError(f"{trace.name} has no steps")
    clauses = "\n".join(_clause(name) for name in next_actions())
    return f"""\
---- MODULE IssueTrackerTrace ----
\\* Generated from spec/IssueTracker.tla and proof/tracker/mapping.py. Do not edit.
\\* Trace {trace.name}.
EXTENDS IssueTracker, Sequences

VARIABLE step

IssueView(record, n) ==
  [
    number |-> n,
    title |-> record.title,
    status |-> record.status,
    assignee |-> record.assignee,
    blocked_by |-> record.blocked_by,
    comments |-> record.comments,
    started_at |-> record.started_at,
    completed_at |-> record.completed_at,
    canceled_at |-> record.canceled_at
  ]

View(ps) ==
  {{[key |-> k, issues |-> {{IssueView(ps[k].issues[n], n) : n \\in DOMAIN ps[k].issues}}]
    : k \\in DOMAIN ps}}

Act(name, key, n, arg, num) ==
{clauses}

Steps == <<
{_steps(trace)}
>>

Posts == <<
{_posts(trace)}
>>

Guard(s) ==
  \\/ s.kind = "commit" /\\ ENABLED Act(s.action, s.key, s.n, s.arg, s.num)
  \\/ s.kind = "refused" /\\ ~ENABLED Act(s.action, s.key, s.n, s.arg, s.num)

TraceInit ==
  /\\ Init
  /\\ step = 0

TraceNext ==
  /\\ step < Len(Steps)
  /\\ LET s == Steps[step + 1]
     IN /\\ \\/ /\\ s.kind = "commit"
              /\\ Act(s.action, s.key, s.n, s.arg, s.num)
           \\/ /\\ s.kind = "refused"
              /\\ ~ENABLED Act(s.action, s.key, s.n, s.arg, s.num)
              /\\ UNCHANGED projects
        /\\ View(projects') = Posts[step + 1]
        /\\ step' = step + 1

GuardHolds ==
  \\/ step = Len(Steps)
  \\/ Guard(Steps[step + 1])

TraceAccepted ==
  \\/ step = Len(Steps)
  \\/ ENABLED TraceNext

====
"""


def config_text(trace: Trace) -> str:
    values = constants(trace)
    lines = [
        "CONSTANTS",
        "Keys = {" + ", ".join(f'"{key}"' for key in values["Keys"]) + "}",  # type: ignore[union-attr]
        "Users = {" + ", ".join(f'"{user}"' for user in values["Users"]) + "}",  # type: ignore[union-attr]
        f"MaxIssues = {values['MaxIssues']}",
        f"MaxComments = {values['MaxComments']}",
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


def validate_one(trace: Trace, jar: str, timeout: float = 120) -> Verdict:
    with tempfile.TemporaryDirectory(prefix=f"tracker-trace-{trace.name}-") as directory:
        folder = Path(directory)
        (folder / "IssueTracker.tla").symlink_to(SPEC)
        (folder / "IssueTrackerTrace.tla").write_text(module_text(trace), encoding="utf-8")
        (folder / "IssueTrackerTrace.cfg").write_text(config_text(trace), encoding="utf-8")
        (folder / f"{trace.name}.json").write_text(trace_json(trace), encoding="utf-8")
        (folder / "tmp").mkdir()
        local_jar = folder / "tla2tools.jar"
        shutil.copy(jar, local_jar)
        try:
            completed = subprocess.run(
                [
                    "java",
                    "-XX:+UseParallelGC",
                    # TLC unpacks its standard modules into java.io.tmpdir.
                    # Parallel checkers race there unless each has its own.
                    f"-Djava.io.tmpdir={folder / 'tmp'}",
                    "-jar",
                    str(local_jar),
                    "-workers",
                    "1",
                    "-metadir",
                    str(folder / "meta"),
                    "IssueTrackerTrace.tla",
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
        return Verdict(True, trace.name, None, None, "accepted", f"trace {trace.name} accepted")
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
    return Verdict(False, trace.name, step.line, step.file, kind, _message(trace, step, kind))


def _error(trace: Trace, detail: str) -> Verdict:
    return Verdict(False, trace.name, None, None, "error", f"trace {trace.name} failed: {detail}")


def _message(trace: Trace, step: Step, kind: str) -> str:
    where = f"trace {trace.name}, {step.file} line {step.line}: {render_call(step)}"
    if kind == "guard" and step.kind == "commit":
        return f"{where} taken while the guard was false"
    if kind == "guard":
        return f"{where} refused while the guard was true"
    if kind == "state" and step.kind == "commit":
        return f"{where} taken but the resulting state does not match"
    return f"{where} refused but the state changed"


def _clause(action: str) -> str:
    binding = binding_for(action)
    parts = ["key"]
    if binding.ref_param is not None:
        parts.append("n")
    if binding.title_param is not None or binding.user_param is not None:
        parts.append("arg")
    if binding.blocker_param is not None:
        parts.append("num")
    return f'  \\/ name = "{action}" /\\ {action}({", ".join(parts)})'


def _steps(trace: Trace) -> str:
    rows = []
    for step in trace.steps:
        for text in (step.action, step.key, step.arg, step.kind):
            _plain(text)
        rows.append(
            f'  [action |-> "{step.action}", key |-> "{step.key}", n |-> {step.number}, '
            f'arg |-> "{step.arg}", num |-> {step.num}, kind |-> "{step.kind}", line |-> {step.line}]'
        )
    return ",\n".join(rows)


def _posts(trace: Trace) -> str:
    return ",\n".join(_view(step.post) for step in trace.steps)


def _view(view: View) -> str:
    if not view.projects:
        return "  {}"
    projects = []
    for project in view.projects:
        _plain(project.key)
        if project.issues:
            body = ",\n      ".join(_issue(issue) for issue in project.issues)
            issues = "{\n      " + body + "\n    }"
        else:
            issues = "{}"
        projects.append(f'[key |-> "{project.key}", issues |-> {issues}]')
    return "  {\n    " + ",\n    ".join(projects) + "\n  }"


def _issue(issue: IssueView) -> str:
    for text in (issue.title, issue.status, issue.assignee):
        _plain(text)
    for mark in (issue.started_at, issue.completed_at, issue.canceled_at):
        if mark not in {"set", "unset"}:
            raise RuntimeError(f"mark {mark!r} is not set or unset")
    blocked = "{" + ", ".join(str(number) for number in issue.blocked_by) + "}"
    return (
        f'[number |-> {issue.number}, title |-> "{issue.title}", status |-> "{issue.status}", '
        f'assignee |-> "{issue.assignee}", blocked_by |-> {blocked}, comments |-> {issue.comments}, '
        f'started_at |-> "{issue.started_at}", completed_at |-> "{issue.completed_at}", '
        f'canceled_at |-> "{issue.canceled_at}"]'
    )


def _plain(value: str) -> None:
    if not _SAFE_TEXT.fullmatch(value):
        raise RuntimeError(f"refusing to embed {value!r} in a trace spec")


def trace_json(trace: Trace) -> str:
    payload = {
        "name": trace.name,
        "source": trace.source,
        "skipped": trace.skipped,
        "steps": [
            {
                "action": step.action,
                "key": step.key,
                "number": step.number,
                "arg": step.arg,
                "num": step.num,
                "kind": step.kind,
                "method": step.method,
                "file": step.file,
                "line": step.line,
                "post": [
                    {"key": project.key, "issues": [issue.__dict__ for issue in project.issues]}
                    for project in step.post.projects
                ],
            }
            for step in trace.steps
        ],
    }
    return json.dumps(payload, indent=2) + "\n"
