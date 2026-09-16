# Spike S2: `metadata=print` exposes `lavfi.scene_score`

**Status:** PASS on ffmpeg 9.0.1 (2026-09-15). One pass at threshold 0.2 returns
both `pts_time` and `lavfi.scene_score` per selected frame.
**Owner:** music path (ADR-002, `docs/design/music-path-spec.md`).

## Question

Does `metadata=print:key=lavfi.scene_score` placed between `select` and
`showinfo` print the scene score to stderr on ffmpeg 9.0.1?

## Method

```
ffmpeg -hide_banner -nostats -i fixtures/interview-outputs/walking-interview.mp4 \
  -vf "select='gt(scene,0.2)',metadata=print:key=lavfi.scene_score,showinfo" \
  -an -f null -
```

## Result

Three example stderr line pairs:

```
[Parsed_metadata_1 @ 0x9d500e1c0] lavfi.scene_score=0.707689
[Parsed_showinfo_2 @ 0x9d500e280] n:   0 pts: 142336 pts_time:11.12 ...

[Parsed_metadata_1 @ 0x9d500e1c0] lavfi.scene_score=0.645836
[Parsed_showinfo_2 @ 0x9d500e280] n:   1 pts: 290304 pts_time:22.68 ...

[Parsed_metadata_1 @ 0x9d500e1c0] lavfi.scene_score=0.582529
[Parsed_showinfo_2 @ 0x9d500e280] n:   2 pts: 313856 pts_time:24.52 ...
```

Both fields parse cleanly. `metadata=print` emits one `lavfi.scene_score=<float>`
line per selected frame; `showinfo` emits `pts_time:<float>` on the next line.
One regex each extracts both values.
