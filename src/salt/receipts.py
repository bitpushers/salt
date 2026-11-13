"""Running a command and capturing evidence.

pipeline.RECEIPT.* — a receipt records the command, its working directory, its exit code
and the HEAD commit. A non-zero exit is stored but never satisfies a phase.
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

TAIL_LINES = 20
DEFAULT_TIMEOUT = 1800


@dataclass
class Receipt:
    command: str
    cwd: str
    exit_code: int
    commit_sha: str | None
    digest: str
    output_tail: str
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def git_sha(cwd: Path | str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def normalise_command(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(part) for part in command]


def run_command(
    command: str | list[str], cwd: Path | str, timeout: int = DEFAULT_TIMEOUT
) -> Receipt:
    argv = normalise_command(command)
    display = " ".join(shlex.quote(part) for part in argv)
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = result.returncode
        output = (result.stdout or "") + (result.stderr or "")
    except FileNotFoundError as exc:
        exit_code = 127
        output = f"{exc}\n"
    except subprocess.TimeoutExpired:
        exit_code = 124
        output = f"timed out after {timeout}s\n"
    except OSError as exc:
        exit_code = 126
        output = f"{exc}\n"

    digest = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()
    tail = "\n".join(output.strip().splitlines()[-TAIL_LINES:])
    return Receipt(
        command=display,
        cwd=str(cwd),
        exit_code=exit_code,
        commit_sha=git_sha(cwd),
        digest=digest,
        output_tail=tail,
        output=output,
    )
