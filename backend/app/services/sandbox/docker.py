"""Running model-written code, only ever inside a throwaway container.

Each run gets a fresh copy of the workspace in a temporary directory, mounted
into a container that has no network, no Linux capabilities, a read-only root
filesystem, capped memory, CPU and process count, and a non-root user. The
container is removed afterwards and the directory deleted.

The one exception to "no network" is the optional install step, because
dependencies have to come from somewhere. It runs in its own container before
the tests, and can be locked down too with SANDBOX_INSTALL_NETWORK=false.

There is deliberately no fallback that runs code directly on the host. If no
container runtime is available, tests are reported as not run.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.services.sandbox.workspace import materialize

log = get_logger(__name__)

# Enough to diagnose a failure; a model reading the feedback needs the end of
# the log far more than the start.
MAX_OUTPUT_CHARS = 12_000

WORKDIR = "/workspace"

# Written into every workspace and checked inside the container before
# anything runs. If the runtime cannot see the host directory it mounts an
# EMPTY one instead, without any error — and a test command over an empty
# workspace can "pass" by running nothing. The check makes that impossible.
SENTINEL = ".evergent-workspace"
MOUNT_FAILED = 125

# Per-runtime environment. Dependencies install into the workspace itself,
# because the container's root filesystem is read-only.
RUNTIME_ENV: dict[str, dict[str, str]] = {
    "python": {
        "PIP_TARGET": f"{WORKDIR}/.deps",
        "PYTHONPATH": f"{WORKDIR}/.deps:{WORKDIR}",
        "PATH": f"{WORKDIR}/.deps/bin:/usr/local/bin:/usr/bin:/bin",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    },
    "node": {
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NPM_CONFIG_FUND": "false",
    },
}


# Who each runtime runs as. Never root. Postgres needs a named account,
# because initdb refuses a user id it cannot look up.
RUNTIME_USER: dict[str, str] = {"postgres": "postgres"}
DEFAULT_USER = "1000:1000"


@dataclass(slots=True)
class RunSpec:
    runtime: str
    command: str
    install: str | None = None
    # Shown in the output log instead of the command, when the command is
    # internal plumbing rather than something the caller wrote.
    label: str | None = None


@dataclass(slots=True)
class SandboxResult:
    # None when the run never finished: a timeout or a runtime failure.
    exit_code: int | None
    output: str
    duration_ms: int
    timed_out: bool = False
    stage: str = "test"

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    @property
    def infrastructure_failed(self) -> bool:
        """The tests never ran because of the environment, not the tests."""
        return self.stage == "setup"


def tail(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters cut ...]\n{text[-limit:]}"


class DockerSandbox:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    async def available(self) -> tuple[bool, str]:
        """Whether a container runtime is reachable, and if not, why."""
        if not self.config.sandbox_enabled:
            return False, "sandbox disabled (SANDBOX_ENABLED=false)"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.config.sandbox_docker_bin,
                "version",
                "--format",
                "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            return False, f"'{self.config.sandbox_docker_bin}' is not installed"
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except TimeoutError:
            proc.kill()
            return False, "container runtime did not respond"
        if proc.returncode != 0:
            return False, f"container runtime not running: {out.decode().strip()[:200]}"
        return True, out.decode().strip()

    def command(
        self, *, name: str, image: str, workdir: str, script: str, runtime: str, network: bool
    ) -> list[str]:
        """The full `docker run` invocation. Every restriction is here."""
        c = self.config
        cmd = [
            c.sandbox_docker_bin, "run", "--rm",
            "--name", name,
            "--network", "bridge" if network else "none",
            "--memory", c.sandbox_memory,
            "--memory-swap", c.sandbox_memory,
            "--cpus", str(c.sandbox_cpus),
            "--pids-limit", str(c.sandbox_pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,exec,size=512m",
            "--user", RUNTIME_USER.get(runtime, DEFAULT_USER),
            "--env", "HOME=/tmp",
            "--volume", f"{workdir}:{WORKDIR}:rw",
            "--workdir", WORKDIR,
        ]  # fmt: skip
        for key, value in RUNTIME_ENV.get(runtime, {}).items():
            cmd += ["--env", f"{key}={value}"]
        guarded = (
            f'test -f {WORKDIR}/{SENTINEL} || {{ echo "workspace not mounted: the '
            f'container runtime cannot see {workdir}"; exit {MOUNT_FAILED}; }}; {script}'
        )
        return [*cmd, image, "sh", "-c", guarded]

    async def _kill(self, name: str) -> None:
        # Killing the client is not enough: the container keeps running unless
        # the runtime is told to stop it.
        killer = await asyncio.create_subprocess_exec(
            self.config.sandbox_docker_bin, "kill", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        await killer.wait()

    async def _exec(self, cmd: list[str], name: str, timeout: int) -> tuple[int | None, str, bool]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            # The caller gave up — a task timeout or a cancelled workflow. The
            # container must not outlive it.
            await asyncio.shield(self._kill(name))
            proc.kill()
            raise
        except TimeoutError:
            await self._kill(name)
            proc.kill()
            # Bounded too: a stray process holding the pipe open must not turn
            # a timeout back into a hang.
            try:
                out = (await asyncio.wait_for(proc.communicate(), timeout=5))[0] or b""
            except TimeoutError:
                out = b""
            return None, out.decode(errors="replace"), True
        return proc.returncode, out.decode(errors="replace"), False

    async def run(
        self, files: Iterable[tuple[str, str]], spec: RunSpec, *, timeout: int | None = None
    ) -> SandboxResult:
        image = self.config.sandbox_images.get(spec.runtime)
        if image is None:
            known = ", ".join(sorted(self.config.sandbox_images))
            # The Tester's mistake, not the environment's, so it is fed back.
            return SandboxResult(None, f"Unknown runtime '{spec.runtime}'. Use one of: {known}", 0, stage="spec")

        # A stopped runtime makes `docker run` exit 1 — the same code as a
        # failing test. Checking first is the only way to tell "could not
        # run" from "ran and failed".
        ok, reason = await self.available()
        if not ok:
            return SandboxResult(None, reason, 0, stage="setup")

        started = time.monotonic()
        timeout = timeout or self.config.sandbox_timeout_seconds
        base = Path(self.config.sandbox_workdir_root).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="run-", dir=base) as tmp:
            root = Path(tmp)
            materialize(files, root)
            (root / SENTINEL).write_text("mounted\n")
            # The container runs as an unprivileged user that does not own the
            # temp dir, and installs write into it.
            for directory, _, _ in os.walk(root):
                os.chmod(directory, 0o777)

            stages = [("install", spec.install, self.config.sandbox_install_network)] if spec.install else []
            stages.append(("test", spec.command, False))
            shown = {"install": spec.install, "test": spec.label or spec.command}

            log_parts: list[str] = []
            for stage, script, network in stages:
                name = f"evergent-{stage}-{uuid.uuid4().hex[:12]}"
                cmd = self.command(
                    name=name, image=image, workdir=str(root), script=script,
                    runtime=spec.runtime, network=network,
                )  # fmt: skip
                remaining = max(1, timeout - int(time.monotonic() - started))
                code, out, timed_out = await self._exec(cmd, name, remaining)
                log_parts.append(f"$ {shown[stage]}\n{out}")
                elapsed = int((time.monotonic() - started) * 1000)
                if code == MOUNT_FAILED and "workspace not mounted" in out:
                    return SandboxResult(None, out.strip(), elapsed, False, "setup")
                if timed_out:
                    log_parts.append(f"[timed out after {timeout}s]")
                    return SandboxResult(None, tail("\n".join(log_parts)), elapsed, True, stage)
                if code != 0 or stage == "test":
                    return SandboxResult(code, tail("\n".join(log_parts)), elapsed, False, stage)

        raise AssertionError("unreachable: the test stage always returns")


def get_sandbox() -> DockerSandbox:
    return DockerSandbox()
