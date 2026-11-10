"""Run the companion's real JavaScript privacy contracts in a headless browser."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def browser():
    candidates = [shutil.which(name) for name in ("msedge", "chromium", "chromium-browser", "google-chrome")]
    if os.name == "nt":
        for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            if os.environ.get(variable):
                candidates.append(str(Path(os.environ[variable]) / "Microsoft/Edge/Application/msedge.exe"))
    return next((str(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)


def check(executable, page):
    with tempfile.TemporaryDirectory(prefix="pyrogenesis-contract-") as profile:
        args = [executable, "--headless", "--disable-gpu", "--no-first-run",
                "--disable-background-networking", "--allow-file-access-from-files",
                f"--user-data-dir={profile}", "--dump-dom", page.as_uri()]
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        match = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
        message = match.group(1) if match else "No contract result"
        return page.name, result.returncode == 0 and message.startswith("PASS:"), message


def main():
    executable = browser()
    if not executable:
        raise SystemExit("Install Edge or Chromium to run the JavaScript contracts")
    pages = sorted((ROOT / "mod/tests").glob("*_contract.html"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda page: check(executable, page), pages))
    for name, passed, message in results:
        print(f"{name}: {message}")
    return 0 if results and all(passed for _, passed, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
