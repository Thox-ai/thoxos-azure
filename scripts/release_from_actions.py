#!/usr/bin/env python3
"""Publish an engineering prerelease from the exact validated main push.

Run only in the release job that needs the read-only validation job. Uses the
job's repository-scoped GH_TOKEN through gh; no user token or /user API is used.
Never moves tags, replaces assets, edits published releases, or deploys Azure.
An interrupted matching draft can resume by adding only missing verified assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from package_release import package

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Thox-ai/thoxos-azure"
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"v\d+\.\d+\.\d+(?:-rc\.\d+)?")


def run(*args, input_text=None, check=True):
    result = subprocess.run(list(args), cwd=ROOT, input=input_text,
                            text=True, capture_output=True, timeout=180)
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({' '.join(args[:3])}): {result.stderr.strip()[:2000]}")
    return result


def api(endpoint, *, method="GET", data=None, missing_ok=False, pages=False):
    args = ["gh", "api", "--hostname", "github.com", "--method", method,
            "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
    if data is not None:
        args += ["--input", "-"]
    if pages:
        args += ["--paginate", "--slurp"]
    result = run(*args, endpoint, input_text=json.dumps(data) if data is not None else None, check=False)
    if result.returncode:
        if missing_ok and re.search(r"\bHTTP 404\b", result.stderr):
            return None
        raise RuntimeError(f"GitHub API request failed for {endpoint}: {result.stderr.strip()[:2000]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def check_actions_context(environment, event, expected, config):
    if not SHA.fullmatch(expected):
        raise RuntimeError("Expected commit must be a full lowercase Git SHA")
    required = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_SHA": expected}
    if any(environment.get(k) != v for k, v in required.items()):
        raise RuntimeError("Release is restricted to this repository's exact main push in GitHub Actions")
    if not environment.get("GH_TOKEN"):
        raise RuntimeError("The release job must supply its scoped GH_TOKEN")
    if (not isinstance(event, dict) or event.get("ref") != "refs/heads/main"
            or event.get("after") != expected or event.get("deleted") is not False
            or (event.get("repository") or {}).get("full_name") != REPOSITORY):
        raise RuntimeError("Push event does not identify the expected repository and commit")
    if config.get("repository") != REPOSITORY or not VERSION.fullmatch(str(config.get("deploymentVersion", ""))):
        raise RuntimeError("Deployment configuration does not identify an authorized version in this repository")


def require_tag_commit(ref, expected, resolve):
    """Resolve lightweight/annotated tags without allowing a tag rewrite."""
    obj = ref.get("object") or {}
    visited = set()
    for _ in range(6):
        sha = obj.get("sha", "")
        if not SHA.fullmatch(sha) or sha in visited:
            raise RuntimeError("Invalid or cyclic version tag")
        visited.add(sha)
        if obj.get("type") == "commit":
            if sha != expected:
                raise RuntimeError("Version tag belongs to another commit. Bump deploymentVersion; issued tags are never rewritten")
            return sha
        if obj.get("type") != "tag":
            break
        obj = (resolve(sha) or {}).get("object") or {}
    raise RuntimeError("Version tag did not resolve to the expected commit")


def assess_release(release, version, title, body, expected_names):
    """Return missing assets only for an exactly matching, resumable draft."""
    if (release.get("tag_name") != version or release.get("name") != title
            or release.get("body") != body or release.get("prerelease") is not True
            or type(release.get("draft")) is not bool):
        raise RuntimeError("Existing release metadata differs from this source; no existing release is overwritten")
    assets = release.get("assets")
    if not isinstance(assets, list) or any(not isinstance(a, dict) for a in assets):
        raise RuntimeError("Invalid release asset inventory")
    names = [a.get("name") for a in assets]
    if len(set(names)) != len(names) or set(names) - expected_names:
        raise RuntimeError("Unexpected or duplicate release assets; nothing is overwritten or removed")
    missing = expected_names - set(names)
    if missing and not release["draft"]:
        raise RuntimeError("Published release is incomplete; missing assets are not silently added to an issued release")
    return missing


def get_release(version):
    # Listing with the authenticated repository token includes matching drafts,
    # allowing an interrupted workflow to resume without creating a duplicate.
    pages = api(f"repos/{REPOSITORY}/releases?per_page=100", pages=True)
    if not isinstance(pages, list) or any(not isinstance(p, list) for p in pages):
        raise RuntimeError("Invalid paginated release inventory")
    matches = [r for page in pages for r in page if r.get("tag_name") == version]
    if len(matches) > 1:
        raise RuntimeError("More than one release claims this tag")
    return matches[0] if matches else None


def check_remote_main(expected):
    remote = api(f"repos/{REPOSITORY}/git/ref/heads/main")
    if (remote.get("object") or {}).get("type") != "commit" or remote["object"].get("sha") != expected:
        raise RuntimeError("Public main moved away from the validated commit; no further publication mutation is allowed")


def verify_asset_bytes(version, release, paths):
    expected = {p.name: p for p in paths}
    for asset in release["assets"]:
        if asset["name"] not in expected:
            raise RuntimeError("Unexpected asset during verification")
        path = expected[asset["name"]]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if asset.get("state") != "uploaded" or asset.get("size") != path.stat().st_size:
            raise RuntimeError("Release asset size/state differs: " + path.name)
        if asset.get("digest") not in (None, "sha256:" + digest):
            raise RuntimeError("GitHub asset digest differs: " + path.name)
        with tempfile.TemporaryDirectory(prefix="thox-actions-readback-") as temporary:
            run("gh", "release", "download", version, "--repo", REPOSITORY,
                "--dir", temporary, "--pattern", path.name)
            if (Path(temporary) / path.name).read_bytes() != path.read_bytes():
                raise RuntimeError("Downloaded release asset bytes differ: " + path.name)


def verify_raw_template(version):
    expected = (ROOT / "azuredeploy.json").read_bytes()
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{version}/azuredeploy.json"
    failure = None
    for attempt in range(6):
        try:
            with urlopen(url, timeout=30) as response:
                if response.status != 200 or response.read(1024 * 1024 + 1) != expected:
                    raise RuntimeError("Versioned public ARM template differs from the validated source")
            return {"url": url, "sha256": hashlib.sha256(expected).hexdigest()}
        except Exception as exc:
            failure = exc
            if attempt < 5:
                time.sleep(3)
    raise RuntimeError("Public raw-template readback failed: " + str(failure))


def publish(expected, config):
    version = config["deploymentVersion"]
    head = run("git", "rev-parse", "HEAD").stdout.strip()
    if head != expected or run("git", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise RuntimeError("Checkout is not the clean, exact validated commit")
    remote = api(f"repos/{REPOSITORY}")
    if (remote.get("full_name") != REPOSITORY or remote.get("private") is not False
            or remote.get("archived") or remote.get("disabled") or remote.get("fork")
            or remote.get("default_branch") != "main"):
        raise RuntimeError("Repository identity, public visibility or active-main policy differs")
    check_remote_main(expected)
    archive, checksum = package()
    paths = (archive, checksum)
    expected_names = {p.name for p in paths}
    title = f"ThoxOS Azure Deployment {version}"
    body = ((ROOT / "docs/RELEASE_NOTES.md").read_text().rstrip() +
            f"\n\n<!-- thox-actions-release-v1 source={expected} -->\n")
    tag_endpoint = f"repos/{REPOSITORY}/git/ref/tags/{version}"
    ref = api(tag_endpoint, missing_ok=True)
    release = get_release(version)
    if release is not None and ref is None:
        raise RuntimeError("An existing release has no corresponding version tag; inspect it manually")
    if ref is not None:
        require_tag_commit(ref, expected, lambda sha: api(f"repos/{REPOSITORY}/git/tags/{sha}"))
    if release is not None:
        assess_release(release, version, title, body, expected_names)
        verify_asset_bytes(version, release, paths)
    originally_published = release is not None and not release["draft"]

    if ref is None:
        check_remote_main(expected)
        api(f"repos/{REPOSITORY}/git/refs", method="POST",
            data={"ref": "refs/tags/" + version, "sha": expected})
        ref = api(tag_endpoint)
        require_tag_commit(ref, expected, lambda sha: api(f"repos/{REPOSITORY}/git/tags/{sha}"))
    if release is None:
        with tempfile.TemporaryDirectory(prefix="thox-release-notes-") as temporary:
            notes = Path(temporary) / "notes.md"
            notes.write_text(body)
            run("gh", "release", "create", version, "--repo", REPOSITORY,
                "--verify-tag", "--draft", "--prerelease", "--latest=false",
                "--title", title, "--notes-file", str(notes))
        release = get_release(version)
        if release is None:
            raise RuntimeError("Created draft was not returned by GitHub; no publication is inferred")
    missing = assess_release(release, version, title, body, expected_names)
    if missing:
        # gh refuses duplicate asset names unless --clobber is explicitly used.
        # This publisher never supplies that flag or deletes any remote object.
        run("gh", "release", "upload", version, *(str(p) for p in paths if p.name in missing),
            "--repo", REPOSITORY)
        release = get_release(version)
        if release is None or assess_release(release, version, title, body, expected_names):
            raise RuntimeError("Uploaded draft assets did not read back as complete")
    verify_asset_bytes(version, release, paths)
    raw = verify_raw_template(version)
    if release["draft"]:
        check_remote_main(expected)
        require_tag_commit(api(tag_endpoint), expected, lambda sha: api(f"repos/{REPOSITORY}/git/tags/{sha}"))
        run("gh", "release", "edit", version, "--repo", REPOSITORY,
            "--draft=false", "--prerelease", "--latest=false", "--verify-tag")
    published = get_release(version)
    if published is None or published.get("draft") is not False:
        raise RuntimeError("Release is not published; no completion is inferred")
    if assess_release(published, version, title, body, expected_names):
        raise RuntimeError("Published release assets are incomplete")
    require_tag_commit(api(tag_endpoint), expected, lambda sha: api(f"repos/{REPOSITORY}/git/tags/{sha}"))
    verify_asset_bytes(version, published, paths)
    raw = verify_raw_template(version)
    return {"schema": "thox.azure.actions-publication.v1", "status": "verified-existing" if originally_published else "published-and-verified",
            "repository": REPOSITORY, "version": version, "source_commit": expected,
            "release_url": published["html_url"], "release_id": published["id"],
            "prerelease": True, "raw_template": raw,
            "assets": {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for p in paths},
            "azure_deployment": "not_run", "real_kernel_boot": "not_run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    receipt = {"schema": "thox.azure.actions-publication.v1", "status": "failed",
               "repository": REPOSITORY, "source_commit": args.expected_commit,
               "azure_deployment": "not_run", "real_kernel_boot": "not_run"}
    code = 1
    try:
        config = json.loads((ROOT / "deployment-config.json").read_text())
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        check_actions_context(os.environ, event, args.expected_commit, config)
        receipt = publish(args.expected_commit, config)
        code = 0
    except Exception as exc:
        receipt["error"] = str(exc)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    (output / "release-publication-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("## Engineering prerelease publication\n\n")
            summary.write("```json\n" + json.dumps(receipt, indent=2) + "\n```\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
