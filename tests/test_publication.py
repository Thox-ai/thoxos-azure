"""Offline publication guards. GitHub and HTTP calls are always replaced by fixtures."""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import quote, unquote, urlsplit
import zipfile


SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "scripts"))
import links
import package_release
import publish
sys.path.pop(0)


CONFIG = {
    "repository": "Thox-ai/thoxos-azure",
    "deploymentVersion": "v0.1.0",
    "kernelRelease": "v0.9.1",
    "kernelRepository": "Thox-ai/thoxinux",
    "kernelSourceCommit": "84b4a7e740496297ca79dff735834ca2a8f2bad3",
}


class HttpResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, limit):
        return self.body[:limit]


def public_repo(**updates):
    result = {"full_name": CONFIG["repository"], "private": False,
              "visibility": "public", "archived": False, "disabled": False,
              "permissions": {"push": True}}
    result.update(updates)
    return result


def write_public_fixture(root):
    for name in package_release.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Public fixture for " + name + "\n")
    (root / "deployment-config.json").write_text(json.dumps(CONFIG))


class DeploymentLinkTests(unittest.TestCase):
    def test_button_targets_exact_public_versioned_raw_template(self):
        expected = "https://raw.githubusercontent.com/Thox-ai/thoxos-azure/v0.1.0/azuredeploy.json"
        self.assertEqual(links.raw_template(CONFIG), expected)
        url = links.portal_url(CONFIG)
        parsed = urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc), ("https", "portal.azure.com"))
        self.assertTrue(parsed.fragment.startswith("create/Microsoft.Template/uri/"))
        encoded = parsed.fragment.split("/uri/", 1)[1]
        self.assertEqual(encoded, quote(expected, safe=""))
        self.assertEqual(unquote(encoded), expected)
        self.assertNotIn("/main/", expected)
        self.assertIn(url, links.button(CONFIG))

    def test_invalid_repository_paths_are_rejected(self):
        invalid = ("https://github.com/Thox-ai/thoxos-azure", "Thox-ai/../outside",
                   "Thox-ai/repo?x=y", "/repository", "owner/repo/extra", "owner/repo\n")
        with tempfile.TemporaryDirectory() as temp, patch.object(links, "ROOT", Path(temp)):
            for value in invalid:
                with self.subTest(repository=value):
                    data = {**CONFIG, "repository": value}
                    (Path(temp) / "deployment-config.json").write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        links.config()

    def test_unversioned_or_pathlike_deployment_refs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(links, "ROOT", Path(temp)):
            for value in ("main", "latest", "v0.1", "v0.1.0/extra", "v0.1.0\n"):
                with self.subTest(version=value):
                    (Path(temp) / "deployment-config.json").write_text(json.dumps({**CONFIG, "deploymentVersion": value}))
                    with self.assertRaises(ValueError):
                        links.config()

    def test_button_drift_is_detected_and_exact_block_is_repaired(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(links, "ROOT", Path(temp)):
            root = Path(temp)
            (root / "deployment-config.json").write_text(json.dumps(CONFIG))
            before = "# Public README\n\n" + links.START + "\nold target\n" + links.END + "\n\nKeep this text.\n"
            (root / "README.md").write_text(before)
            with self.assertRaisesRegex(ValueError, "drift"):
                links.check_or_write(check=True)
            self.assertEqual((root / "README.md").read_text(), before)
            links.check_or_write()
            self.assertEqual(links.check_or_write(check=True), links.portal_url(CONFIG))
            updated = (root / "README.md").read_text()
            self.assertTrue(updated.startswith("# Public README\n\n"))
            self.assertTrue(updated.endswith("\n\nKeep this text.\n"))

    def test_missing_or_duplicate_button_blocks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(links, "ROOT", Path(temp)):
            root = Path(temp)
            (root / "deployment-config.json").write_text(json.dumps(CONFIG))
            for text in ("# No deployment block\n", links.button(CONFIG) + "\n" + links.button(CONFIG)):
                (root / "README.md").write_text(text)
                with self.subTest(text=text[:30]), self.assertRaisesRegex(ValueError, "exactly one"):
                    links.check_or_write()


class ExistingRepositoryTests(unittest.TestCase):
    def test_matching_public_repository_with_push_access_is_accepted(self):
        publish.require_safe_existing(public_repo(), CONFIG["repository"])

    def test_private_foreign_archived_disabled_or_readonly_repository_is_refused(self):
        cases = (public_repo(private=True, visibility="private"),
                 public_repo(full_name="different-owner/valuable-repository"),
                 public_repo(archived=True), public_repo(disabled=True),
                 public_repo(permissions={"push": False}), public_repo(permissions={}),
                 public_repo(permissions=None))
        for remote in cases:
            with self.subTest(remote=remote), self.assertRaises(RuntimeError):
                publish.require_safe_existing(remote, CONFIG["repository"])

    def test_only_explicit_not_found_is_treated_as_missing(self):
        result = subprocess.CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)")
        with patch.object(publish, "run", return_value=result) as run:
            self.assertIsNone(publish.existing_repository(CONFIG["repository"]))
        run.assert_called_once_with("gh", "api", "repos/" + CONFIG["repository"], check=False)

    def test_lookup_authorization_and_network_failures_do_not_start_creation(self):
        for error in ("gh: Forbidden (HTTP 403)", "gh: Bad credentials (HTTP 401)",
                      "dial tcp: temporary DNS failure", "gh: API rate limit exceeded (HTTP 429)"):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                root = Path(temp)
                stack.enter_context(patch.object(publish, "ROOT", root))
                stack.enter_context(patch.object(publish.shutil, "which", return_value="/mock/bin/tool"))
                stack.enter_context(patch.object(publish, "config", return_value=copy.deepcopy(CONFIG)))
                stack.enter_context(patch.object(publish, "package", return_value=(root / "fixture.zip", root / "fixture.sha256")))
                stack.enter_context(patch.object(publish, "gh_json", return_value={"login": "offline-test-operator"}))
                stack.enter_context(patch.object(sys, "argv", ["publish.py", "--public"]))

                def mock_run(*args, **kwargs):
                    if args[:3] == ("gh", "api", "repos/" + CONFIG["repository"]):
                        return subprocess.CompletedProcess(args, 1, "", error)
                    return subprocess.CompletedProcess(args, 0, "", "")

                run = stack.enter_context(patch.object(publish, "run", side_effect=mock_run))
                with self.assertRaisesRegex(RuntimeError, "no create is attempted"):
                    publish.main()
                calls = [call.args for call in run.call_args_list]
                self.assertFalse(any(call[:3] == ("gh", "repo", "create") for call in calls))
                self.assertFalse(any(call and call[0] == "git" for call in calls))
                self.assertFalse((root / ".git").exists())

    def test_only_one_fresh_snapshot_commit_can_be_published(self):
        with patch.object(publish, "git", return_value="1") as git:
            publish.require_single_snapshot()
        git.assert_called_once_with("rev-list", "--count", "HEAD")
        for count in ("0", "2", "17", ""):
            with self.subTest(count=count), patch.object(publish, "git", return_value=count), \
                    self.assertRaisesRegex(RuntimeError, "fresh snapshot"):
                publish.require_single_snapshot()


class PublicTemplateVerificationTests(unittest.TestCase):
    def test_http_200_exact_json_bytes_return_expected_digest(self):
        body = b'{"resources":[{"type":"Microsoft.Compute/virtualMachines"}]}\n'
        with patch.object(publish, "urlopen", return_value=HttpResponse(body)) as fetch:
            digest = publish.verify_public_template(links.raw_template(CONFIG), body)
        self.assertEqual(digest, hashlib.sha256(body).hexdigest())
        fetch.assert_called_once_with(links.raw_template(CONFIG), timeout=30)

    def test_changed_template_bytes_fail_even_when_json_is_valid(self):
        expected = b'{"resources":[{"name":"reviewed"}]}'
        changed = b'{"resources":[{"name":"changed"}]}'
        with patch.object(publish, "urlopen", return_value=HttpResponse(changed)), \
                patch.object(publish.time, "sleep"), self.assertRaisesRegex(RuntimeError, "bytes do not match"):
            publish.verify_public_template(links.raw_template(CONFIG), expected)

    def test_non_success_http_status_is_never_accepted(self):
        body = b'{"resources":[{"name":"reviewed"}]}'
        for status in (204, 301, 401, 403, 404, 500):
            with self.subTest(status=status), patch.object(publish, "urlopen", return_value=HttpResponse(body, status)), \
                    patch.object(publish.time, "sleep"), self.assertRaisesRegex(RuntimeError, "HTTP 200"):
                publish.verify_public_template(links.raw_template(CONFIG), body)

    def test_non_template_or_empty_resources_cannot_pass_byte_verification(self):
        for body in (b"<html>Sign in</html>", b"[]", b"{}", b'{"resources":[]}'):
            with self.subTest(body=body), patch.object(publish, "urlopen", return_value=HttpResponse(body)), \
                    patch.object(publish.time, "sleep"), self.assertRaises(RuntimeError):
                publish.verify_public_template(links.raw_template(CONFIG), body)

    def test_http_network_failure_is_reported_without_retry_sleep(self):
        with patch.object(publish, "urlopen", side_effect=URLError("offline fixture")), \
                patch.object(publish.time, "sleep"), self.assertRaisesRegex(RuntimeError, "did not verify"):
            publish.verify_public_template(links.raw_template(CONFIG), b'{"resources":[{}]}')


class PublicArchiveTests(unittest.TestCase):
    def fixture(self, stack, temp):
        root = Path(temp) / "source"
        root.mkdir()
        write_public_fixture(root)
        stack.enter_context(patch.object(package_release, "ROOT", root))
        stack.enter_context(patch.object(links, "ROOT", root))
        return root

    def test_archive_is_deterministic_and_excludes_unlisted_private_material(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = self.fixture(stack, temp)
            forbidden = ("AGENTS.md", ".agents/HANDOFF.md", ".env", "id_ed25519", "docs/secret.pem",
                         ".publication-receipts/internal.json")
            for name in forbidden:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("PRIVATE_FIXTURE_MUST_NOT_BE_PACKAGED")
            archive, checksums = package_release.package()
            before = archive.read_bytes()
            before_checksums = checksums.read_bytes()
            for name in package_release.FILES:
                os.utime(root / name, (1800000000, 1800000000))
            (root / ".env").write_text("CHANGED_PRIVATE_FIXTURE")
            second, second_checksums = package_release.package()
            self.assertEqual(second.read_bytes(), before)
            self.assertEqual(second_checksums.read_bytes(), before_checksums)
            self.assertEqual(checksums.read_bytes(),
                             (hashlib.sha256(before).hexdigest() + "  " + archive.name + "\n").encode("ascii"))
            prefix = "thoxos-azure-v0.1.0/"
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                members = {name.removeprefix(prefix) for name in z.namelist()}
                self.assertEqual(members, set(package_release.FILES) | {"SHA256SUMS"})
                for entry in z.infolist():
                    self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(entry.create_system, 3)
                    self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                for name in forbidden:
                    self.assertNotIn(name, members)
                for name in z.namelist():
                    self.assertNotIn(b"PRIVATE_FIXTURE", z.read(name))
                for line in z.read(prefix + "SHA256SUMS").decode().splitlines():
                    expected, name = line.split("  ", 1)
                    self.assertEqual(expected, hashlib.sha256(z.read(prefix + name)).hexdigest())

    def test_missing_allowlisted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = self.fixture(stack, temp)
            (root / "README.md").unlink()
            with self.assertRaisesRegex(RuntimeError, "Missing regular public source"):
                package_release.package()

    def test_allowlisted_leaf_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = self.fixture(stack, temp)
            external = Path(temp) / "private-file"
            external.write_text("SECRET_FIXTURE")
            (root / "README.md").unlink()
            (root / "README.md").symlink_to(external)
            with self.assertRaises(RuntimeError):
                package_release.package()

    def test_symlinked_parent_directory_cannot_publish_outside_source(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = self.fixture(stack, temp)
            external = Path(temp) / "external-documents"
            (root / "docs").rename(external)
            (root / "docs").symlink_to(external, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                package_release.package()

    def test_invalid_release_versions_are_rejected_before_archive_creation(self):
        for version in ("main", "v0.1", "v0.1.0/extra", "v0.1.0\n"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                root = self.fixture(stack, temp)
                (root / "deployment-config.json").write_text(json.dumps({**CONFIG, "deploymentVersion": version}))
                with self.assertRaises((ValueError, RuntimeError)):
                    package_release.package()

    def test_output_paths_cannot_follow_symlinks_outside_dist(self):
        for kind in ("directory", "archive", "checksums"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                root = self.fixture(stack, temp)
                external = Path(temp) / "external-output"
                if kind == "directory":
                    external.mkdir()
                    (root / "dist").symlink_to(external, target_is_directory=True)
                else:
                    external.write_bytes(b"PRESERVE_THIS_FIXTURE")
                    (root / "dist").mkdir()
                    extension = ".zip" if kind == "archive" else ".sha256"
                    (root / "dist" / ("thoxos-azure-v0.1.0" + extension)).symlink_to(external)
                with self.assertRaises(RuntimeError):
                    package_release.package()
                if kind == "directory":
                    self.assertEqual(list(external.iterdir()), [])
                else:
                    self.assertEqual(external.read_bytes(), b"PRESERVE_THIS_FIXTURE")


if __name__ == "__main__":
    unittest.main()
