"""Offline checks for release identity, non-overwrite and token/event boundaries."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_from_actions as release
sys.path.pop(0)
HEAD = "a" * 40
OTHER = "b" * 40
VERSION = "v0.1.0"


def context():
    env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
           "GITHUB_REPOSITORY": release.REPOSITORY, "GITHUB_SHA": HEAD, "GH_TOKEN": "offline-fixture"}
    event = {"ref": "refs/heads/main", "after": HEAD, "deleted": False,
             "repository": {"full_name": release.REPOSITORY}}
    config = {"repository": release.REPOSITORY, "deploymentVersion": VERSION}
    return env, event, config


def remote_release(**changes):
    value = {"tag_name": VERSION, "name": "Reviewed title", "body": "Reviewed body",
             "prerelease": True, "draft": True, "assets": []}
    value.update(changes)
    return value


class ActionsContextTests(unittest.TestCase):
    def test_exact_main_push_accepted(self):
        env, event, config = context()
        release.check_actions_context(env, event, HEAD, config)

    def test_pr_fork_wrong_ref_missing_token_or_mismatched_sha_refused(self):
        for key, bad in (("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "pull_request"),
                         ("GITHUB_REF", "refs/tags/v0.1.0"), ("GITHUB_REPOSITORY", "foreign/fork"),
                         ("GITHUB_SHA", OTHER), ("GH_TOKEN", "")):
            env, event, config = context()
            env[key] = bad
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                release.check_actions_context(env, event, HEAD, config)

    def test_event_payload_and_config_must_match(self):
        for key, bad in (("ref", "refs/heads/feature"), ("after", OTHER), ("deleted", True),
                         ("repository", {"full_name": "foreign/fork"})):
            env, event, config = context()
            event[key] = bad
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                release.check_actions_context(env, event, HEAD, config)
        for bad in ("main", "latest", "v1.2.3/extra"):
            env, event, config = context()
            config["deploymentVersion"] = bad
            with self.assertRaises(RuntimeError):
                release.check_actions_context(env, event, HEAD, config)


class TagAndReleaseTests(unittest.TestCase):
    def test_lightweight_and_annotated_tag_exact_commit(self):
        self.assertEqual(release.require_tag_commit({"object": {"type": "commit", "sha": HEAD}}, HEAD, None), HEAD)
        self.assertEqual(release.require_tag_commit({"object": {"type": "tag", "sha": OTHER}}, HEAD,
                         lambda sha: {"object": {"type": "commit", "sha": HEAD}}), HEAD)

    def test_foreign_commit_or_tag_cycle_cannot_be_rewritten(self):
        for ref, resolver in (({"object": {"type": "commit", "sha": OTHER}}, None),
                              ({"object": {"type": "tag", "sha": OTHER}},
                               lambda sha: {"object": {"type": "tag", "sha": OTHER}})):
            with self.assertRaises(RuntimeError):
                release.require_tag_commit(ref, HEAD, resolver)

    def test_matching_draft_allows_only_missing_assets(self):
        names = {"source.zip", "source.sha256"}
        draft = remote_release(assets=[{"name": "source.zip"}])
        self.assertEqual(release.assess_release(draft, VERSION, "Reviewed title", "Reviewed body", names), {"source.sha256"})

    def test_metadata_conflict_published_missing_and_unknown_assets_fail_closed(self):
        names = {"source.zip", "source.sha256"}
        cases = [remote_release(name="Other title"), remote_release(body="Unreviewed notes"),
                 remote_release(prerelease=False), remote_release(draft="false"),
                 remote_release(draft=False), remote_release(assets=[{"name": "unknown"}]),
                 remote_release(assets=[{"name": "source.zip"}, {"name": "source.zip"}])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                release.assess_release(case, VERSION, "Reviewed title", "Reviewed body", names)

    def test_existing_complete_published_release_is_idempotent(self):
        names = {"source.zip", "source.sha256"}
        published = remote_release(draft=False, assets=[{"name": name} for name in names])
        self.assertEqual(release.assess_release(published, VERSION, "Reviewed title", "Reviewed body", names), set())

    def test_only_404_is_missing(self):
        for code in (401, 403, 429, 500):
            with patch.object(release, "run", return_value=subprocess.CompletedProcess([], 1, "", f"HTTP {code}")), self.assertRaises(RuntimeError):
                release.api("repos/example/repo/git/ref/tags/v0.1.0", missing_ok=True)
        with patch.object(release, "run", return_value=subprocess.CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)")):
            self.assertIsNone(release.api("repos/example/repo/git/ref/tags/v0.1.0", missing_ok=True))

    def test_remote_main_move_blocks_mutation(self):
        with patch.object(release, "api", return_value={"object": {"type": "commit", "sha": OTHER}}), self.assertRaises(RuntimeError):
            release.check_remote_main(HEAD)


class AssetReadbackTests(unittest.TestCase):
    def test_metadata_and_actual_downloaded_bytes_both_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.zip"
            path.write_bytes(b"reviewed-public-payload")
            asset = {"name": path.name, "state": "uploaded", "size": path.stat().st_size,
                     "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}

            def download(*args, **unused):
                destination = Path(args[args.index("--dir") + 1]) / path.name
                destination.write_bytes(path.read_bytes())
                return subprocess.CompletedProcess(args, 0, "", "")

            with patch.object(release, "run", side_effect=download):
                release.verify_asset_bytes(VERSION, {"assets": [asset]}, (path,))
            for key, bad in (("size", 0), ("digest", "sha256:" + "0" * 64), ("state", "starter")):
                wrong = {**asset, key: bad}
                with patch.object(release, "run") as command, self.assertRaises(RuntimeError):
                    release.verify_asset_bytes(VERSION, {"assets": [wrong]}, (path,))
                command.assert_not_called()

            def changed_download(*args, **unused):
                destination = Path(args[args.index("--dir") + 1]) / path.name
                destination.write_bytes(b"different bytes")
                return subprocess.CompletedProcess(args, 0, "", "")

            with patch.object(release, "run", side_effect=changed_download), self.assertRaises(RuntimeError):
                release.verify_asset_bytes(VERSION, {"assets": [asset]}, (path,))


class WorkflowBoundaryTests(unittest.TestCase):
    def test_release_is_main_only_downstream_and_actions_are_sha_pinned(self):
        text = (ROOT / ".github/workflows/validate.yml").read_text()
        import re
        self.assertIn("needs: validate", text)
        self.assertIn("github.event_name == 'push'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("github.repository == 'Thox-ai/thoxos-azure'", text)
        self.assertEqual(text.count("contents: write"), 1)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("cancel-in-progress: false", text)
        for action in re.findall(r"uses: (\S+)", text):
            self.assertRegex(action, r"^[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("workflow_run:", text)


if __name__ == "__main__":
    unittest.main()
