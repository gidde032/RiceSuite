#!/bin/bash
cd "$(dirname "$0")"
echo "Starting RicePoster..."
# 127.0.0.1, not localhost: that is the address uvicorn actually binds below,
# and localhost can resolve to ::1 where nothing is listening.
echo "Open http://127.0.0.1:1738 in your browser"
echo ""
# No --reload: it restarts the server on any file change, which would kill an
# in-flight scheduled post mid-run (Phase 2 audit, finding #13). Restart by
# hand while developing.
# --loop asyncio --http h11: RicePoster's historical runtime. RiceSuite's shared
# environment also installs uvloop and httptools (Clipper's uvicorn[standard]),
# which uvicorn would otherwise pick automatically.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 1738 --loop asyncio --http h11
