"""The Slate-styled local review UI (D7, FR-8): the human select-and-approve gate.

A FastAPI app + vanilla-JS frontend that lets the maintainer browse scored
candidate slices, preview each moment's video window, tighten the intended in/out,
and Select / Reject. No auto-select; it reads and annotates the local library and
serves local media — it never posts, publishes, or uploads content.
"""
