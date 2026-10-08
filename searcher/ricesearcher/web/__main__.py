"""Run the review UI: ``python -m ricesearcher.web`` (or ``ricesearcher review``)."""

from __future__ import annotations

import sys

from ricesuite.env import SuiteConfigError

from ricesearcher.web.app import create_app


def main() -> int:
    """Serve on loopback; a bad suite config is a clean error, as in ``cli.main``."""
    try:
        app = create_app()
    except SuiteConfigError as exc:
        print(f"error: RiceSuite configuration: {exc}", file=sys.stderr)
        return 2
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
    return 0


if __name__ == "__main__":  # pragma: no cover - live server
    raise SystemExit(main())
