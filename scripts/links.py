#!/usr/bin/env python3
"""Generate and check the documented Azure template deployment URL."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- deploy-button:start -->"
END = "<!-- deploy-button:end -->"


def config():
    data = json.loads((ROOT / "deployment-config.json").read_text())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*", data["repository"]):
        raise ValueError("Invalid GitHub repository name")
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-rc\.\d+)?", data["deploymentVersion"]):
        raise ValueError("Invalid deployment version")
    return data


def raw_template(data):
    return f"https://raw.githubusercontent.com/{data['repository']}/{data['deploymentVersion']}/azuredeploy.json"


def portal_url(data):
    return "https://portal.azure.com/#create/Microsoft.Template/uri/" + quote(raw_template(data), safe="")


def button(data):
    return f"{START}\n[![Deploy to Azure](https://aka.ms/deploytoazurebutton)]({portal_url(data)})\n{END}"


def check_or_write(check=False):
    readme = ROOT / "README.md"
    text = readme.read_text()
    pattern = re.escape(START) + r".*?" + re.escape(END)
    if len(re.findall(pattern, text, re.S)) != 1:
        raise ValueError("README needs exactly one deployment button block")
    updated = re.sub(pattern, lambda _: button(config()), text, flags=re.S)
    if check and updated != text:
        raise ValueError("Deployment button drift. Run python3 scripts/links.py")
    if not check:
        readme.write_text(updated)
    return portal_url(config())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(check_or_write(args.check))
