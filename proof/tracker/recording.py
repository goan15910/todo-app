"""The proving adapters: a recording unit of work and a recording service facade.

Neither is imported by ``tracker``. The facade stands where an entrypoint
calls the service layer and labels each command with its spec action. The
unit of work stands where the service layer reaches storage and reports,
when a unit of work ends, whether it committed and what the repository then
holds, read back through the port in a fresh unit of work.

A step is one command whose unit of work ended. A call that never opened
one (a ref that does not parse, a blocker from another project) is not a
step; the spec cannot express it. A command that ends two units of work, or
a committed command with no unit of work, is an error in the core, not a
step.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any

from proof.tracker.mapping import ActionBinding, Label, View, abstract, binding_for_method
from proof.tracker.spec import ROOT
from tracker.bootstrap import UnitOfWorkFactory
from tracker.domain.errors import TrackerError
from tracker.service_layer import services as default_services
from tracker.service_layer.errors import InvalidRef
from tracker.service_layer.unit_of_work import AbstractUnitOfWork


@dataclass(frozen=True)
class Step:
    action: str
    key: str
    number: int
    arg: str
    num: int
    kind: str  # "commit" or "refused"
    method: str
    file: str
    line: int
    post: View


@dataclass(frozen=True)
class Trace:
    name: str
    source: str
    steps: tuple[Step, ...]
    skipped: int = 0  # commands that never opened a unit of work


@dataclass(frozen=True)
class Patch:
    """A deliberately wrong mapping, used to show a stuck trace."""

    drop: frozenset[str] = frozenset()
    rename: tuple[tuple[str, str], ...] = ()
    force_number: tuple[tuple[str, int], ...] = ()
    force_kind: tuple[tuple[str, str], ...] = ()

    def apply(self, method: str, label: Label, kind: str) -> tuple[Label, str]:
        action = label.action
        if method in self.drop:
            action = "UNMAPPED"
        for item, forced in self.rename:
            if item == method:
                action = forced
        number = label.number
        for item, forced in self.force_number:
            if item == method:
                number = forced
        for item, forced in self.force_kind:
            if item == method:
                kind = forced
        return Label(action, label.key, number, label.arg, label.num), kind


@dataclass
class _Outcome:
    committed: bool
    post: View


class Recorder:
    """Collects steps. Shared by one facade and its unit-of-work factory."""

    def __init__(self, patch: Patch | None = None) -> None:
        self.steps: list[Step] = []
        self.skipped = 0
        self.patch = patch
        self._lock = threading.Lock()
        self._outcomes: list[_Outcome] | None = None

    def to_trace(self, name: str, source: str) -> Trace:
        return Trace(name=name, source=source, steps=tuple(self.steps), skipped=self.skipped)

    def _begin(self) -> None:
        with self._lock:
            if self._outcomes is not None:
                raise RuntimeError("a command is already being recorded")
            self._outcomes = []

    def _end(self) -> list[_Outcome]:
        with self._lock:
            outcomes = self._outcomes
            self._outcomes = None
        if outcomes is None:
            raise RuntimeError("no command was being recorded")
        return outcomes

    def _finished(self, outcome: _Outcome) -> None:
        with self._lock:
            if self._outcomes is not None:
                self._outcomes.append(outcome)


class RecordingUnitOfWork(AbstractUnitOfWork):
    """Wraps one real unit of work and reports how it ended."""

    def __init__(self, inner: AbstractUnitOfWork, factory: RecordingUnitOfWorkFactory) -> None:
        self._inner = inner
        self._factory = factory
        self._commits = 0

    @property
    def projects(self):  # type: ignore[override]
        return self._inner.projects

    def __enter__(self) -> RecordingUnitOfWork:
        self._inner.__enter__()
        self._commits = 0
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._factory.finished(self._commits)

    def _commit(self) -> None:
        self._inner.commit()
        self._commits += 1

    def rollback(self) -> None:
        self._inner.rollback()


class RecordingUnitOfWorkFactory:
    """A unit-of-work factory that wraps another and snapshots after each one."""

    def __init__(self, inner: UnitOfWorkFactory, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __call__(self) -> RecordingUnitOfWork:
        return RecordingUnitOfWork(self._inner(), self)

    def snapshot(self) -> View:
        with self._inner() as uow:
            return abstract(uow.projects.list())

    def finished(self, commits: int) -> None:
        if commits > 1:
            raise RuntimeError("a unit of work committed more than once")
        self._recorder._finished(_Outcome(committed=commits == 1, post=self.snapshot()))


class RecordingServices:
    """Has the service layer's names. Commands become steps; reads pass through.

    A command may be called with a unit of work from ``uow_factory``, as an
    entrypoint does, or without one, as a scenario does; the facade then
    supplies one. Any other unit of work ends unobserved and the call is
    reported as a command that finished without a unit of work.
    """

    def __init__(
        self,
        uow_factory: RecordingUnitOfWorkFactory,
        recorder: Recorder,
        services: ModuleType | Any = default_services,
    ) -> None:
        self.uow_factory = uow_factory
        self._recorder = recorder
        self._services = services

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._services, name)
        binding = binding_for_method(name)
        if binding is None or not callable(target):
            return target
        return self._wrap(name, binding, target)

    def _wrap(self, name: str, binding: ActionBinding, target: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(target)

        uow_index = list(signature.parameters).index("uow")

        def command(*args: Any, **kwargs: Any) -> Any:
            if len(args) <= uow_index and "uow" not in kwargs:
                kwargs["uow"] = self.uow_factory()
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            label = binding.label(bound)
            file, line = _caller()
            self._recorder._begin()
            try:
                result = target(*bound.args, **bound.kwargs)
            except TrackerError as error:
                outcomes = self._recorder._end()
                self._record(name, label, outcomes, file, line, error)
                raise
            except BaseException:
                self._recorder._end()
                raise
            outcomes = self._recorder._end()
            self._record(name, label, outcomes, file, line, None)
            return result

        command.__name__ = name
        command.__wrapped__ = target  # type: ignore[attr-defined]
        return command

    def _record(
        self,
        method: str,
        label: Label | None,
        outcomes: list[_Outcome],
        file: str,
        line: int,
        error: TrackerError | None,
    ) -> None:
        if not outcomes:
            if error is None or not isinstance(error, InvalidRef):
                raise RuntimeError(f"{method} finished without a unit of work")
            self._recorder.skipped += 1
            return
        if len(outcomes) != 1:
            raise RuntimeError(f"{method} ended {len(outcomes)} units of work")
        outcome = outcomes[0]
        if error is None and not outcome.committed:
            raise RuntimeError(f"{method} returned without committing")
        if error is not None and outcome.committed:
            raise RuntimeError(f"{method} committed and then raised {error!r}")
        if label is None:
            raise RuntimeError(f"{method} opened a unit of work with arguments the mapping cannot label")
        kind = "commit" if outcome.committed else "refused"
        if self._recorder.patch is not None:
            label, kind = self._recorder.patch.apply(method, label, kind)
        self._recorder.steps.append(
            Step(
                action=label.action,
                key=label.key,
                number=label.number,
                arg=label.arg,
                num=label.num,
                kind=kind,
                method=method,
                file=file,
                line=line,
                post=outcome.post,
            )
        )


def recording_stack(
    inner: UnitOfWorkFactory,
    patch: Patch | None = None,
) -> tuple[RecordingServices, Recorder]:
    """A facade and its recorder over any unit-of-work factory."""

    recorder = Recorder(patch)
    factory = RecordingUnitOfWorkFactory(inner, recorder)
    return RecordingServices(factory, recorder), recorder


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
