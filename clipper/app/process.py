"""Small, owned subprocess helpers for local media tools.

ffmpeg and ffprobe are launched by request-scoped work, so their processes must
not outlive the operation that owns them. On POSIX, each child starts in its own
session and termination targets that session; other platforms fall back to the
child process' native ``kill`` method.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path


class ProcessTimeoutError(subprocess.TimeoutExpired):
    """A child process exceeded its timeout and was terminated."""


_ACTIVE_PROCESSES: set[subprocess.Popen] = set()
_ACTIVE_PROCESSES_LOCK = threading.Lock()


def terminate_all_owned_processes() -> None:
    """Terminate every media child still owned by this process.

    This is used from application shutdown. A normal ``Popen.communicate``
    timeout handles request-local hangs; this second registry covers a graceful
    server shutdown while a synchronous render is still in flight.
    """
    with _ACTIVE_PROCESSES_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
    for proc in processes:
        terminate_owned_process(proc)


def terminate_owned_process(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and its owned process group when the platform supports it."""
    if os.name == "posix" and hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            process_group = os.getpgid(proc.pid)
            os.killpg(process_group, signal.SIGKILL)
            return
        except (AttributeError, OSError, ProcessLookupError, ValueError):
            # The process may have exited between the timeout and this call, or
            # a test/platform process object may not expose a usable pid.
            pass

    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _reap_after_termination(proc: subprocess.Popen) -> tuple[object, object]:
    """Reap a killed child without letting a second decode error mask the cause."""
    try:
        return proc.communicate()
    except BaseException:
        try:
            proc.wait()
        except BaseException:
            pass
        return None, None


def run_owned(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    capture_output: bool = False,
    text: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a child process and cleanly terminate it on timeout or interruption."""
    command = list(args)
    popen_kwargs: dict[str, object] = {}
    if cwd is not None:
        popen_kwargs["cwd"] = str(cwd)
    if capture_output:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    if text:
        popen_kwargs["text"] = True
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    # Keep launch and registration atomic with respect to shutdown. Otherwise
    # shutdown could snapshot the registry between Popen() and add().
    with _ACTIVE_PROCESSES_LOCK:
        proc = subprocess.Popen(command, **popen_kwargs)
        _ACTIVE_PROCESSES.add(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_owned_process(proc)
        stdout, stderr = _reap_after_termination(proc)
        raise ProcessTimeoutError(
            command,
            timeout,
            output=stdout if stdout is not None else exc.output,
            stderr=stderr if stderr is not None else exc.stderr,
        ) from exc
    except KeyboardInterrupt:
        terminate_owned_process(proc)
        _reap_after_termination(proc)
        raise
    except BaseException:
        # communicate() can fail for reasons other than timeout (for example a
        # text decoding error). The child still belongs to this operation.
        terminate_owned_process(proc)
        _reap_after_termination(proc)
        raise
    finally:
        with _ACTIVE_PROCESSES_LOCK:
            _ACTIVE_PROCESSES.discard(proc)

    completed = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    if check and completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


# A forced interpreter exit can bypass FastAPI's lifespan finalizer. This is a
# last-resort safety net for a normally exiting process; SIGKILL still cannot be
# handled by Python.
atexit.register(terminate_all_owned_processes)
