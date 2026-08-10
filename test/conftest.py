"""pytest conftest for anaconda-webui tests.

Enables pytest to collect and run the existing unittest-based check-* files
without modifications. Generates .py symlinks for files without extensions,
sets up testlib.opts, and pre-creates a global machine for nondestructive tests.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TextIO

import libvirt
import pytest

# Set up PYTHONPATH for test imports
TEST_DIR = Path(__file__).parent
ROOT_DIR = TEST_DIR.parent
sys.path.insert(0, str(TEST_DIR / "common"))
sys.path.insert(0, str(ROOT_DIR / "bots"))

os.environ.setdefault("TEST_OS", "fedora-rawhide-boot")
os.environ.setdefault("TEST_VM_CACHE", "1")
os.environ.setdefault("TEST_ATTACHMENTS", str(ROOT_DIR / "tmp" / "testlogs"))
os.environ.setdefault("TEST_HTTP_PORT", "8100")
os.environ["TEST_ALLOW_NOLOGIN"] = "true"


def pytest_addoption(parser):
    parser.addoption("--disable-vm-cache", action="store_true", default=False, help="Disable VM snapshot cache (fresh boot for each test)")
    parser.addoption("--show-browser", action="store_true", default=False, help="Show the browser window during tests")
    parser.addoption("--no-pixel-tests", action="store_true", default=False, help="Skip pixel (screenshot) comparison tests")
    parser.addoption("--sit", action="store_true", default=False, help="Sit and wait after test failure")
    parser.addoption("--tap", action="store_true", default=False, help="Stream TAP output to stdout")


class TapReporter:
    """Stream TAP v14 output with YAML diagnostics.

    Based on pytest-tapreporter by Allison Karlitskaya.
    https://github.com/allisonkarlitskaya/pytest-tapreporter
    """

    def __init__(self, config: pytest.Config, output: TextIO):
        self.config = config
        self.output = output
        self.plan_printed = False
        self.reported: set[str] = set()

    def print_plan(self, n_tests: int) -> None:
        if not self.plan_printed:
            print(f"1..{n_tests}", file=self.output)
            self.output.flush()
            self.plan_printed = True

    def report(self, report: pytest.TestReport, status: str, directive: str = "", directive_reason: str = "", **kwargs: str) -> None:
        line = f"{status} {len(self.reported)} - {report.nodeid}"
        if directive:
            line += f" # {directive}"
            if directive_reason:
                line += f" {directive_reason}"

        lines = [line]

        try:
            kwargs["message"] = report.longrepr.reprcrash.message
            kwargs["traceback"] = report.longreprtext
        except AttributeError:
            pass

        if kwargs:
            lines.append("  ---")
            for key, value in kwargs.items():
                if "\n" in value:
                    lines.append(f"  {key}: |+")
                    lines.extend(f"    {ln}" for ln in value.splitlines())
                elif value:
                    lines.append(f"  {key}: {json.dumps(value)}")
            lines.append("  ...")

        print(*lines, sep="\n", file=self.output, flush=True)

    @pytest.hookimpl()
    def pytest_runtestloop(self, session: pytest.Session) -> None:
        self.print_plan(session.testscollected)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        del node
        self.print_plan(len(ids))

    @pytest.hookimpl()
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        category, _letter, _verbose = self.config.hook.pytest_report_teststatus(report=report, config=self.config)

        if category and report.nodeid not in self.reported:
            self.reported.add(report.nodeid)
        else:
            return

        if category == "passed":
            self.report(report, "ok")
        elif category == "skipped":
            assert isinstance(report.longrepr, tuple)
            reason = re.sub(r"^Skipped:? ?", "", report.longrepr[2])
            self.report(report, "ok", "SKIP", reason)
        elif category == "xfailed":
            assert isinstance(report.wasxfail, str)
            reason = re.sub(r"^reason:? ?", "", report.wasxfail)
            self.report(report, "not ok", "TODO", reason)
        elif category == "xpassed":
            assert isinstance(report.wasxfail, str)
            reason = re.sub(r"^reason:? ?", "", report.wasxfail)
            self.report(report, "ok", "TODO", reason)
        else:
            self.report(report, "not ok")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running, destructive, or E2E tests")

    if config.getoption("--disable-vm-cache", default=False):
        os.environ["TEST_VM_CACHE"] = "0"
    if config.getoption("--show-browser", default=False):
        os.environ["TEST_SHOW_BROWSER"] = "1"
    if config.getoption("--no-pixel-tests", default=False):
        os.environ["TEST_NO_PIXEL_TESTS"] = "1"
    if config.getoption("--tap", default=False):
        original_stdout = sys.stdout
        config.pluginmanager.register(TapReporter(config, original_stdout), "tapreporter")
        sys.stdout = open("/dev/null", "w")

    for check_file in TEST_DIR.glob("check-*"):
        if check_file.suffix or check_file.is_dir():
            continue
        link = check_file.with_name(check_file.name.replace("-", "_") + ".py")
        if not link.exists():
            link.symlink_to(check_file.name)


def pytest_collection_modifyitems(items):
    for item in items:
        method = getattr(item, "obj", None)
        cls = getattr(item, "cls", None)
        is_slow = (
            "_e2e" in item.nodeid
            or "Destructive" in (cls.__name__ if cls else "")
            or getattr(method, "_testlib__timeout", 0) > 600
            or getattr(cls, "_testlib__timeout", 0) > 600
        )
        if is_slow:
            item.add_marker(pytest.mark.slow)




def pytest_sessionstart(session):
    """Set up testlib.opts for the test session."""
    import testlib

    testlib.opts.attachments = os.environ.get("TEST_ATTACHMENTS")
    if testlib.opts.attachments:
        os.makedirs(testlib.opts.attachments, exist_ok=True)

    testlib.opts.trace = bool(os.environ.get("TEST_TRACE"))
    testlib.opts.sit = session.config.getoption("--sit", default=False)
    testlib.opts.coverage = False
    testlib.opts.fetch = False

    # testlib.setUp writes /etc/cockpit/cockpit.conf but the directory
    # may not exist in the installer environment
    testlib.opts.tests = []

    from testlib import TEST_DIR as TESTLIB_DIR
    from testlib import attach

    attach(os.path.join(TESTLIB_DIR, "common/pixeldiff.html"))
    attach(os.path.join(TESTLIB_DIR, "common/link-patterns.json"))


def _destroy_test_domains(prefix=None, undefine=False):
    """Destroy (and optionally undefine) test VM domains matching a prefix.

    If prefix is None, matches all anaconda-test domains.
    """
    conn = libvirt.open("qemu:///session")
    try:
        for dom in conn.listAllDomains():
            name = dom.name()
            if prefix:
                if not name.startswith(prefix):
                    continue
            elif not re.match(r"anaconda-test-.*-w\d+", name):
                continue
            if dom.isActive():
                dom.destroy()
            if undefine:
                if dom.hasManagedSaveImage(0):
                    dom.managedSaveRemove(0)
                try:
                    dom.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_KEEP_NVRAM)
                except libvirt.libvirtError:
                    try:
                        dom.undefine()
                    except libvirt.libvirtError:
                        pass
    except Exception:
        pass
    finally:
        conn.close()


def pytest_keyboard_interrupt(excinfo):
    subprocess.run(["pkill", "-f", "chromedriver"], check=False)
    _destroy_test_domains()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    yield
    attachments = os.environ.get("TEST_ATTACHMENTS")
    if not attachments:
        return
    import glob
    import shutil

    for pattern in ("Test*.png", "Test*.html", "Test*.js.log", "Test*.log.gz"):
        for f in glob.glob(pattern):
            dest = os.path.join(attachments, os.path.basename(f))
            if not os.path.exists(dest):
                shutil.move(f, dest)
    # _kill_pending_vms()


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

    _destroy_test_domains(prefix=label, undefine=True)

    case = VirtInstallMachineCase()
    case._testMethodName = "__pytest_session__"

    machine = case.new_machine(restrict=True, cleanup=False, label=label)
    machine.start()

    MachineCase.global_machine = machine

    yield machine

    _destroy_test_domains(prefix=label)
