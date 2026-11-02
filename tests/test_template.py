"""Deployment guard checks. These tests do not contact or impersonate Azure."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generate_template", ROOT / "scripts/generate_template.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = (ROOT / "guest-bootstrap.sh").read_bytes()
        cls.template = generator.make_template(cls.bootstrap)
        cls.resources = {r["type"]: r for r in cls.template["resources"]}

    def test_checked_in_template_exactly_binds_bootstrap(self):
        self.assertEqual((ROOT / "azuredeploy.json").read_bytes(), generator.render(self.bootstrap))
        decoded = base64.b64decode(self.template["variables"]["bootstrapBase64"], validate=True)
        self.assertEqual(decoded, self.bootstrap)
        self.assertEqual(self.template["metadata"]["bootstrapSha256"], hashlib.sha256(decoded).hexdigest())
        self.assertLessEqual(len(self.template["variables"]["bootstrapBase64"]), 65535)

    def test_payload_is_literal_not_oversized_arm_expression(self):
        vm = self.resources["Microsoft.Compute/virtualMachines"]
        self.assertEqual(vm["properties"]["osProfile"]["customData"], "[variables('bootstrapBase64')]")
        self.assertFalse(self.template["variables"]["bootstrapBase64"].startswith("["))

    def test_console_log_operations_drop_root_authority(self):
        text = self.bootstrap.decode("utf-8")
        helper = text.split("cat >/usr/local/sbin/thoxinux-console <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.assertLess(helper.index("if os.geteuid()!=0:"), helper.index("os.setgroups([])"))
        self.assertLess(helper.index("os.setgid(account.pw_gid)"), helper.index("os.setuid(account.pw_uid)"))
        self.assertLess(helper.index("os.setuid(account.pw_uid)"), helper.index("def send(command):"))
        self.assertIn("if account.pw_uid==0 or account.pw_gid==0:", helper)
        self.assertIn("with temp.open('x') as f:", helper)
        self.assertNotIn("temp.write_text(payload)", helper)

    def test_invalid_bootstraps_refused(self):
        for value in (b"", b"https://example.invalid/bootstrap", b"#!/usr/bin/env bash\n\x00", b"#!/usr/bin/env bash\n" + b"x" * 50000):
            with self.subTest(bytes=len(value)), self.assertRaises(ValueError):
                generator.make_template(value)

    def test_no_inbound_rule_or_forwarding(self):
        nsg = self.resources["Microsoft.Network/networkSecurityGroups"]["properties"]
        rules = nsg["securityRules"]
        self.assertEqual(len(rules), 1)
        rule = rules[0]["properties"]
        self.assertEqual((rule["direction"], rule["access"], rule["protocol"]), ("Inbound", "Deny", "*"))
        for name in ("sourcePortRange", "destinationPortRange", "sourceAddressPrefix", "destinationAddressPrefix"):
            self.assertEqual(rule[name], "*")
        nic = self.resources["Microsoft.Network/networkInterfaces"]["properties"]
        self.assertIs(nic["enableIPForwarding"], False)
        self.assertIn("networkSecurityGroup", nic)
        subnet = self.resources["Microsoft.Network/virtualNetworks"]["properties"]["subnets"][0]
        self.assertIs(subnet["properties"]["defaultOutboundAccess"], False)
        self.assertIn("publicIPAddress", nic["ipConfigurations"][0]["properties"])

    def test_passwords_disabled_and_storage_preserved(self):
        p = self.resources["Microsoft.Compute/virtualMachines"]["properties"]
        self.assertNotIn("adminPassword", p["osProfile"])
        self.assertIs(p["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"], True)
        self.assertEqual(p["storageProfile"]["osDisk"]["deleteOption"], "Detach")
        self.assertEqual(p["storageProfile"]["osDisk"]["diskSizeGB"], 128)
        self.assertEqual(p["securityProfile"]["securityType"], "TrustedLaunch")

    def test_acceptance_is_mandatory_and_synchronous(self):
        rc = self.resources["Microsoft.Compute/virtualMachines/runCommands"]
        self.assertNotIn("condition", rc)
        self.assertIs(rc["properties"]["asyncExecution"], False)
        self.assertIs(rc["properties"]["treatFailureAsDeploymentFailure"], True)
        self.assertEqual(rc["properties"]["timeoutInSeconds"], 1800)
        self.assertIn("timeout 1500 cloud-init status --wait --long", rc["properties"]["source"]["script"])
        script = rc["properties"]["source"]["script"]
        self.assertIn("mktemp /var/lib/thoxinux-azure/readiness.", script)
        self.assertIn("install -d -o root -g root -m 0700 /var/lib/thoxinux-azure", script)
        self.assertNotIn(">/var/log/thoxinux", script)
        self.assertIn('python3 - "$status_exit" "$receipt"', script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True, capture_output=True)

    def test_guest_version_fixed_host_version_explicitly_selectable(self):
        self.assertEqual(self.template["metadata"]["release"], "v0.9.1")
        self.assertEqual(self.template["parameters"]["imageVersion"]["defaultValue"], "latest")
        self.assertNotIn("bootstrapBase64", self.template["parameters"])
        self.assertNotIn("kernelVersion", self.template["parameters"])
        self.assertIn("deployment().name", self.template["parameters"]["vmName"]["defaultValue"])
        self.assertNotIn("Microsoft.Resources/deployments", self.resources)


class ReceiptAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = {"__name__": "test_receipt_validator"}
        exec(compile(generator.acceptance_python(), "managed-run-command", "exec"), namespace)
        cls.reject = staticmethod(namespace["rejection_reasons"])

    def receipt(self):
        return {"marker": "THOXOS_RELEASE_STATUS_V1", "ok": True,
                "release": "v0.9.1", "source_commit": generator.SOURCE_COMMIT,
                "efi_sha256": generator.EFI_SHA256, "checksums_verified": True,
                "service_state": "active", "qmp_status": "running", "boot_id": "a-real-current-boot-id",
                "serial_markers": {"unified_identity": True, "controller": True, "prompt": True, "panic": False},
                "smoke": {"ok": True, "current_boot": True, "commands": ["status", "components", "domain list", "help"]},
                "errors": []}

    def test_complete_receipt_passes(self):
        self.assertEqual(self.reject(self.receipt(), 0), [])

    def test_self_reported_ok_does_not_override_wrong_identity(self):
        for key in ("efi_sha256", "source_commit", "release", "marker", "service_state", "qmp_status"):
            value = self.receipt()
            value[key] = "wrong"
            with self.subTest(key=key):
                self.assertTrue(self.reject(value, 0))

    def test_truthy_strings_and_integers_are_rejected(self):
        for bad in ("true", "false", 1, None):
            for key in ("ok", "checksums_verified"):
                value = self.receipt()
                value[key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertTrue(self.reject(value, 0))
            value = self.receipt()
            value["smoke"]["current_boot"] = bad
            self.assertTrue(self.reject(value, 0))

    def test_missing_or_malformed_runtime_evidence_fails_closed(self):
        values = [None, [], "passed", {"ok": True}]
        for value in values:
            self.assertTrue(self.reject(value, 0))
        for key in ("serial_markers", "smoke", "errors", "boot_id"):
            for invalid in (None, [], "", False):
                value = self.receipt()
                value[key] = invalid
                if key == "errors" and invalid == []:
                    continue
                with self.subTest(key=key, invalid=invalid):
                    self.assertTrue(self.reject(value, 0))

    def test_panic_stale_boot_failed_command_or_exit_fails(self):
        value = self.receipt()
        self.assertTrue(self.reject(value, 7))
        for path, bad in (("panic", True), ("prompt", False), ("controller", False)):
            value = self.receipt()
            value["serial_markers"][path] = bad
            self.assertTrue(self.reject(value, 0))
        value = self.receipt()
        value["smoke"]["current_boot"] = False
        self.assertTrue(self.reject(value, 0))
        value = self.receipt()
        value["smoke"]["commands"] = ["status"]
        self.assertTrue(self.reject(value, 0))


if __name__ == "__main__":
    unittest.main()
