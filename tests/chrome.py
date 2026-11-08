"""Open a report in headless Chrome and read what a probe script finds in the page.

The probe is the body of a JavaScript function that runs after the page script. It returns a
value, and the test gets that value back as Python data. The page runs in an iframe inside an
outer page, because headless Chrome will not make its window narrower than 500 px. The iframe
sends the result to the outer page with postMessage, and the outer page writes it into a <pre>
element. Chrome prints the outer page with --dump-dom when the page is done.

Set AHD_CHROME to the path of Chrome or Chromium to choose the program. Without Chrome the tests
skip, except when the CI variable is set, e.g., on GitHub Actions. There they fail.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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
# Only a guard against a Chrome that hangs. A normal page takes about a second.
HANG_GUARD_S = 120
# A script that marks every error and every request the CSP blocked, before the page script runs.
CATCHER = (
    "<script>window.__probeErrors=[];"
    "addEventListener('error',function(e){__probeErrors.push(String(e.message))});"
    "addEventListener('unhandledrejection',function(e){__probeErrors.push(String(e.reason))});"
    "document.addEventListener('securitypolicyviolation',function(e){"
    "__probeErrors.push('CSP blocked '+e.violatedDirective+' '+e.blockedURI)});</script>"
)
PROBE = (
    "<script>(function(){{var msg;"
    "try{{msg={{result:(function(){{\n{probe}\n}})(),errors:window.__probeErrors}};}}"
    "catch(e){{msg={{probe_error:String(e&&e.stack||e),errors:window.__probeErrors}};}}"
    "parent.postMessage(JSON.stringify(msg),'*');}})();</script>"
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
    if path:
        return path
    message = "Chrome or Chromium was not found. Set AHD_CHROME to its path."
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def without_csp(page: str) -> str:
    """The page without its Content Security Policy, so that a probe may use new Function."""
    out, count = CSP_META.subn("", page)
    assert count == 1, "the report should have exactly one CSP meta tag"
    return out


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
        f"--user-data-dir={folder / 'profile'}",
        f"--window-size={max(width, 500) + 100},{height + 100}",
        f"--virtual-time-budget={VIRTUAL_TIME_MS}",
        "--dump-dom",
        outer.as_uri(),
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")  # Chrome refuses to start as root with its sandbox on
    done = subprocess.run(
        cmd, check=False, capture_output=True, encoding="utf-8", timeout=HANG_GUARD_S
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
