# todo-app

## Specification

`spec/TodoTask.tla` is one task from Clew's b-full chart (`examples/todo/b-full.yaml`). TLC checks `TypeOK` and the chart guards as invariants: `TitleNotEmpty` and `CompletedAtSet`.

GitHub Actions runs TLC on pull requests and on `main`. Locally, with Java 21:

```bash
curl -fsSL -o /tmp/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar
java -XX:+UseParallelGC -jar /tmp/tla2tools.jar spec/TodoTask.tla
```

## Execution events

Python 3.12 and newer can record calls, returns, raised exceptions, exits, and coroutine switches with `sys.monitoring`. Each event includes the thread that produced it.

```bash
python -m execution_events
```

```python
import execution_events
from execution_events import sample

with execution_events.capture(files=[sample.__file__]) as events:
    sample.run()
```

Pass `files` to keep events from those source files. Omit it to record the rest of the process.

## Alignment

Those events are calls and returns, not values of `task`. `event_alignment` is the declaration of which returns are spec steps. `todo_app.alignment` maps this task onto `spec/TodoTask.tla`: the action for each step, and the `task` record read from the live object. `submitted` is remembered there; the events do not carry it.

```python
import todo_app.task as tasks
from event_alignment import trace
from todo_app.alignment import todo_alignment

with trace(todo_alignment, files=[tasks.__file__]) as run:
    item = tasks.TodoTask("")
    run.bind(item)
    item.set_title("buy milk")
    item.submit()

mapped = run.mapped()
```
