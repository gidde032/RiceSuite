"""Enable ``python -m ricesearcher`` (mirrors the ``ricesearcher`` console script)."""

from ricesearcher.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
