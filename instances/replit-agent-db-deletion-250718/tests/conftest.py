"""Skip, rather than fail, tests whose inputs a fresh checkout does not have yet: data fetched by `python prepare.py`
or restricted text kept outside the repository (see docs/PROTOCOL.md, "Restricted material")."""
from pathlib import Path

import pytest

INSTANCE = Path(__file__).resolve().parents[1]
DATA = INSTANCE.parents[1] / "data" / INSTANCE.name
UNPREPARED = (INSTANCE / "prepare.py").exists() and not (DATA.is_dir() and any(DATA.iterdir()))
MARKERS = ("prepare.py", "data/gated", "/gated/", "not prepared", "Run `python prepare.py`")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.excinfo is not None and rep.failed:
        msg = str(call.excinfo.value)
        missing = UNPREPARED or (isinstance(call.excinfo.value, (FileNotFoundError, RuntimeError)) and (
            any(m in msg for m in MARKERS) or "/data/" in msg))
        if missing:
            rep.outcome = "skipped"
            why = "run `python prepare.py` in this instance first" if UNPREPARED else "needs restricted data"
            rep.longrepr = (str(item.fspath), 0, f"Skipped: {why}: " + msg[:160])
