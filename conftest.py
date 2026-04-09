"""Repository-root pytest bootstrap.

Ensures local repo imports and test-environment defaults are available even when
pytest is invoked via the project virtualenv entrypoint in automation.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "development")
