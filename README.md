# RiceSuite

RiceSuite combines the three Rice pillars into one local app:

| Directory | Pillar | Job |
|---|---|---|
| [`searcher/`](searcher/) | RiceSearcher | Acquire → transcribe → score → **human select** |
| [`clipper/`](clipper/) | RiceClipper | Caption / header / 9:16 crop / music → **human review + render** |
| [`poster/`](poster/) | RicePoster | **Human Post All / Schedule** to Instagram/TikTok |

The target shape is one `rice` command, one Slate front door with Search / Clip /
Post tabs, three supervised pillar processes sharing one Python environment, and
batches moving automatically between pillars over the existing filesystem
handoff contracts. All three human judgement gates stay, and nothing is ever
posted automatically. The design contract is
[ADR-001](docs/adr/ADR-001-ricesuite-consolidation.md).

## Status

**Burn-in candidate, not yet supported.** The original repositories
(`gidde032/RiceSearcher`, `gidde032/RiceClipper`, `gidde032/RicePoster`) remain
the supported apps until the burn-in in ADR-001 Q9 passes. Until the suite
launcher lands, run each pillar from its own directory as described in its
README.

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

## History

Each pillar's full public history was imported from its repository's GitHub
`main` and rewritten into its subdirectory. `#N` references in imported commit
messages, docs, and code comments point to Issues and PRs **in the original
repository** of that pillar, not to this repository.

## Licence

Each pillar keeps its own `LICENSE` file.
