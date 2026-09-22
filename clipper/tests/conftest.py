"""Test config: ensure the project root is importable."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Never read the developer's real .env (Issue #2): app.main loads it at import,
# so redirect the path before any test module imports app.main.
import app.env

app.env.DOTENV_PATH = Path(__file__).resolve().parent / "fixtures" / "no-such.env"
