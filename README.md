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
