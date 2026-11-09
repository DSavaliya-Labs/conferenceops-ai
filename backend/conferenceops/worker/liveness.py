"""The worker's liveness, proven by the loops that do the work.

The worker serves no HTTP port, so each of its loops writes a file under `/tmp/worker-heartbeat` (an emptyDir in the
chart: the root filesystem is read-only) before its first iteration and after every one. The file holds the time by
which the loop will write it again: the coming wait plus a budget for one iteration. The chart's exec liveness probe
runs `python -m conferenceops.worker.liveness`, which exits 1 when a loop is past that time or no loop has beaten yet.
It reads only these files, never PostgreSQL, Elasticsearch or OpenAI (K8S-1).

A loop whose work hangs stops beating, which is the point: a separate task or thread would keep beating while the
work is stuck. A loop whose work fails keeps beating, because it is retried and a restart wouldn't help it."""

import sys
import time
from collections.abc import Callable
from pathlib import Path

from conferenceops.settings import settings


class Liveness:
    """The heartbeat files of one worker process, under `directory`."""

    def __init__(self, directory: Path, now: Callable[[], float] = time.time) -> None:
        self.directory = directory
        self.now = now

    def beat(self, name: str, due_in: float) -> None:
        """Record that loop `name` is alive and will beat again within `due_in` seconds."""
        self.directory.mkdir(parents=True, exist_ok=True)
        draft = self.directory / f".{name}.tmp"
        draft.write_text(f"{self.now() + due_in:.0f}\n")
        draft.replace(self.directory / name)  # one rename, so the probe never reads a half-written file

    def check(self) -> list[str]:
        """Why the worker is not live: the loops past their promised beat, with how late, or that no loop has beaten
        yet. Empty when every loop is on time."""
        if not self.directory.is_dir():
            return ["no loop has beaten yet"]
        files = sorted(path for path in self.directory.iterdir() if path.is_file() and not path.name.startswith("."))
        if not files:
            return ["no loop has beaten yet"]
        now = self.now()
        problems = []
        for path in files:
            try:
                due = float(path.read_text())
            except ValueError:
                problems.append(f"{path.name}: unreadable heartbeat")
            else:
                if now > due:
                    problems.append(f"{path.name}: {now - due:.0f} s late")
        return problems


def main() -> int:
    """The liveness probe: exit 0 when every loop is on time, 1 otherwise, naming the late loops in the pod's events."""
    problems = Liveness(Path(settings().worker_heartbeat_dir)).check()
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
