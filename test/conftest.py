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
os.environ["TEST_ALLOW_NOLOGIN"] = "true"


def pytest_configure(config):
    """Create .py symlinks for check-* files and cap xdist workers by RAM."""
    for check_file in TEST_DIR.glob("check-*"):
        if check_file.suffix or check_file.is_dir():
            continue
        link = check_file.with_name(check_file.name.replace("-", "_") + ".py")
        if not link.exists():
            link.symlink_to(check_file.name)

    # Cap xdist parallelism based on available RAM (4.5GB per VM)
    numprocesses = getattr(config.option, "numprocesses", None)
    if numprocesses is not None:
        try:
            import psutil

            avail_gb = psutil.virtual_memory().available / (1024**3)
            max_vms = max(1, int(avail_gb // 4.5))
            maxprocs = getattr(config.option, "maxprocesses", None)
            if maxprocs is None or maxprocs > max_vms:
                config.option.maxprocesses = max_vms
        except ImportError:
            pass


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


@pytest.fixture(autouse=True, scope="session")
def _global_machine(worker_id):
    """Pre-create the global machine so all nondestructive tests share it.

    Each xdist worker gets a deterministic label based on its worker
    number. VirtNetwork allocates ports with file locking to prevent
    conflicts between parallel workers.
    """
    from testlib import MachineCase

    from anacondalib import VirtInstallMachineCase

    if worker_id == "master":
        worker_num = 0
    else:
        worker_num = int(worker_id.replace("gw", ""))

    case = VirtInstallMachineCase()
    case._testMethodName = "__pytest_session__"

    image = os.environ.get("TEST_OS", "fedora-rawhide-boot")
    label = f"anaconda-test-{image}-w{worker_num}"

    import libvirt

    conn = libvirt.open("qemu:///session")
    try:
        dom = conn.lookupByName(label)
        if dom.isActive():
            pytest.exit(
                f"Domain '{label}' is already running. "
                "Stop it before running tests (anadev vm stop or virsh destroy).",
                returncode=1,
            )
    except libvirt.libvirtError:
        pass
    finally:
        conn.close()

    machine = case.new_machine(restrict=True, cleanup=False, label=label)

    machine.start()

    MachineCase.global_machine = machine

    yield machine

    machine.kill()
