"""Run the review UI: ``python -m ricesearcher.web`` (or ``ricesearcher review``)."""

from ricesearcher.web.app import create_app

if __name__ == "__main__":  # pragma: no cover - live server
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
