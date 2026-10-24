"""Print events captured from :mod:`execution_events.sample`."""

from __future__ import annotations

import execution_events.sample as sample
from execution_events import capture
from execution_events.sample import run


def main() -> None:
    with capture(files=[sample.__file__]) as events:
        run()
    for event in events:
        print(event)


if __name__ == "__main__":
    main()
