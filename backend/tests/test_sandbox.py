"""The code sandbox: its restrictions, and the run flow around them."""

from __future__ import annotations

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.sandbox.docker import DockerSandbox, RunSpec, tail
from app.services.sandbox.workspace import build_archive, materialize

# Stands in for the docker CLI. `run` executes the script in the mounted
# workspace directly, so the flow can be tested where Docker is absent; the
# isolation itself comes from the real flags, asserted separately below.
FAKE_DOCKER = r"""#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
case "$1" in
  version) echo "27.0.0-fake"; exit 0 ;;
  kill) exit 0 ;;
  run)
    shift
    vol=""
    while [ $# -gt 4 ]; do
      if [ "$1" = "--volume" ]; then vol="$2"; fi
      shift
    done
    if [ -n "$FAKE_DOCKER_EMPTY_MOUNT" ]; then mkdir -p "$FAKE_DOCKER_EMPTY_MOUNT"; vol="$FAKE_DOCKER_EMPTY_MOUNT"; fi
    cd "${vol%%:*}" && exec sh -c "$(printf '%s' "$4" | sed 's#/workspace/#./#g')"
    ;;
esac
exit 1
"""


def _config(**overrides) -> Settings:
    import tempfile

    return Settings(
        **{
            "secret_key": "x" * 40,
            "sandbox_workdir_root": tempfile.gettempdir(),
            **overrides,
        }
    )


@pytest.fixture
def fake_docker(tmp_path, monkeypatch) -> Path:
    binary = tmp_path / "docker"
    binary.write_text(FAKE_DOCKER)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    return binary


class TestRestrictions:
    def _cmd(self, **config) -> list[str]:
        return DockerSandbox(_config(**config)).command(
            name="n", image="python:3.13-slim", workdir="/tmp/w",
            script="python -m pytest -q", runtime="python", network=False,
        )  # fmt: skip

    def test_tests_run_without_network(self) -> None:
        cmd = self._cmd()
        assert cmd[cmd.index("--network") + 1] == "none"

    def test_container_is_locked_down(self) -> None:
        cmd = " ".join(self._cmd())
        for flag in (
            "--rm",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--read-only",
            "--user 1000:1000",
            "--memory 1g",
            "--memory-swap 1g",
            "--pids-limit 256",
            "--volume /tmp/w:/workspace:rw",
        ):
            assert flag in cmd, flag

    def test_limits_come_from_settings(self) -> None:
        cmd = " ".join(self._cmd(sandbox_memory="256m", sandbox_pids_limit=64))
        assert "--memory 256m" in cmd and "--pids-limit 64" in cmd

    def test_python_installs_into_the_workspace(self) -> None:
        # The root filesystem is read-only, so packages must land in /workspace.
        assert "PIP_TARGET=/workspace/.deps" in self._cmd()

    def test_the_script_runs_only_after_the_mount_is_proven(self) -> None:
        shell, flag, script = self._cmd()[-3:]
        assert (shell, flag) == ("sh", "-c")
        assert script.startswith("test -f /workspace/.evergent-workspace ||")
        assert script.endswith("; python -m pytest -q")


class TestWorkspace:
    def test_materialize_writes_nested_files(self, tmp_path) -> None:
        materialize([("src/app.py", "x = 1"), ("README.md", "hi")], tmp_path)
        assert (tmp_path / "src" / "app.py").read_text() == "x = 1"

    @pytest.mark.parametrize("path", ["../escape.py", "a/../../escape.py", ""])
    def test_paths_cannot_escape(self, tmp_path, path) -> None:
        with pytest.raises(ValueError):
            materialize([(path, "boom")], tmp_path)

    def test_absolute_paths_stay_inside(self, tmp_path) -> None:
        materialize([("/etc/evergent-test", "x")], tmp_path)
        assert (tmp_path / "etc" / "evergent-test").exists()

    def test_archive_holds_every_file_under_one_folder(self) -> None:
        blob = build_archive([("a.py", "1"), ("pkg/b.py", "2")], folder="proj")
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert sorted(archive.namelist()) == ["proj/a.py", "proj/pkg/b.py"]
            assert archive.read("proj/pkg/b.py") == b"2"

    def test_tail_keeps_the_end(self) -> None:
        cut = tail("a" * 50 + "THE END", limit=10)
        assert cut.endswith("THE END") and "cut" in cut


class TestRuns:
    async def test_missing_runtime_is_reported_not_raised(self) -> None:
        ok, reason = await DockerSandbox(
            _config(sandbox_docker_bin="definitely-not-docker-xyz")
        ).available()
        assert not ok and "not installed" in reason

    async def test_disabled_sandbox_says_so(self) -> None:
        ok, reason = await DockerSandbox(_config(sandbox_enabled=False)).available()
        assert not ok and "disabled" in reason

    async def test_passing_tests(self, fake_docker) -> None:
        sandbox = DockerSandbox(_config(sandbox_docker_bin=str(fake_docker)))
        assert (await sandbox.available())[0]

        result = await sandbox.run(
            [("check.sh", "test -f check.sh && echo all good")],
            RunSpec(runtime="python", command="sh check.sh"),
        )
        assert result.passed, result.output
        assert "all good" in result.output

    async def test_failing_tests_report_the_exit_code(self, fake_docker) -> None:
        sandbox = DockerSandbox(_config(sandbox_docker_bin=str(fake_docker)))
        result = await sandbox.run([], RunSpec(runtime="node", command="echo nope; exit 3"))
        assert not result.passed
        assert result.exit_code == 3 and result.stage == "test"
        assert "nope" in result.output

    async def test_install_runs_first_with_network_then_tests_offline(
        self, fake_docker
    ) -> None:
        sandbox = DockerSandbox(_config(sandbox_docker_bin=str(fake_docker)))
        result = await sandbox.run(
            [],
            RunSpec(runtime="python", install="echo installed > dep.txt", command="cat dep.txt"),
        )
        assert result.passed and "installed" in result.output

        runs = [
            line for line in Path(os.environ["FAKE_DOCKER_LOG"]).read_text().splitlines()
            if line.startswith("run ")
        ]
        assert "--network bridge" in runs[0]
        assert "--network none" in runs[1]

    async def test_a_failed_install_stops_before_the_tests(self, fake_docker) -> None:
        sandbox = DockerSandbox(_config(sandbox_docker_bin=str(fake_docker)))
        result = await sandbox.run(
            [], RunSpec(runtime="python", install="exit 7", command="echo never")
        )
        assert result.stage == "install" and result.exit_code == 7
        assert "never" not in result.output.split("$ echo never")[0]

    async def test_install_can_be_kept_offline(self, fake_docker) -> None:
        sandbox = DockerSandbox(
            _config(sandbox_docker_bin=str(fake_docker), sandbox_install_network=False)
        )
        await sandbox.run([], RunSpec(runtime="python", install="true", command="true"))
        log = Path(os.environ["FAKE_DOCKER_LOG"]).read_text()
        assert "--network bridge" not in log

    async def test_a_hung_run_is_killed(self, fake_docker) -> None:
        sandbox = DockerSandbox(
            _config(sandbox_docker_bin=str(fake_docker), sandbox_timeout_seconds=1)
        )
        result = await sandbox.run([], RunSpec(runtime="python", command="sleep 30"))
        assert result.timed_out and result.exit_code is None
        # The container itself was told to stop, not just the client.
        assert "kill evergent-test-" in Path(os.environ["FAKE_DOCKER_LOG"]).read_text()

    async def test_unknown_runtime_is_the_testers_mistake(self, fake_docker) -> None:
        sandbox = DockerSandbox(_config(sandbox_docker_bin=str(fake_docker)))
        result = await sandbox.run([], RunSpec(runtime="cobol", command="true"))
        assert not result.passed and "Unknown runtime" in result.output
        assert not result.infrastructure_failed

    async def test_an_empty_mount_can_never_pass(
        self, fake_docker, tmp_path, monkeypatch
    ) -> None:
        """Regression, found on real Docker.

        Colima does not share the macOS temp dir with its VM, so the workspace
        mounted as an empty folder — and a test command over nothing exited 0.
        """
        monkeypatch.setenv("FAKE_DOCKER_EMPTY_MOUNT", str(tmp_path / "empty"))
        sandbox = DockerSandbox(_config(sandbox_docker_bin=str(fake_docker)))
        result = await sandbox.run([("test_app.py", "x")], RunSpec("node", "true"))
        assert not result.passed
        assert result.infrastructure_failed
        assert "workspace not mounted" in result.output


class TestRuntimeDown:
    async def test_a_stopped_runtime_is_never_reported_as_failing_code(self) -> None:
        """Regression, found when Colima was stopped: `docker run` then exits 1,
        the same code as a failing test, so run_code said "command failed"."""
        sandbox = DockerSandbox(_config(sandbox_docker_bin="definitely-not-docker-xyz"))
        result = await sandbox.run([("a.py", "print(1)")], RunSpec("python", "python a.py"))
        assert result.infrastructure_failed
        assert result.exit_code is None and "not installed" in result.output
