# RiceSuite

https://github.com/user-attachments/assets/75a386cd-5fa9-4092-bdbf-a42125f7a7d2

RiceSuite combines the three Rice pillars into one local app:

| Directory | Pillar | Job |
|---|---|---|
| [`searcher/`](searcher/) | RiceSearcher | Acquire → transcribe → score → **human select** |
| [`clipper/`](clipper/) | RiceClipper | Caption / header / 9:16 crop / music / photo clips → **human review + render** |
| [`poster/`](poster/) | RicePoster | **Human Post All / Schedule** to Instagram/TikTok |

One `rice` command starts a Slate front door with Search / Clip / Post tabs and
three supervised pillar processes that share one Python environment. Batches
move automatically from Search to Clip over filesystem handoffs. You send each rendered batch with **Send to RicePoster**,
and you pull it into Post with **Pull from Clipper**. All three human judgement
gates stay, and nothing is ever posted automatically. The design contract is
[ADR-001](docs/adr/ADR-001-ricesuite-consolidation.md).

## Status

RiceSuite is the supported app for daily use. Burn-in passed on 2026-10-05.
The original repositories (`gidde032/RiceSearcher`, `gidde032/RiceClipper`,
`gidde032/RicePoster`) are superseded and get no fixes. Open Issues and pull
requests here.

## Run

```bash
rice            # or: rice start — foreground; open http://127.0.0.1:8790
rice status     # from another terminal
rice stop       # refuses while Post is posting; --force overrides
```

`rice` starts a localhost-only gateway on port 8790 and the three pillars on
8791–8793. It restarts a pillar that crashes. It refuses to start while
anything answers on the legacy apps' ports (8765 / 8000 / 1738), because a
legacy app started by mistake could write to the same data. Ctrl-C runs the
same safety check as `rice stop`; press it twice to force. See
[SPEC.md](SPEC.md) §2–3.

RiceSuite keeps application data under `~/.ricesuite` by default. An
installation with legacy data paths keeps them until an explicit migration. Run `rice data location` to see effective paths; see
[Data location and migration](docs/data-migration.md) for the offline
plan, copy, cutover and rollback commands and Finder/File Explorer directions.

To try it without touching real data, point every data root at a temp dir:

```bash
T=$(mktemp -d); mkdir -p $T/poster
cat > $T/ricesuite.env <<EOT
RICESEARCHER_DATA_DIR=$T/searcher
RICECLIPPER_WORK_DIR=$T/clipper
RICESEARCHER_HANDOFF_DIR=$T/h1
RICECLIPPER_HANDOFF_DIR=$T/h2
RICEPOSTER_DATA_DIR=$T/poster
POST_MODE=mock
SCHEDULER_ENABLED=false
EOT
RICESUITE_ENV=$T/ricesuite.env RICESUITE_RUN_DIR=$T/run rice
```

## Install (one environment)

Python 3.12 or newer, `ffmpeg` with libass (see `clipper/README.md`), and Node
for Searcher's UI tests.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # full runtime, incl. Searcher's PyTorch-based dedup
pip install -r requirements-dev.txt    # gates + everything the test suites import
pip install -e . -e searcher
cp ricesuite.env.example ricesuite.env # optional; every variable is optional
```

`requirements.txt` is the single authority for shared pins; each pillar's own
requirements file must accept them (`tests/test_pins.py`).

The shared Python modules under `ricesuite/` are installed with `pip install -e .`
and imported by the pillars from their own working directories. Each pillar
keeps its own transcription output rules and Anthropic prompt/model/payload.
Anthropic clients use a 60-second SDK timeout **per attempt** and two SDK
retries with backoff, so one request can take longer than 60 seconds. The
clients are opened on demand and closed after the request. Searcher also
accepts `ANTHROPIC_AUTH_TOKEN` when `ANTHROPIC_API_KEY` is absent; Clipper and
Poster require the API key.

## Gates

One CI workflow (`.github/workflows/ci.yml`) runs every pillar's own gates at
its own floor, from its own directory — Searcher ≥ 90%, Clipper ≥ 85%,
Poster ≥ 43% — plus the suite tests:

```bash
python -m pytest -q                                          # suite (ricesuite/ ≥ 90%)
(cd searcher && ruff format --check . && ruff check . && mypy && node --test tests/js/*.test.js && pytest)
(cd clipper && ruff check . && ruff format --check . && python -m pytest tests/ -q --cov=app --cov=render --cov=transcribe --cov-fail-under=85)
(cd poster && python -m pytest tests/ -q --cov=backend --cov-fail-under=43)
```

## Slate styling

The common Slate palette and base rules are in `ricesuite/shell/slate.css`.
Each page loads that asset before its own stylesheet; see
[Slate ownership](docs/design/slate-ownership.md) for routes and overrides.

## History

Each pillar's full public history was imported from its repository's GitHub
`main` and rewritten into its subdirectory. `#N` references in imported commit
messages, docs, and code comments point to Issues and PRs **in the original
repository** of that pillar, not to this repository.

## Licence

Each pillar keeps its own `LICENSE` file.
