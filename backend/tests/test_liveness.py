"""The worker's liveness probe: the heartbeat files the loops write, and the exact command the chart runs on them."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from conferenceops.worker.liveness import Liveness

CHART_TEMPLATE = Path(__file__).resolve().parents[2] / "deploy" / "chart" / "templates" / "app.yaml"


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_a_loop_on_time_passes(tmp_path):
    clock = Clock(1000.0)
    liveness = Liveness(tmp_path, now=clock)
    liveness.beat("import", due_in=60)

    clock.now = 1060.0  # exactly on time
    assert liveness.check() == []
    assert (tmp_path / "import").read_text() == "1060\n"


def test_a_loop_past_its_promised_beat_fails_with_how_late(tmp_path):
    clock = Clock(1000.0)
    liveness = Liveness(tmp_path, now=clock)
    liveness.beat("import", due_in=60)

    clock.now = 1075.0
    assert liveness.check() == ["import: 15 s late"]


def test_one_late_loop_fails_even_when_the_others_are_on_time(tmp_path):
    clock = Clock(1000.0)
    liveness = Liveness(tmp_path, now=clock)
    liveness.beat("import", due_in=1800)
    liveness.beat("indexing", due_in=5)

    clock.now = 1010.0
    assert liveness.check() == ["indexing: 5 s late"]


def test_no_beat_yet_fails(tmp_path):
    assert Liveness(tmp_path / "never-created").check() == ["no loop has beaten yet"]
    assert Liveness(tmp_path).check() == ["no loop has beaten yet"]  # the directory exists, no loop has written


def test_a_beat_replaces_the_promise_and_leaves_no_draft_behind(tmp_path):
    clock = Clock(1000.0)
    liveness = Liveness(tmp_path, now=clock)
    liveness.beat("import", due_in=10)
    clock.now = 1020.0
    assert liveness.check() == ["import: 10 s late"]

    liveness.beat("import", due_in=10)

    assert liveness.check() == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["import"]


def test_an_unreadable_heartbeat_fails_rather_than_passes(tmp_path):
    (tmp_path / "import").write_text("soon\n")
    assert Liveness(tmp_path).check() == ["import: unreadable heartbeat"]


def chart_probe_command() -> list[str]:
    """The exec command of the worker's liveness probe, read from the chart template."""
    match = re.search(r"livenessProbe:\n\s+exec:\n\s+command: (\[.*\])", CHART_TEMPLATE.read_text())
    assert match, "the chart has no exec liveness probe"
    return json.loads(match.group(1))


def test_the_probe_command_the_chart_runs_exits_0_when_on_time_and_1_when_late_or_missing(tmp_path):
    # The same module and arguments as the chart; only the interpreter is this test's, where the chart's `python` is
    # the image's venv. The directory comes from the environment, as a Kubernetes container would get it.
    command = chart_probe_command()
    assert command[0] == "python"
    env = {**os.environ, "WORKER_HEARTBEAT_DIR": str(tmp_path)}

    def probe() -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *command[1:]], env=env, capture_output=True, text=True, check=False)  # noqa: S603 - the chart's own command, on a test directory

    missing = probe()
    assert (missing.returncode, missing.stdout) == (1, "no loop has beaten yet\n")

    Liveness(tmp_path).beat("import", due_in=60)
    assert probe().returncode == 0

    (tmp_path / "import").write_text("1\n")  # a promise long past
    late = probe()
    assert late.returncode == 1
    assert re.fullmatch(r"import: \d+ s late\n", late.stdout)
