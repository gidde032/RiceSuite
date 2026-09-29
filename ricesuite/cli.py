"""`rice`: start, stop and inspect RiceSuite.

ADR-001 Q5, Q17; SPEC FR-1 – FR-8, FR-17, FR-18.

`rice` / `rice start` runs in the foreground until stopped (Q18). It supervises
the gateway and the three pillars, and records their state in a small JSON
file so `rice status` and `rice stop` can work from another terminal.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from ricesuite import SUITE_ROOT, ports, stopguard
from ricesuite import env as suite_env
from ricesuite.pillars import PILLARS, gateway_argv, pillar_argv, pillar_cwd
from ricesuite.supervisor import Child, Supervisor

POLL_S = 0.5
# A second Ctrl-C within this window forces a stop the guard refused.
FORCE_WINDOW_S = 10.0


def run_dir(environ=os.environ) -> Path:
    return Path(
        environ.get("RICESUITE_RUN_DIR") or SUITE_ROOT / ".ricesuite"
    ).expanduser()


def state_path(environ=os.environ) -> Path:
    return run_dir(environ) / "state.json"


def read_state(environ=os.environ) -> dict | None:
    try:
        return json.loads(state_path(environ).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def lock_path(environ=os.environ) -> Path:
    return run_dir(environ) / "launcher.lock"


def acquire_launcher_lock(environ=os.environ):
    """Take the launcher lock, held for the launcher's whole life.

    Liveness is "someone holds this lock", never "the recorded pid exists": a
    state file left by a killed launcher, or its pid reused by an unrelated
    process, cannot fake a held lock, and the kernel releases it however the
    launcher dies. Returns the open file (keep it open), or None if held.
    """
    path = lock_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a")  # noqa: SIM115 - held open for the launcher's life
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def launcher_running(environ=os.environ) -> bool:
    path = lock_path(environ)
    if not path.exists():
        return False
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


_CHILD_TARGETS = {p.name: p.target for p in PILLARS} | {
    "gateway": "ricesuite.gateway:app"
}


def process_args(pid: int) -> list[str]:
    """The process's argv: exact from /proc on Linux, else from `ps -ww`.
    (`ps` without -ww truncates a piped command line to 80 columns on Linux,
    which cut off everything after a long interpreter path.)"""
    proc = Path(f"/proc/{pid}/cmdline")
    try:
        raw = proc.read_bytes()
    except OSError:
        raw = b""
    if raw:
        return [a for a in raw.decode(errors="replace").split("\0") if a]
    try:
        command = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return command.split()


def _is_suite_child(pid: int, name: str, port) -> bool:
    """The pid is alive and its command line is that child's uvicorn target on
    the recorded port, so a reused pid (or a developer's own uvicorn run) is
    never mistaken for a leftover."""
    if not _pid_alive(pid) or port is None:
        return False
    args = process_args(pid)
    return (
        "uvicorn" in args
        and _CHILD_TARGETS.get(name) in args
        and "--port" in args[:-1]
        and args[args.index("--port") + 1] == str(port)
    )


def leftovers(state: dict | None) -> list[dict]:
    """Children of a launcher that died without stopping them (e.g. SIGKILL).
    They keep running, and Poster may be mid-post, so they are surfaced by
    `rice status` and stopped only through `rice stop`'s guard."""
    found = []
    for name, child in (state or {}).get("children", {}).items():
        pid = child.get("pid")
        if pid and _is_suite_child(int(pid), name, child.get("port")):
            found.append({"name": name, "pid": int(pid), "port": child.get("port")})
    return found


def _terminate(found: list[dict], timeout: float = 15.0) -> None:
    for child in found:
        try:
            os.killpg(child["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + timeout
    for child in found:
        while _pid_alive(child["pid"]) and time.monotonic() < deadline:
            time.sleep(0.2)
        if _pid_alive(child["pid"]):
            try:
                os.killpg(child["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _describe(found: list[dict]) -> str:
    return ", ".join(f"{c['name']} (pid {c['pid']}, port {c['port']})" for c in found)


def _write_state(path: Path, launcher_pid: int, sup: Supervisor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "launcher_pid": launcher_pid,
                "gateway_port": ports.GATEWAY_PORT,
                "children": sup.snapshot(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:  # os.kill(0, …) would signal our own process group
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def build_children(environ: dict[str, str], state_file: Path) -> list[Child]:
    base = dict(environ)
    base["PYTHONUNBUFFERED"] = "1"
    gateway_env = dict(base)
    gateway_env["RICESUITE_GATEWAY_CONFIG"] = json.dumps(
        {
            "gateway_port": ports.GATEWAY_PORT,
            "pillar_ports": ports.PILLAR_PORTS,
            "state_file": str(state_file),
        }
    )
    children = [
        Child(
            name=p.name,
            argv=pillar_argv(p, ports.PILLAR_PORTS[p.name]),
            cwd=pillar_cwd(p),
            env=base,
            port=ports.PILLAR_PORTS[p.name],
        )
        for p in PILLARS
    ]
    children.append(
        Child(
            name="gateway",
            argv=gateway_argv(ports.GATEWAY_PORT),
            cwd=str(SUITE_ROOT),
            env=gateway_env,
            port=ports.GATEWAY_PORT,
        )
    )
    return children


def stop_decision(state: dict | None, force: bool) -> stopguard.StopDecision:
    """Decide whether a stop may go ahead. A running Poster is asked for its
    stop hold first, so no posting run can start before the stop takes effect;
    a refused stop releases the hold again. An allowed decision that holds it
    names Poster's port in ``held_port``; see ``_abandon``."""
    poster = (state or {}).get("children", {}).get("poster", {})
    poster_running = poster.get("state") == "running" and bool(poster.get("pid"))
    poster_running = poster_running and _pid_alive(int(poster["pid"]))
    if not poster_running:
        return stopguard.decide(None, False, force)
    port = int(poster["port"])
    poster_state = stopguard.hold_poster(port)
    decision = stopguard.decide(poster_state, True, force)
    if poster_state.held:
        if decision.allowed:
            decision.held_port = port
        else:
            stopguard.release_poster(port)
    return decision


def _abandon(decision: stopguard.StopDecision) -> int:
    """The stop did not happen: give Poster its posting back at once rather
    than after the hold's lease."""
    if decision.held_port is not None:
        stopguard.release_poster(decision.held_port)
    return 1


class InterruptHandler:
    """SIGINT/SIGTERM for the foreground launcher.

    SIGTERM (what `rice stop` sends, after its own check) stops at once. A
    Ctrl-C runs the same safety check as `rice stop`: while a Poster run is
    active it refuses, and a second Ctrl-C within FORCE_WINDOW_S forces it.
    """

    def __init__(self, decide=None, clock=time.monotonic, out=print):
        self.decide = decide or (lambda: stop_decision(read_state(), force=False))
        self.clock = clock
        self.out = out
        self.last_refused: float | None = None

    def __call__(self, signum, frame=None):
        now = self.clock()
        forced = (
            self.last_refused is not None and now - self.last_refused <= FORCE_WINDOW_S
        )
        if signum == signal.SIGTERM or forced:
            raise KeyboardInterrupt
        decision = self.decide()
        for message in decision.messages:
            self.out(f"rice: {message}")
        if decision.allowed:
            raise KeyboardInterrupt
        self.last_refused = now
        self.out(
            f"rice: press Ctrl-C again within {FORCE_WINDOW_S:g}s to force the stop."
        )


def cmd_start(args: argparse.Namespace) -> int:
    lock = acquire_launcher_lock()
    if lock is None:
        print("RiceSuite is already running (see `rice status`).", file=sys.stderr)
        return 1
    try:
        return _start(lock)
    finally:
        lock.close()


def _start(lock) -> int:
    found = leftovers(read_state())
    if found:
        print(
            f"rice: refusing to start: processes from an earlier RiceSuite run are "
            f"still alive: {_describe(found)}. Run `rice stop` first.",
            file=sys.stderr,
        )
        return 1
    # Nothing it names is alive. Remove it now, so `rice stop` never signals
    # the old launcher pid while this launcher starts.
    state_path().unlink(missing_ok=True)
    try:
        environ = suite_env.load()
    except suite_env.SuiteConfigError as exc:
        print(f"rice: configuration error: {exc}", file=sys.stderr)
        return 2
    unknown = suite_env.unknown_keys(
        suite_env.read_env_file(
            Path(os.environ.get("RICESUITE_ENV") or suite_env.DEFAULT_ENV_FILE)
        )
    )
    for key in unknown:
        print(f"rice: warning: ricesuite.env sets {key}, which no pillar reads")
    root = suite_env.data_root(environ)
    if Path(environ["RICEPOSTER_DATA_DIR"]) == root / "poster":
        if root.is_symlink() or (root / "poster").is_symlink():
            print("rice: refusing a symlinked unified data root", file=sys.stderr)
            return 2
        (root / "poster").mkdir(parents=True, exist_ok=True, mode=0o700)
    problems = ports.startup_conflicts()
    if problems:
        for problem in problems:
            print(f"rice: refusing to start: {problem}", file=sys.stderr)
        return 1

    state_file = state_path()
    # Publish the state after every spawn and restart, so no child runs
    # unrecorded if the launcher dies before its next poll.
    sup = Supervisor(
        build_children(environ, state_file),
        on_spawn=lambda: _write_state(state_file, os.getpid(), sup),
    )
    request_stop = InterruptHandler()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    print(
        f"RiceSuite → http://{ports.HOST}:{ports.GATEWAY_PORT}"
        "   (Ctrl-C or `rice stop`)"
    )
    try:
        sup.start_all()
        while True:
            sup.poll_once()
            _write_state(state_file, os.getpid(), sup)
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("rice: stopping…")
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        sup.stop_all()
        try:
            state_file.unlink()
        except OSError:
            pass
        print("rice: stopped.")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    state = read_state()
    if not launcher_running():
        found = leftovers(state)
        if not found:
            state_path().unlink(missing_ok=True)
            print("RiceSuite is not running.")
            return 0
        print(
            "rice: the launcher is gone but its processes are alive: "
            + _describe(found)
        )
        decision = stop_decision(state, force=args.force)
        for message in decision.messages:
            print(f"rice: {message}")
        if not decision.allowed:
            return 1
        _terminate(found)
        survivors = leftovers(state)
        if survivors:
            print(f"rice: could not stop, still alive: {_describe(survivors)}")
            return _abandon(decision)
        state_path().unlink(missing_ok=True)
        print("RiceSuite's leftover processes stopped.")
        return 0
    if not state or not state.get("launcher_pid"):
        print("rice: RiceSuite is starting; try again in a moment.", file=sys.stderr)
        return 1
    decision = stop_decision(state, force=args.force)
    for message in decision.messages:
        print(f"rice: {message}")
    if not decision.allowed:
        return 1
    try:
        os.kill(int(state["launcher_pid"]), signal.SIGTERM)
    except ProcessLookupError:
        # Only a state from an earlier launcher can name a dead pid.
        print("rice: RiceSuite is starting; try again in a moment.", file=sys.stderr)
        return _abandon(decision)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and launcher_running():
        time.sleep(0.2)
    print("RiceSuite stopped.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = read_state()
    running = launcher_running()
    if running and not state:
        print("RiceSuite is starting (launcher holds its lock; no state yet).")
        return 0
    if not running:
        found = leftovers(state)
        if found:
            print(
                "RiceSuite's launcher is not running, but processes it started are "
                f"still alive: {_describe(found)}. Run `rice stop` (it checks for "
                "an active Poster run first)."
            )
            return 4
        print("RiceSuite is not running.")
        busy = [
            f"{name} on {port}"
            for port, name in sorted(ports.LEGACY_PORTS.items())
            if ports.is_listening(port)
        ]
        if busy:
            print("Old apps answering: " + ", ".join(busy))
        return 3
    print(
        f"RiceSuite is running (launcher pid {state['launcher_pid']}) → "
        f"http://{ports.HOST}:{state.get('gateway_port', ports.GATEWAY_PORT)}"
    )
    for name, child in state.get("children", {}).items():
        print(
            f"  {name:<9} {child.get('state', '?'):<11} port {child.get('port')}  "
            f"pid {child.get('pid')}  restarts {child.get('restarts', 0)}"
        )
    return 0


def cmd_data(args: argparse.Namespace) -> int:
    from ricesuite import migration

    config_file = Path(
        os.environ.get("RICESUITE_ENV") or suite_env.DEFAULT_ENV_FILE
    ).expanduser()
    if args.data_command == "location":
        try:
            configured = suite_env.load()
        except suite_env.SuiteConfigError as exc:
            print(f"rice: configuration error: {exc}", file=sys.stderr)
            return 2
        print(f"Unified data root: {suite_env.data_root(configured)}")
        for key in suite_env.DATA_PATHS:
            print(f"  {key}={configured[key]}")
        return 0
    if args.data_command is None:
        print(
            "rice: choose plan, copy, cutover, rollback, or location", file=sys.stderr
        )
        return 2
    root = Path(args.root or os.environ.get("RICESUITE_DATA_DIR") or "~/.ricesuite")
    lock = acquire_launcher_lock()
    if lock is None:
        print("rice: migration requires RiceSuite to be stopped", file=sys.stderr)
        return 1
    try:
        found = leftovers(read_state())
        conflicts = ports.startup_conflicts()
        if found or conflicts:
            raise migration.MigrationError(
                "stop RiceSuite, old apps and workers first: "
                + "; ".join(([_describe(found)] if found else []) + conflicts)
            )
        shell_paths = [key for key in migration.PATH_VARIABLES if os.environ.get(key)]
        if shell_paths:
            raise migration.MigrationError(
                "unset shell path overrides before migration: " + ", ".join(shell_paths)
            )
        sources = migration._sources(suite_env.read_env_file(config_file))
        # Browser parents advertise their profile path in --user-data-dir.
        # Never open a profile to test whether it is live.
        process_list = subprocess.run(
            ["ps", "-axww", "-o", "command="],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        session_paths = [
            str(sources["poster"] / "sessions"),
            str(root.expanduser() / "poster/sessions"),
        ]
        if any(
            "--user-data-dir" in line and any(path in line for path in session_paths)
            for line in process_list.splitlines()
        ):
            raise migration.MigrationError(
                "a browser is using a source or destination profile; "
                "close it before migration"
            )
        source_paths = [str(path) for path in sources.values()]
        if any(
            any(path in line for path in source_paths)
            and any(
                name in line
                for name in (
                    "ffmpeg",
                    "uvicorn",
                    "ricesearcher",
                    "riceclipper",
                    "riceposter",
                )
            )
            for line in process_list.splitlines()
        ):
            raise migration.MigrationError(
                "a worker references an old data path; stop it before migration"
            )
        if args.data_command in {"plan", "copy"}:
            config = suite_env.read_env_file(config_file)
            report = (
                migration.plan(root, config)
                if args.data_command == "plan"
                else migration.copy(root, config)
            )
            print(json.dumps(report, indent=2))
        elif args.data_command == "cutover":
            migration.cutover(root, config_file, dict(os.environ))
            print(
                "Cutover complete. Originals retained. "
                f"New data root: {root.expanduser()}"
            )
        else:
            migration.rollback(root, config_file, dict(os.environ))
            print("Configuration restored to the original paths; copied data retained.")
        return 0
    except (migration.MigrationError, OSError, ValueError) as exc:
        print(f"rice: migration refused: {exc}", file=sys.stderr)
        return 1
    finally:
        lock.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rice", description="Run RiceSuite: Search, Clip and Post behind one tab."
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="start the suite in the foreground (default)")
    p_stop = sub.add_parser("stop", help="stop a running suite")
    p_stop.add_argument(
        "--force", action="store_true", help="stop even while a Poster run is active"
    )
    sub.add_parser("status", help="show what is running")
    p_data = sub.add_parser(
        "data", help="inspect or explicitly migrate application data"
    )
    data_commands = p_data.add_subparsers(dest="data_command")
    data_commands.add_parser("location", help="show effective configured data paths")
    for action in ("plan", "copy", "cutover", "rollback"):
        p_action = data_commands.add_parser(action)
        p_action.add_argument("--root", help="destination root (default ~/.ricesuite)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "start"
    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "data": cmd_data,
    }
    return handlers[command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
