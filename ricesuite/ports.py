"""Ports and the legacy-app port guard (ADR-001 Q10, SPEC §2.1 and FR-4)."""

from __future__ import annotations

import socket

HOST = "127.0.0.1"

# The legacy apps' ports. If anything answers on one of these, a legacy app may
# be running, so RiceSuite refuses to start. The guard prevents an accidental
# double run (ADR-001 amendment "Burn-in complete; public").
LEGACY_PORTS: dict[int, str] = {
    8765: "RiceSearcher",
    8000: "RiceClipper",
    1738: "RicePoster",
}

GATEWAY_PORT = 8790
PILLAR_PORTS: dict[str, int] = {"searcher": 8791, "clipper": 8792, "poster": 8793}


def is_listening(port: int, timeout: float = 0.3) -> bool:
    """True if something accepts TCP connections on ``port`` on IPv4 or IPv6
    loopback. An app bound to 0.0.0.0 also answers on 127.0.0.1."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def startup_conflicts(
    legacy: dict[int, str] | None = None, suite_ports: list[int] | None = None
) -> list[str]:
    """Human-readable reasons the suite must not start; empty means go."""
    legacy = LEGACY_PORTS if legacy is None else legacy
    if suite_ports is None:
        suite_ports = [GATEWAY_PORT, *PILLAR_PORTS.values()]
    problems = [
        f"port {port} is in use — is the legacy {name} app running? Stop it "
        f"first. This guard prevents an accidental double run."
        for port, name in sorted(legacy.items())
        if is_listening(port)
    ]
    problems += [
        f"port {port} is already in use (RiceSuite needs it). Is RiceSuite already "
        f"running? Try `rice status`."
        for port in suite_ports
        if is_listening(port)
    ]
    return problems
