# todo-app

A task repository. Tasks move through draft, todo, in progress, snoozed, done, archived, and deleted.

## Layout

- `todo_app/domain` holds the task and the status rules.
- `todo_app/application` holds the use cases and the repository port.
- `todo_app/adapters` holds the in-memory repository.
- `spec/TodoTask.tla` is the same lifecycle for a repository of at most `MaxTasks` tasks, plus get-by-id and list-by-status.

## Tests

```bash
python -m unittest discover -s tests
```

That run does not need Java. `tests/test_model_generated.py` is produced from the TLC state graph and then runs on its own. Regenerate it after a spec change:

```bash
curl -fsSL -o /tmp/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar
TLC_JAR=/tmp/tla2tools.jar python tools/generate_model_tests.py
```

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
