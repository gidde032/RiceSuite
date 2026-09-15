# Spike S1: `sendcmd` drives `crop` x over time

**Status:** PASS on ffmpeg 9.0.1 (2026-09-14). The `sendcmd` path is the
chosen path. The nested `if()` fallback also passes and stays as the reserve.
**Owner:** subject crop (ADR-001, `docs/design/subject-crop-spec.md`)

## Question

Can one ffmpeg `filter_complex` move a 608x1080 crop window horizontally at
times read from a command file, with no re-encode step in between?

## Method

1. Source: 1920x1080, 30 fps, 4 s. Red fill. Blue block at x 1200..1808.
2. Command file `crop.cmd`, two lines: `0.0 crop x 0;` and `2.0 crop x 1200;`.
3. Filter: `[0:v]sendcmd=f=crop.cmd,crop=608:1080:0:0,scale=1080:1920[v]`.
4. Check: mean RGB of frames at t=1 and t=3, then frames 58 to 61.
5. Fallback: `crop=608:1080:x='if(lt(t,2),0,1200)':y=0`, same check.
6. Scale: 60 s source, 300-line command file with x on every 0.2 s.

## Result

| Check | `sendcmd` | `if()` fallback |
|---|---|---|
| Frame at t=1 | red, mean (252,0,0) | red |
| Frame at t=3 | blue, mean (0,0,253) | blue |
| Switch frame | frame 60 = 2.000 s, frame 59 still red | not measured |
| 300-line file, 60 s, ultrafast | 7.6 s wall, no error | not measured |

The switch is frame-exact. Output size is 1080x1920. The command file uses a
bare filename with cwd set to the job directory, the same as the ASS file.

## Notes for F2

- `ffmpeg -filters` lists `crop` without a `C` flag on this build. The filter
  still accepts `x` through `sendcmd`. Do not gate on the flag.
- Each `sendcmd` line ends with `;`. Times are seconds with a decimal point.
- One `x` per plan sample is fine. A 60 s clip at 5 fps is 300 lines.
- Keep `crop=W:H:0:0` literal. `sendcmd` overrides only `x`.
