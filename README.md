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

## History

Each pillar's full public history was imported from its repository's GitHub
`main` and rewritten into its subdirectory. `#N` references in imported commit
messages, docs, and code comments point to Issues and PRs **in the original
repository** of that pillar, not to this repository.

## Licence

Each pillar keeps its own `LICENSE` file.
