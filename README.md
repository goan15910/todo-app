# todo-app

A task repository. Tasks move through draft, todo, in progress, snoozed, done, archived, and deleted.

## Layout

- `todo_app/domain` holds the task and the status rules.
- `todo_app/application` holds the use cases and the repository port.
- `todo_app/adapters` holds the in-memory repository and the sequential id allocator.
- `spec/TodoTask.tla` is the same lifecycle for a repository of at most `MaxTasks` tasks, plus get-by-id and list-by-status.
- `proof/` holds the human-owned mapping, the generated trace check, and the Hypothesis machine.

## The tracker slice

`tracker/` is the second slice: an issue tracker whose ports are specified by `spec/IssueTracker.tla`. `docs/tracker-poc.md` is the design. The layout follows *Architecture Patterns with Python*, Part 1:

- `tracker/domain` holds the `Project` aggregate, its `Issue` entities, and the rules.
- `tracker/service_layer` holds one use case per spec action, the `AbstractUnitOfWork` and repository ports, and the service-layer errors.
- `tracker/adapters/memory.py` holds the in-memory repository and a unit of work that really rolls back.
- `tracker/adapters/sqlalchemy` holds the schema, a repository that reconstitutes a project from rows and writes it back with an optimistic version check, and a unit of work over one connection and one transaction.
- `tracker/entrypoints/fastapi_app.py` is the HTTP adapter. `uvicorn tracker.entrypoints.main:app` serves it over `tracker.db`.
- `tracker/bootstrap.py` wires either stack: `in_memory()` or `sqlite(url)`.

`tests/test_tracker_adapters.py` runs one unit-of-work contract against both adapters. `tests/test_tracker_http.py` drives every route over SQLite. Dependencies are pinned in `requirements.txt`.

### Proving the tracker against its spec

`proof/tracker` records traces at the ports and checks them with TLC. Nothing in `tracker/` imports it.

- `recording.py` holds the two proving adapters. `RecordingServices` has the service layer's names and labels each command with its spec action. `RecordingUnitOfWork` wraps any real unit of work and, when it ends, reads the repository back in a fresh unit of work and records whether the command committed or was refused.
- `mapping.py` is the human-owned abstraction, pinned by `MAPPING.sha256`.
- `trace.py` generates `IssueTrackerTrace.tla` for one trace. A committed step must be an enabled spec action with the recorded result; a refused step must be a disabled action with an unchanged state. `Keys`, `Users` and `MaxIssues` are set from the trace.
- `scenarios.py` runs four readable walks through three stacks: the fake, SQLite through the service layer, and SQLite through the FastAPI app. `explore.py` is a Hypothesis machine that draws among the commands the spec enables in the current state, and sometimes any command at all.
- `negatives.py` holds six wrong mappings that must get stuck at the recorded line: a dropped label, a wrong name, a wrong parameter, a refusal recorded as a commit, a commit recorded as a refusal, and a wrong post-state.

```bash
TLC_JAR=/tmp/tla2tools.jar python tools/validate_tracker_traces.py
```

That records 12 scenario traces and 40 Hypothesis traces, 2685 steps covering all 14 actions committed, all 14 refused, all 11 status edges, and 839 abstract states, checks every trace with TLC, and then checks the negatives. It takes about 40 seconds here. `tests/test_tracker_trace_validation.py` runs a subset and skips without `TLC_JAR`.

`tests/test_tracker_services.py` drives every rule through the service layer. The mutation job covers `tracker/domain` and `tracker/service_layer`; adapters and entrypoints are excluded from mutation and covered by their own tests.

## Tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```

That run does not need Java. It needs the pinned dependencies for the tracker slice and Hypothesis for both bounded state machines. `tests/test_lifecycle_scenarios.py` is a few lifecycle scenarios produced from the TLC state graph. Each docstring names a trace id from `tests/model/traces.json`. Regenerate both after a spec change:

```bash
curl -fsSL -o /tmp/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar
TLC_JAR=/tmp/tla2tools.jar python tools/generate_model_tests.py
```

`TLC_JAR=/tmp/tla2tools.jar python tools/generate_model_tests.py --check` fails if those files drift. It does not rewrite them. The TLC job also replays every state-graph edge in a temporary directory; that replay is not committed.

## What the model checks, and what the code checks

TLC on `spec/TodoTask.tla` with `MaxTasks = 2` is the whole design: 12872 states generated, 3423 distinct, depth 19. There is no liveness property and no environment variable. Titles and wake times are action parameters. Datetimes in the code collapse to `set` or `unset`.

The five readable scenarios cover all 14 actions and all 13 status edges, and 15 abstract states. They drop empty titles, a second task that does more than sit as a draft, and the rest of those 3423 states. Further executions of the in-memory fake are the Hypothesis machine in `proof/explore.py` (30 examples, 18 steps, `derandomize`, bounded by `MaxTasks`). Its traces cover 86 abstract states. Together with the five scenarios the recorded traces cover 94, including empty titles. `TLC_JAR=/tmp/tla2tools.jar python tools/validate_traces.py` checks every one of those traces and prints the coverage. The other model states are not executed code. Trace validation plus that report is the conformance claim, not a proof over all executions.

`proof/mapping.py` is the human-owned `abstract` and action map, pinned by `proof/MAPPING.sha256`. `submitted` is not in the view. The trace spec keeps it as history by taking the model's own actions. `seq` is derived from the stored ids. The id allocator is a port the mapping does not read.

A stuck trace names the trace, the file, and the line, for example `trace wrong-name, proof/negatives.py line 41: TodoStart(1) taken while the guard was false`. The plain unit-test job does not run TLC. `tests/test_trace_validation.py` skips unless `TLC_JAR` is set.

## Specification

```bash
java -XX:+UseParallelGC -jar /tmp/tla2tools.jar -metadir tlc-meta spec/TodoTask.tla
```

The bound is `MaxTasks` in `spec/TodoTask.cfg`.

## Mutation

`mutmut` edits `todo_app` and reruns the unit tests. It does not edit the spec.

```bash
python -m pip install mutmut==3.8.0 "pytest==8.3.5"
python -m mutmut run
```

`mutmut` runs the same tests through its own runner. A mutant that is not killed fails the mutation job.
