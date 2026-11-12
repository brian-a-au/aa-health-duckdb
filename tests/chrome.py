"""Open a report in headless Chrome and read what a probe script finds in the page.

The probe is the body of a JavaScript function that runs after the page script. It returns a
value or a Promise of one, and the test gets that value back as Python data. The page runs in an iframe inside an
outer page, because headless Chrome will not make its window narrower than 500 px. The iframe
sends the result to the outer page with postMessage, and the outer page writes it into a <pre>
element. Chrome prints the outer page with --dump-dom when the page is done.

Rarely, Chrome runs on and never prints the page. Then run_probe kills it with every process it
started and starts it again, up to ATTEMPTS times. It warns about each new start.

Set AHD_CHROME to the path of Chrome or Chromium to choose the program. Without Chrome the tests
skip, except when the CI variable is set, e.g., on GitHub Actions. There they fail.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import IO, Any

import pytest

NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
MAC_APPS = (
    "Google Chrome.app/Contents/MacOS/Google Chrome",
    "Chromium.app/Contents/MacOS/Chromium",
)
WINDOWS_APP = Path("Google") / "Chrome" / "Application" / "chrome.exe"
# Chrome stops when the page has had this much time. It is Chrome's own page clock, which runs
# as fast as the page allows, so the tests do not wait for it.
VIRTUAL_TIME_MS = 10000
# A normal page takes about a second. On GitHub Actions, headless Chrome has twice run for the
# full guard of 120 seconds without printing the page, and the same test passed when it ran
# again. So a Chrome that runs longer than this is killed and started again with a new profile,
# up to ATTEMPTS starts in all. Any other failure fails the test at once.
ATTEMPT_TIMEOUT_S = 40
ATTEMPTS = 3
# A script that marks every error and every request the CSP blocked, before the page script runs.
CATCHER = (
    "<script>window.__probeErrors=[];"
    "addEventListener('error',function(e){__probeErrors.push(String(e.message))});"
    "addEventListener('unhandledrejection',function(e){__probeErrors.push(String(e.reason))});"
    "document.addEventListener('securitypolicyviolation',function(e){"
    "__probeErrors.push('CSP blocked '+e.violatedDirective+' '+e.blockedURI)});</script>"
)
# The probe may return a value or a Promise of one. Errors are read when the result is sent,
# so an error that happens while the Promise is open is counted too.
PROBE = (
    "<script>(function(){{function post(m){{m.errors=window.__probeErrors;"
    "parent.postMessage(JSON.stringify(m),'*');}}"
    "function fail(e){{post({{probe_error:String(e&&e.stack||e)}});}}"
    "try{{Promise.resolve((function(){{\n{probe}\n}})())"
    ".then(function(r){{post({{result:r}});}},fail);}}catch(e){{fail(e);}}}})();</script>"
)
OUTER = (
    "<!doctype html><html><head><meta charset='utf-8'></head><body style='margin:0'>"
    "<iframe src='report.html' style='width:{width}px;height:{height}px;border:0'></iframe>"
    "<pre id='probe'></pre><script>addEventListener('message',function(e){{"
    "document.getElementById('probe').textContent=e.data;}});</script></body></html>"
)
CSP_META = re.compile(r'<meta http-equiv="Content-Security-Policy"[^>]*>\n?')


def find_chrome() -> str | None:
    """The path of Chrome or Chromium, or None when there is none."""
    chosen = os.environ.get("AHD_CHROME")
    if chosen:
        if not Path(chosen).is_file():
            pytest.fail(f"AHD_CHROME is set to {chosen}, but there is no file there.")
        return chosen
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    places: list[Path] = []
    if sys.platform == "darwin":
        for apps in (Path("/Applications"), Path.home() / "Applications"):
            places += [apps / app for app in MAC_APPS]
    if sys.platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(var):
                places.append(Path(os.environ[var]) / WINDOWS_APP)
    return next((str(p) for p in places if p.is_file()), None)


def chrome_or_skip() -> str:
    """The Chrome path. Skips the test without Chrome, or fails it when CI is set."""
    path = find_chrome()
    if not path:
        message = "Chrome or Chromium was not found. Set AHD_CHROME to its path."
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return path


def without_csp(page: str) -> str:
    """The page without its Content Security Policy, so that a probe may use new Function."""
    out, count = CSP_META.subn("", page)
    assert count == 1, "the report should have exactly one CSP meta tag"
    return out


def chrome_command(chrome: str, outer: Path, profile: Path, width: int, height: int) -> list[str]:
    """The command that opens the outer page in headless Chrome and prints it when done."""
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-extensions",
        # On macOS, Chrome reads the keychain in HOME. The tests set HOME to an empty folder,
        # and without this flag each start took about 10 seconds longer.
        "--use-mock-keychain",
        "--allow-file-access-from-files",
        f"--user-data-dir={profile}",
        f"--window-size={max(width, 500) + 100},{height + 100}",
        f"--virtual-time-budget={VIRTUAL_TIME_MS}",
        "--dump-dom",
        outer.as_uri(),
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")  # Chrome refuses to start as root with its sandbox on
    return cmd


def launch(cmd: list[str], out: IO[str], err: IO[str]) -> subprocess.Popen[bytes]:
    """Start a program in a process group of its own, so that kill_tree can stop all of it."""
    if sys.platform == "win32":
        return subprocess.Popen(
            cmd, stdout=out, stderr=err, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
    return subprocess.Popen(cmd, stdout=out, stderr=err, start_new_session=True)


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill a program that launch started, and every process it started."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False, capture_output=True
        )
        proc.kill()  # in case taskkill could not stop it
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # every process of the group has stopped already


def start_chrome(cmd: list[str], folder: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run Chrome once and return its exit code and what it printed.

    Chrome writes to files in the folder, not to pipes. A process that Chrome started and that
    is still running then cannot keep this function waiting. When Chrome runs longer than the
    timeout, this kills it with every process it started and raises subprocess.TimeoutExpired.
    """
    out_path, err_path = folder / "stdout.txt", folder / "stderr.txt"
    with (
        out_path.open("w", encoding="utf-8") as out,
        err_path.open("w", encoding="utf-8") as err,
    ):
        proc = launch(cmd, out, err)
        try:
            code = proc.wait(timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            proc.wait()
            stderr = err_path.read_text(encoding="utf-8", errors="replace")
            raise subprocess.TimeoutExpired(cmd, timeout, stderr=stderr) from None
    return subprocess.CompletedProcess(
        cmd,
        code,
        out_path.read_text(encoding="utf-8", errors="replace"),
        err_path.read_text(encoding="utf-8", errors="replace"),
    )


def run_probe(
    chrome: str, page: str, probe: str, folder: Path, width: int = 1280, height: int = 900
) -> Any:
    """Load the page at this width, run the probe and return its result.

    Fails the test when the page or the probe raised a JavaScript error.
    """
    folder.mkdir(parents=True, exist_ok=True)
    assert page.count("<body>") == 1 and page.count("</body>") == 1
    page = page.replace("<body>", "<body>" + CATCHER, 1)
    page = page.replace("</body>", PROBE.format(probe=probe) + "</body>", 1)
    (folder / "report.html").write_text(page, encoding="utf-8")
    outer = folder / "outer.html"
    outer.write_text(OUTER.format(width=width, height=height), encoding="utf-8")
    hung: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        # Each start has a new folder for the profile and the output, also when a test calls
        # run_probe twice with one folder. A Chrome that hung may have left a lock or half
        # written files in its profile.
        start = Path(tempfile.mkdtemp(prefix=f"chrome{attempt}-", dir=folder))
        cmd = chrome_command(chrome, outer, start / "profile", width, height)
        try:
            done = start_chrome(cmd, start, ATTEMPT_TIMEOUT_S)
            break
        except subprocess.TimeoutExpired as e:
            hung.append(f"attempt {attempt} stderr: {str(e.stderr or '')[-2000:]}")
            if attempt < ATTEMPTS:
                warnings.warn(
                    f"Chrome ran for {ATTEMPT_TIMEOUT_S} s without printing the page and was "
                    f"started again.\n{hung[-1]}",
                    stacklevel=2,
                )
    else:
        pytest.fail(
            f"Chrome ran for {ATTEMPT_TIMEOUT_S} s without printing the page on all "
            f"{ATTEMPTS} attempts.\n" + "\n".join(hung)
        )
    found = re.search(r'<pre id="probe">(.*?)</pre>', done.stdout, re.DOTALL)
    if not found or not found.group(1):
        pytest.fail(
            f"Chrome returned no probe result (exit {done.returncode}).\n"
            f"stderr: {done.stderr[-2000:]}\nstdout: {done.stdout[-2000:]}"
        )
    msg = json.loads(html.unescape(found.group(1)))
    assert "probe_error" not in msg, msg["probe_error"]
    assert msg["errors"] == [], f"JavaScript errors in the page: {msg['errors']}"
    return msg["result"]
