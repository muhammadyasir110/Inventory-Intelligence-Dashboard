"""
Imported FIRST by every test module. Points the application at a throw-away
SQLite database so tests can never read or modify backend/database/iid_local.db,
and uses a low PBKDF2 iteration count so the suite stays fast.
"""
import atexit
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="iip_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ.setdefault("IIP_ENV", "development")
os.environ["IIP_PBKDF2_ITERATIONS"] = "1000"
os.environ.pop("IIP_ADMIN_PASSWORD", None)
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if "database.db" in sys.modules:  # pragma: no cover - defensive
    raise RuntimeError("tests/_testenv must be imported before database.db")

SAMPLE_CSV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                          "sample_data", "sample_inventory.csv"))
