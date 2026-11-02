#!/usr/bin/env python3
"""Create the authorized PUBLIC deployment repository, validate CI, and publish its release.

Uses the user's existing authenticated GitHub CLI. It never requests or prints
tokens, never force-pushes, and never changes an existing private repository's
visibility. No Azure resources are created by this command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from links import ROOT, check_or_write, config, portal_url, raw_template
from package_release import FILES, package

DESCRIPTION = "Deploy the ThoxInux open source engineering release on Azure with a verified QEMU guest and a Deploy to Azure button."


def run(*args, check=True):
    result = subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(args[:3])}\n{result.stderr.strip()}")
    return result


def gh_json(*args):
    return json.loads(run("gh", *args).stdout)


def git(*args):
    return run("git", *args).stdout.strip()


def git_push(*args):
    # Select the already authenticated gh identity for this operation only.
    # This does not alter global Git configuration or expose its credential.
    return run("git", "-c", "credential.helper=", "-c",
               "credential.https://github.com.helper=!gh auth git-credential", "push", "--no-follow-tags", *args)


def existing_repository(repository):
    result = run("gh", "api", f"repos/{repository}", check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "HTTP 404" in result.stderr:
        return None
    raise RuntimeError("Repository lookup failed; no create is attempted.\n" + result.stderr.strip())


def require_safe_existing(remote, repository):
    if remote.get("full_name", "").lower() != repository.lower():
        raise RuntimeError("GitHub returned a different repository.")
    if remote.get("private") is not False or remote.get("visibility") not in (None, "public"):
        raise RuntimeError("The repository exists and is not public. Its visibility will not be changed.")
    if remote.get("archived") or remote.get("disabled"):
        raise RuntimeError("The existing repository is archived or disabled.")
    if (remote.get("permissions") or {}).get("push") is not True:
        raise RuntimeError("The selected GitHub identity does not have push access.")


def require_single_snapshot():
    if git("rev-list", "--count", "HEAD") != "1":
        raise RuntimeError("Initial publication requires one fresh snapshot commit. Existing history may contain private material; it will not be published. Extract the reviewed source archive into a fresh directory.")


def wait_for_ci(repository, head):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        runs = gh_json("run", "list", "--repo", repository, "--commit", head,
                       "--limit", "20", "--json", "databaseId,status,conclusion,headSha,workflowName")
        exact = [r for r in runs if r.get("headSha") == head
                 and r.get("workflowName") == "Validate deployment source"]
        if exact:
            chosen = exact[0]
            if chosen.get("status") == "completed":
                if chosen.get("conclusion") != "success":
                    raise RuntimeError("Remote source validation did not pass. No release tag will be pushed.")
            else:
                run("gh", "run", "watch", str(chosen["databaseId"]), "--repo", repository, "--exit-status")
            return chosen["databaseId"]
        time.sleep(5)
    raise RuntimeError("No CI run was observed for the exact pushed commit. Inspect GitHub Actions, then rerun this command.")


def verify_public_template(url, expected):
    last = None
    for _ in range(6):
        try:
            with urlopen(url, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError("Public template URL did not return HTTP 200.")
                actual = response.read(1024 * 1024 + 1)
            if actual != expected:
                raise RuntimeError("Public template bytes do not match the reviewed local template.")
            payload = json.loads(actual)
            if not isinstance(payload.get("resources"), list) or not payload["resources"]:
                raise RuntimeError("Public template has no resources.")
            return hashlib.sha256(actual).hexdigest()
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"Public deployment-button target did not verify: {last}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true", required=True,
                        help="Publish these explicitly allowlisted deployment files publicly")
    parser.add_argument("--repository", help="owner/name, default from deployment-config.json")
    args = parser.parse_args()
    for tool in ("gh", "git"):
        if not shutil.which(tool):
            raise RuntimeError(f"Install {tool} before publishing. Authenticate gh normally; do not paste credentials into these files.")
    run("gh", "auth", "status", "--hostname", "github.com")
    identity = gh_json("api", "user").get("login")
    data = config()
    if args.repository and args.repository != data["repository"]:
        data["repository"] = args.repository
        # Validate with the same strict repository grammar before writing.
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*", args.repository):
            raise RuntimeError("Invalid repository owner/name.")
        (ROOT / "deployment-config.json").write_text(json.dumps(data, indent=2) + "\n")
        check_or_write()
    repository = data["repository"]
    version = data["deploymentVersion"]
    print(f"GitHub identity: {identity}; target: {repository}; visibility: PUBLIC", flush=True)
    run(sys.executable, "scripts/validate.py")
    package()  # Validate every allowlisted file and path before any public mutation.
    agent_file = ROOT / "AGENTS.md"
    if agent_file.exists() and (agent_file.is_symlink() or not agent_file.is_file()):
        raise RuntimeError("AGENTS.md must be a regular reviewed source file.")
    remote = existing_repository(repository)
    if remote:
        require_safe_existing(remote, repository)
    if not (ROOT / ".git").exists():
        run("git", "init", "-b", "main", str(ROOT))
    if Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT:
        raise RuntimeError("The local Git repository root does not match the deployment source directory.")
    if git("symbolic-ref", "--short", "HEAD") != "main":
        raise RuntimeError("Publish from main; this helper will not switch or rename existing branches.")
    allowed = set(FILES) | {"AGENTS.md"}
    tracked = set(git("ls-files").splitlines())
    if tracked - allowed:
        raise RuntimeError("Unexpected tracked paths are present; review them before publishing: " + ", ".join(sorted(tracked - allowed)))
    run("git", "add", "--", *FILES, *( ["AGENTS.md"] if (ROOT / "AGENTS.md").exists() else []))
    changed = run("git", "diff", "--cached", "--quiet", check=False).returncode
    has_head = run("git", "rev-parse", "--verify", "HEAD", check=False).returncode == 0
    if changed or not has_head:
        run("git", "-c", "user.name=Sadie Weaving (AI)", "-c", "user.email=sadie@thox.ai",
            "commit", "-m", "Add ThoxOS Azure deployment resources and verified release launcher")
    head = git("rev-parse", "HEAD")
    require_single_snapshot()
    if remote:
        branches = gh_json("api", f"repos/{repository}/branches")
        if branches and not (len(branches) == 1 and branches[0].get("name") == "main"
                             and branches[0].get("commit", {}).get("sha") == head):
            raise RuntimeError("Existing repository is not empty or at this exact prepared commit. No push was performed.")
    else:
        run("gh", "repo", "create", repository, "--public", "--description", DESCRIPTION)
        remote = gh_json("api", f"repos/{repository}")
        require_safe_existing(remote, repository)
    origin = f"https://github.com/{repository}.git"
    existing_origin = run("git", "remote", "get-url", "origin", check=False)
    if existing_origin.returncode == 0 and existing_origin.stdout.strip() != origin:
        raise RuntimeError("Existing origin is different; no remote is overwritten.")
    if existing_origin.returncode == 0 and git("remote", "get-url", "--push", "origin") != origin:
        raise RuntimeError("Existing origin push URL is different; no push is attempted.")
    if existing_origin.returncode:
        run("git", "remote", "add", "origin", origin)
    git_push("--set-upstream", "origin", "main")
    remote_head = gh_json("api", f"repos/{repository}/commits/main").get("sha")
    if remote_head != head:
        raise RuntimeError("Remote commit readback differs from the local source.")
    print("Source pushed and read back. Waiting for validation of that exact commit.", flush=True)
    ci = wait_for_ci(repository, head)
    tag = run("git", "rev-parse", "--verify", f"refs/tags/{version}^{{commit}}", check=False)
    if tag.returncode == 0 and tag.stdout.strip() != head:
        raise RuntimeError("An existing version tag points elsewhere. Issued tags are never rewritten.")
    if tag.returncode:
        run("git", "tag", version, head)
    git_push("origin", f"refs/tags/{version}")
    template_hash = verify_public_template(raw_template(data), (ROOT / "azuredeploy.json").read_bytes())
    archive, checksums = package()
    existing_release = run("gh", "release", "view", version, "--repo", repository,
                           "--json", "url,tagName,isDraft,isPrerelease", check=False)
    if existing_release.returncode:
        run("gh", "release", "create", version, str(archive), str(checksums), "--repo", repository,
            "--verify-tag", "--prerelease", "--title", f"ThoxOS Azure Deployment {version}",
            "--notes-file", str(ROOT / "docs/RELEASE_NOTES.md"))
    release = gh_json("release", "view", version, "--repo", repository,
                      "--json", "url,tagName,isDraft,isPrerelease,assets")
    if release.get("tagName") != version or release.get("isDraft") is not False or release.get("isPrerelease") is not True:
        raise RuntimeError("Release readback is not a published engineering prerelease at the intended tag.")
    required_assets = {archive.name, checksums.name}
    if not required_assets.issubset({a.get("name") for a in release.get("assets", [])}):
        raise RuntimeError("Published release assets are incomplete; inspect the existing release without overwriting it.")
    with tempfile.TemporaryDirectory(prefix="thox-release-readback-") as temp:
        run("gh", "release", "download", version, "--repo", repository,
            "--dir", temp, "--pattern", archive.name, "--pattern", checksums.name)
        for path in (archive, checksums):
            if (Path(temp) / path.name).read_bytes() != path.read_bytes():
                raise RuntimeError("Published release asset bytes differ: " + path.name)
    receipt = {"repository": f"https://github.com/{repository}", "commit": head,
               "release": release, "ci_run_id": ci, "raw_template": raw_template(data),
               "template_sha256": template_hash, "deploy_button": portal_url(data),
               "azure_deployment": "not_run", "real_kernel_boot": "not_run"}
    directory = ROOT / ".publication-receipts"
    directory.mkdir(exist_ok=True)
    (directory / f"{head}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        sys.exit(1)
