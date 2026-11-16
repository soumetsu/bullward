"""Launch the installed Chromium browser with a disposable profile for SmokeTest."""

from contextlib import suppress
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def find_browser():
    override = os.environ.get("INVEST_SMOKE_BROWSER")
    if override:
        executable = shutil.which(override)
        if executable:
            return executable
        raise RuntimeError("INVEST_SMOKE_BROWSER does not point to an executable.")
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge", "microsoft-edge"):
        if executable := shutil.which(name):
            return executable
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if directory := os.environ.get(variable):
                for relative in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                    executable = Path(directory) / relative
                    if executable.is_file():
                        return str(executable)
    raise RuntimeError("SmokeTest needs an installed Chromium, Chrome, or Edge. Set INVEST_SMOKE_BROWSER to its path.")


def devtools_endpoint(profile, process):
    target = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Browser exited during startup with status {process.returncode}.")
        if target.exists():
            lines = target.read_text(encoding="ascii").splitlines()
            if len(lines) == 2 and lines[0].isdigit() and lines[1].startswith("/devtools/browser/"):
                return f"ws://127.0.0.1:{lines[0]}{lines[1]}"
        time.sleep(0.15)
    raise RuntimeError("Timed out waiting for the browser DevTools endpoint.")


def run_browser(node, url, directory, log):
    profile = directory / "browser"
    executable = find_browser()
    edge_arguments = ["--edge-skip-compat-layer-relaunch"] if Path(executable).name.lower() == "msedge.exe" else []
    print(f"Opening a disposable headless browser using {executable}", flush=True)
    process = subprocess.Popen(
        [executable, "--headless=new", f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
         "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
         "--disable-background-networking", "--disable-extensions", "--disable-sync", "--enable-logging=stderr",
         *edge_arguments, "about:blank"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        endpoint = devtools_endpoint(profile, process)
        driver = Path(__file__).with_name("workstation_browser.mjs")
        result = subprocess.run([node, str(driver), endpoint, url], timeout=180)
        if result.returncode:
            raise RuntimeError(f"Browser checks exited with status {result.returncode}.")
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, check=False, timeout=10)
            else:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
