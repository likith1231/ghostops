"""Test-wide setup: no test may touch the real knowledge base.

Runs before any test module imports ``agents.knowledge_base``, which reads
GHOSTOPS_DB_PATH at import time.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ghostops-tests-")
os.environ["GHOSTOPS_DB_PATH"] = os.path.join(_tmp, "incidents.db")
