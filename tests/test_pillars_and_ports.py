"""Process specs and port rules (SPEC §2.1, FR-3, FR-4, FR-6)."""

import socket

import pytest

from ricesuite import ports
from ricesuite.pillars import BY_NAME, PILLARS, gateway_argv, pillar_argv


def _listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_suite_ports_never_reuse_an_old_apps_port():
    suite = {ports.GATEWAY_PORT, *ports.PILLAR_PORTS.values()}
    assert len(suite) == 4
    assert not suite & set(ports.LEGACY_PORTS)
    assert set(ports.LEGACY_PORTS) == {8765, 8000, 1738}


@pytest.mark.parametrize("pillar", PILLARS, ids=lambda p: p.name)
def test_every_process_binds_loopback_only(pillar):
    for argv in (pillar_argv(pillar), gateway_argv()):
        assert argv[argv.index("--host") + 1] == "127.0.0.1"
        assert "0.0.0.0" not in argv


def test_poster_keeps_its_historical_runtime_and_never_reloads():
    argv = pillar_argv(BY_NAME["poster"])
    assert argv[argv.index("--loop") + 1] == "asyncio"
    assert argv[argv.index("--http") + 1] == "h11"
    for pillar in PILLARS:
        assert "--reload" not in pillar_argv(pillar)


def test_searcher_runs_its_app_factory():
    argv = pillar_argv(BY_NAME["searcher"])
    assert "ricesearcher.web.app:create_app" in argv and "--factory" in argv


def test_prefixes_are_distinct_and_tabs_are_search_clip_post():
    assert [p.tab for p in PILLARS] == ["Search", "Clip", "Post"]
    assert len({p.prefix for p in PILLARS}) == 3


def test_is_listening_sees_a_real_listener():
    sock = _listener()
    try:
        assert ports.is_listening(sock.getsockname()[1])
    finally:
        sock.close()
    assert not ports.is_listening(_free_port())


def test_startup_refuses_when_an_old_app_port_answers():
    sock = _listener()
    port = sock.getsockname()[1]
    try:
        problems = ports.startup_conflicts({port: "RicePoster"}, suite_ports=[])
    finally:
        sock.close()
    assert len(problems) == 1
    assert str(port) in problems[0] and "RicePoster" in problems[0]
    # Burn-in is over (ADR-001 amendment of 2026-10-05): the guard prevents
    # an accidental double run; it no longer describes shared live data.
    assert "accidental double run" in problems[0]
    assert "share live data" not in problems[0]
    assert "only one side runs" not in problems[0]


def test_startup_refuses_when_a_suite_port_is_taken():
    sock = _listener()
    port = sock.getsockname()[1]
    try:
        problems = ports.startup_conflicts({}, suite_ports=[port])
    finally:
        sock.close()
    assert problems and str(port) in problems[0]


def test_startup_goes_when_everything_is_free():
    assert ports.startup_conflicts({_free_port(): "Old"}, [_free_port()]) == []
