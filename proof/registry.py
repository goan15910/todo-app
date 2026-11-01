"""Bind use cases to the action map and record one spec step per call.

The action name for a fan-out method comes from the task's status before
the call. Parameters come from ``inspect.signature().bind``.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from proof.mapping import (
    ACTION_MAP,
    ActionBinding,
    View,
    abstract,
    abstract_title,
    binding_for_method,
)
from proof.spec import ROOT


_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Step:
    action: str
    method: str
    task_id: int
    title: str
    line: int
    file: str
    post: View


@dataclass(frozen=True)
class Trace:
    name: str
    source: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Patch:
    """A deliberately wrong mapping, used to show a stuck trace."""

    drop: frozenset[str] = frozenset()
    rename: tuple[tuple[str, str], ...] = ()
    force_task_id: tuple[tuple[str, int], ...] = ()
    force_title: tuple[tuple[str, str], ...] = ()

    def name_for(self, method: str, resolved: str | None) -> str | None:
        if method in self.drop:
            return None
        for item, forced in self.rename:
            if item == method:
                return forced
        return resolved

    def task_id_for(self, method: str, task_id: int) -> int:
        for item, forced in self.force_task_id:
            if item == method:
                return forced
        return task_id

    def title_for(self, method: str, title: str) -> str:
        for item, forced in self.force_title:
            if item == method:
                return forced
        return title


class Recorder:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def to_trace(self, name: str, source: str) -> Trace:
        return Trace(name=name, source=source, steps=tuple(self.steps))


_recorder: ContextVar[Recorder | None] = ContextVar("todo_trace_recorder", default=None)
_patch: ContextVar[Patch | None] = ContextVar("todo_trace_patch", default=None)


@contextmanager
def recording(patch: Patch | None = None) -> Iterator[Recorder]:
    recorder = Recorder()
    recorder_token = _recorder.set(recorder)
    patch_token = _patch.set(patch)
    try:
        yield recorder
    finally:
        _recorder.reset(recorder_token)
        _patch.reset(patch_token)


def activate(recorder: Recorder) -> tuple[object, object]:
    return (_recorder.set(recorder), _patch.set(None))


def deactivate(tokens: tuple[object, object]) -> None:
    _recorder.reset(tokens[0])  # type: ignore[arg-type]
    _patch.reset(tokens[1])  # type: ignore[arg-type]


def tla_action(method: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Register ``method`` from the action map. The name is not chosen here."""

    binding = binding_for_method(method)
    if binding is None:
        raise RuntimeError(f"{method} is not in ACTION_MAP")

    def decorate(fn: Callable[..., object]) -> Callable[..., object]:
        signature = inspect.signature(fn)

        def wrapper(self: object, *args: object, **kwargs: object) -> object:
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            recorder = _recorder.get()
            if recorder is None:
                return fn(*bound.args, **bound.kwargs)
            resolved = _resolve(self, binding, bound)
            patch = _patch.get()
            task_id = _task_id(binding, bound)
            title = _title(binding, bound)
            if patch is not None:
                resolved = patch.name_for(method, resolved)
                task_id = patch.task_id_for(method, task_id)
                title = patch.title_for(method, title)
            result = fn(*bound.args, **bound.kwargs)
            file, line = _caller()
            action = resolved if resolved is not None else "UNMAPPED"
            if not _IDENTIFIER.fullmatch(action):
                raise RuntimeError(f"action name {action!r} is not a TLA+ identifier")
            recorder.steps.append(
                Step(
                    action=action,
                    method=method,
                    task_id=task_id,
                    title=title,
                    line=line,
                    file=file,
                    post=abstract(self._repository),  # type: ignore[attr-defined]
                )
            )
            return result

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        wrapper.__signature__ = signature  # type: ignore[attr-defined]
        return wrapper

    return decorate


def bind(service: type) -> None:
    """Wrap each mapped use case. Call once, when the class is defined."""

    for binding in ACTION_MAP:
        original = getattr(service, binding.method)
        setattr(service, binding.method, tla_action(binding.method)(original))


def _resolve(self: object, binding: ActionBinding, bound: inspect.BoundArguments) -> str | None:
    if isinstance(binding.action, str):
        return binding.action
    if binding.id_param is None:
        return None
    task_id = bound.arguments[binding.id_param]
    stored = self._repository.get(task_id)  # type: ignore[attr-defined]
    status = None if stored is None else stored.status.value
    return binding.resolve(status)


def _task_id(binding: ActionBinding, bound: inspect.BoundArguments) -> int:
    if binding.id_param is None:
        return 0
    task_id = bound.arguments[binding.id_param]
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        return 0
    return task_id


def _title(binding: ActionBinding, bound: inspect.BoundArguments) -> str:
    if binding.title_param is None:
        return ""
    return abstract_title(bound.arguments[binding.title_param])  # type: ignore[arg-type]


def _caller() -> tuple[str, int]:
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
    line = caller.f_lineno if caller is not None else 0
    filename = caller.f_code.co_filename if caller is not None else ""
    del frame, caller
    path = Path(filename)
    try:
        relative = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        relative = path.as_posix()
    return relative, line
