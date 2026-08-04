"""pytest conftest for anaconda-webui tests.

Enables pytest to collect and run the existing unittest-based check-* files
without modifications. Generates .py symlinks for files without extensions,
sets up testlib.opts, and pre-creates a global machine for nondestructive tests.
"""

import os
import sys
from pathlib import Path

import pytest

# Set up PYTHONPATH for test imports
TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent
sys.path.insert(0, str(TEST_DIR / "common"))
sys.path.insert(0, str(ROOT_DIR / "bots"))

os.environ.setdefault("TEST_OS", "fedora-rawhide-boot")
os.environ.setdefault("TEST_VM_CACHE", "1")
os.environ.setdefault("TEST_ATTACHMENTS", str(ROOT_DIR / "tmp" / "testlogs"))
os.environ["TEST_ALLOW_NOLOGIN"] = "true"


def pytest_addoption(parser):
    parser.addoption("--disable-vm-cache", action="store_true", default=False,
                     help="Disable VM snapshot cache (fresh boot for each test)")
    parser.addoption("--show-browser", action="store_true", default=False,
                     help="Show the browser window during tests")
    parser.addoption("--no-pixel-tests", action="store_true", default=False,
                     help="Skip pixel (screenshot) comparison tests")


def pytest_configure(config):
    if config.getoption("--disable-vm-cache", default=False):
        os.environ["TEST_VM_CACHE"] = "0"
    if config.getoption("--show-browser", default=False):
        os.environ["TEST_SHOW_BROWSER"] = "1"
    if config.getoption("--no-pixel-tests", default=False):
        os.environ["TEST_NO_PIXEL_TESTS"] = "1"

    """Create .py symlinks for check-* files so pytest can collect them."""
    for check_file in TEST_DIR.glob("check-*"):
        if check_file.suffix or check_file.is_dir():
            continue
        link = check_file.with_name(check_file.name.replace("-", "_") + ".py")
        if not link.exists():
            link.symlink_to(check_file.name)


# def pytest_unconfigure(config):
#     """Clean up .py symlinks."""
#     for link in TEST_DIR.glob("check_*.py"):
#         if link.is_symlink():
#             link.unlink()



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

    from testlib import TEST_DIR as TESTLIB_DIR
    from testlib import attach

    attach(os.path.join(TESTLIB_DIR, "common/pixeldiff.html"))
    attach(os.path.join(TESTLIB_DIR, "common/link-patterns.json"))


@pytest.fixture(autouse=True, scope="session")
def _global_machine(tmp_path_factory, worker_id):
    """Pre-create the global machine so all nondestructive tests share it.

    With xdist, a FileLock serializes VM creation so workers boot one at
    a time. This prevents port races (VirtNetwork uses file locking but
    the HTTP server doesn't) and avoids I/O contention from simultaneous
    VM boots.
    """
    from anacondalib import VirtInstallMachineCase
    from testlib import MachineCase

    if worker_id == "master":
        worker_num = 0
    else:
        worker_num = int(worker_id.replace("gw", ""))

    image = os.environ.get("TEST_OS", "fedora-rawhide-boot")
    label = f"anaconda-test-{image}-w{worker_num}"

    # Check for stale domain
    import libvirt

    conn = libvirt.open("qemu:///session")
    try:
        dom = conn.lookupByName(label)
        if dom.isActive():
            pytest.exit(
                f"Domain '{label}' is already running. "
                "Stop it before running tests (anadev test clean).",
                returncode=1,
            )
    except libvirt.libvirtError:
        pass
    finally:
        conn.close()

    case = VirtInstallMachineCase()
    case._testMethodName = "__pytest_session__"

    machine = case.new_machine(restrict=True, cleanup=False, label=label)
    machine.start()

    MachineCase.global_machine = machine

    yield machine

    machine.kill()
