"""pytest conftest for anaconda-webui tests.

Enables pytest to collect and run the existing unittest-based check-* files
without modifications. Generates .py symlinks for files without extensions,
sets up testlib.opts, and filters out non-test functions.
"""

import os
import sys
from pathlib import Path

# Set up PYTHONPATH for test imports
TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent
sys.path.insert(0, str(TEST_DIR / "common"))
sys.path.insert(0, str(ROOT_DIR / "bots"))

os.environ.setdefault("TEST_OS", "fedora-rawhide-boot")
os.environ["TEST_ALLOW_NOLOGIN"] = "true"


def pytest_configure(config):
    """Create .py symlinks for check-* files so pytest can collect them."""
    for check_file in TEST_DIR.glob("check-*"):
        if check_file.suffix or check_file.is_dir():
            continue
        link = check_file.with_name(check_file.name.replace("-", "_") + ".py")
        if not link.exists():
            link.symlink_to(check_file.name)


def pytest_unconfigure(config):
    """Clean up .py symlinks."""
    for link in TEST_DIR.glob("check_*.py"):
        if link.is_symlink():
            link.unlink()


def pytest_collection_modifyitems(items):
    """Filter out test_main() which is an entry point, not a test."""
    items[:] = [i for i in items if i.name != "test_main"]


def pytest_sessionstart(session):
    """Set up testlib.opts for the test session."""
    import testlib

    testlib.opts.attachments = os.environ.get("TEST_ATTACHMENTS")
    if testlib.opts.attachments:
        os.makedirs(testlib.opts.attachments, exist_ok=True)

    testlib.opts.trace = bool(os.environ.get("TEST_TRACE"))
    testlib.opts.sit = False
    testlib.opts.coverage = False
    testlib.opts.fetch = False

    # testlib.setUp writes /etc/cockpit/cockpit.conf but the directory
    # may not exist in the installer environment
    testlib.opts.tests = []

    from testlib import attach, TEST_DIR as TESTLIB_DIR

    attach(os.path.join(TESTLIB_DIR, "common/pixeldiff.html"))
    attach(os.path.join(TESTLIB_DIR, "common/link-patterns.json"))
