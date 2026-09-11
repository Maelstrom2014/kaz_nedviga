"""Run all scheduler tests with log suppression; print exit code."""
import subprocess
import sys
from pathlib import Path

r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_scheduler.py",
     "--tb=line", "-q", "--log-cli-level=ERROR", "--log-level=ERROR"],
    cwd=str(Path(__file__).resolve().parent),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, errors="replace", timeout=180,
)
# Print only summary lines.
out = r.stdout or ""
for line in out.splitlines():
    if any(k in line for k in ("passed", "failed", "error", "PASS", "FAIL",
                                "====", "====", "Assertion")):
        print(line)
print("EXIT_CODE:", r.returncode)
