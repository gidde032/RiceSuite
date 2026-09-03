"""Runtime registry of collected smoke-marked tests.

``conftest.py`` fills ``SMOKE_NODEIDS`` during collection; a regression test in
``test_smoke.py`` asserts its exact length, making the fast-feedback tier a
contract rather than a convention (see bootstrap Step 4).
"""

SMOKE_NODEIDS: list[str] = []
