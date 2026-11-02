#!/usr/bin/env python3
"""Offline source checks. This script never contacts Azure or GitHub."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    for path in ROOT.rglob("*.py"):
        if any(part in (".git", ".venv", "dist") for part in path.parts):
            continue
        ast.parse(path.read_text(), filename=str(path))
    for path in (ROOT / "azuredeploy.json", ROOT / "deployment-config.json"):
        json.loads(path.read_text())
    source = (ROOT / "guest-bootstrap.sh").read_text()
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", source, re.S)
    for index, block in enumerate(blocks, 1):
        compile(block, f"bootstrap-python-{index}", "exec")
    commands = [
        ["bash", "-n", "guest-bootstrap.sh"],
        [sys.executable, "scripts/generate_template.py", "--check"],
        [sys.executable, "scripts/links.py", "--check"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    ]
    for command in commands:
        print("CHECK:", " ".join(command), flush=True)
        subprocess.run(command, cwd=ROOT, check=True)
    print(f"PASS: offline validation; {len(blocks)} embedded Python blocks compiled. Azure and real kernel execution were not performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
