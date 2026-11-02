"""Offline safety and acceptance tests. No Azure calls or resource mutations."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "deploy.py"
SPEC = importlib.util.spec_from_file_location("thox_azure_deploy", MODULE)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def options(**updates):
    value = deploy.parser().parse_args([
        "prepare", "--subscription", "11111111-2222-4333-8444-555555555555",
        "--image-version", "24.04.202610010",
    ])
    for name, item in updates.items():
        setattr(value, name, item)
    return value


def valid_key():
    kind = b"ssh-ed25519"
    wire = len(kind).to_bytes(4, "big") + kind + (32).to_bytes(4, "big") + bytes(range(32))
    return "ssh-ed25519 " + base64.b64encode(wire).decode() + " test-only"


def valid_guest():
    return {
        "marker": "THOXOS_RELEASE_STATUS_V1", "ok": True,
        "release": deploy.RELEASE, "source_commit": deploy.SOURCE_COMMIT,
        "efi_sha256": deploy.EFI_SHA256, "checksums_verified": True,
        "service_state": "active", "qmp_status": "running", "errors": [],
        "serial_markers": {"unified_identity": True, "controller": True,
                           "prompt": True, "panic": False},
        "smoke": {"ok": True, "current_boot": True,
                  "commands": ["status", "components", "domain list", "help"]},
    }


class AzureTargetTests(unittest.TestCase):
    def test_zero_subscription_cannot_be_deployment_target(self):
        with self.assertRaisesRegex(RuntimeError, "zero subscription"):
            deploy.validate_inputs(options(subscription="00000000-0000-0000-0000-000000000000"))

    def test_cli_targets_explicit_subscription_without_shell(self):
        args = options()
        result = subprocess.CompletedProcess([], 0, '{"name":"chosen"}', "")
        with patch.object(deploy.shutil, "which", return_value="/usr/bin/az"), \
                patch.object(deploy.subprocess, "run", return_value=result) as run:
            self.assertEqual(deploy.azure(args, "account", "show"), {"name": "chosen"})
        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("--subscription") + 1], args.subscription)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_cli_error_and_invalid_json_never_become_success(self):
        for result in (subprocess.CompletedProcess([], 1, "{}", "denied"),
                       subprocess.CompletedProcess([], 0, "not JSON", "")):
            with self.subTest(returncode=result.returncode), \
                    patch.object(deploy.shutil, "which", return_value="/usr/bin/az"), \
                    patch.object(deploy.subprocess, "run", return_value=result), \
                    self.assertRaises(RuntimeError):
                deploy.azure(options(), "account", "show")

    def test_mismatched_authenticated_subscription_rejected(self):
        with patch.object(deploy, "azure", return_value={"id": "another-subscription", "state": "Enabled"}), \
                self.assertRaisesRegex(RuntimeError, "did not match"):
            deploy.authenticated_account(options())

    def test_group_without_tags_is_rejected_with_actionable_error(self):
        args = options()
        with patch.object(deploy, "azure", side_effect=[True, {"location": args.location, "tags": None}]) as az, \
                self.assertRaisesRegex(RuntimeError, "not tagged"):
            deploy.ensure_empty_group(args)
        self.assertFalse(any("create" in call.args for call in az.call_args_list))

    def test_nonempty_owned_group_is_never_overwritten(self):
        args = options()
        with patch.object(deploy, "azure", side_effect=[True, {"location": args.location, "tags": deploy.tags(args)},
                                                       [{"name": "existing-valuable-resource"}]]) as az, \
                self.assertRaisesRegex(RuntimeError, "not empty"):
            deploy.ensure_empty_group(args)
        self.assertFalse(any("create" in call.args for call in az.call_args_list))


class TemplateSafetyTests(unittest.TestCase):
    def template(self):
        return deploy.make_template(options(), "24.04.202610010", valid_key(), b"#!/bin/bash\ntrue\n")

    def test_network_denies_all_inbound_and_declares_explicit_egress(self):
        resources = {item["type"]: item for item in self.template()["resources"]}
        nsg = resources["Microsoft.Network/networkSecurityGroups"]
        rules = [rule["properties"] for rule in nsg["properties"]["securityRules"]]
        self.assertTrue(any(rule["direction"] == "Inbound" and rule["access"] == "Deny"
                            and rule["priority"] < 65000 and rule["protocol"] == "*"
                            and rule["sourceAddressPrefix"] == rule["destinationAddressPrefix"] == "*"
                            and rule["sourcePortRange"] == rule["destinationPortRange"] == "*"
                            for rule in rules))
        self.assertFalse(any(rule["direction"] == "Inbound" and rule["access"] == "Allow" for rule in rules))
        vnet = resources["Microsoft.Network/virtualNetworks"]
        self.assertFalse(vnet["properties"]["subnets"][0]["properties"]["defaultOutboundAccess"])
        nic = resources["Microsoft.Network/networkInterfaces"]
        self.assertIn("networkSecurityGroup", nic["properties"])
        self.assertIn("publicIPAddress", nic["properties"]["ipConfigurations"][0]["properties"])
        self.assertEqual(resources["Microsoft.Network/publicIPAddresses"]["sku"]["name"], "Standard")

    def test_host_keeps_trusted_boot_key_auth_and_persistent_128gib_disk(self):
        vm = next(item for item in self.template()["resources"] if item["type"] == "Microsoft.Compute/virtualMachines")
        props = vm["properties"]
        self.assertEqual(props["securityProfile"]["securityType"], "TrustedLaunch")
        self.assertTrue(props["securityProfile"]["uefiSettings"]["secureBootEnabled"])
        self.assertTrue(props["securityProfile"]["uefiSettings"]["vTpmEnabled"])
        self.assertTrue(props["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"])
        disk = props["storageProfile"]["osDisk"]
        self.assertEqual(disk["diskSizeGB"], 128)
        self.assertEqual(disk["managedDisk"]["storageAccountType"], "StandardSSD_LRS")
        self.assertEqual(disk["deleteOption"], "Detach")

    def test_unpinned_image_and_oversize_custom_data_rejected(self):
        for version, payload in (("latest", b"x"), ("24.04.202610010", b"x" * 65536)):
            with self.subTest(version=version, size=len(payload)), self.assertRaises(RuntimeError):
                deploy.make_template(options(), version, valid_key(), payload)

    def test_private_key_and_mismatched_public_key_type_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "key.pub"
            for value in ("-----BEGIN OPENSSH PRIVATE KEY-----", valid_key().replace("ssh-ed25519 ", "ssh-rsa ", 1)):
                path.write_text(value)
                with self.subTest(value=value[:30]), self.assertRaises(RuntimeError):
                    deploy.public_key(options(ssh_public_key=str(path)))


class EvidenceTests(unittest.TestCase):
    def test_complete_current_release_evidence_is_accepted(self):
        self.assertEqual(deploy.acceptance_errors(valid_guest()), [])

    def test_success_boolean_without_release_and_boot_evidence_is_rejected(self):
        self.assertTrue(deploy.acceptance_errors({"ok": True}))

    def test_changed_release_identity_or_inactive_guest_is_rejected(self):
        for field in ("marker", "release", "source_commit", "efi_sha256", "service_state", "qmp_status"):
            guest = valid_guest()
            guest[field] = "incorrect-or-stale"
            with self.subTest(field=field):
                self.assertTrue(deploy.acceptance_errors(guest))

    def test_failed_checksum_panic_or_old_boot_cannot_pass(self):
        cases = []
        for field in ("ok", "checksums_verified"):
            guest = valid_guest()
            guest[field] = 1  # JSON true is required; truthy values are insufficient.
            cases.append(guest)
        for field in ("unified_identity", "controller", "prompt"):
            guest = valid_guest()
            guest["serial_markers"][field] = False
            cases.append(guest)
        guest = valid_guest()
        guest["serial_markers"]["panic"] = True
        cases.append(guest)
        guest = valid_guest()
        guest["smoke"]["current_boot"] = False
        cases.append(guest)
        for guest in cases:
            with self.subTest(guest=guest):
                self.assertTrue(deploy.acceptance_errors(guest))

    def test_malformed_nested_evidence_fails_closed_with_errors(self):
        for field in ("serial_markers", "smoke"):
            for malformed in ([True], "true", 1):
                guest = valid_guest()
                guest[field] = malformed
                with self.subTest(field=field, malformed=malformed):
                    self.assertTrue(deploy.acceptance_errors(guest))

    def test_error_receipt_and_incomplete_command_set_cannot_pass(self):
        guest = valid_guest()
        guest["errors"] = ["QMP check failed"]
        self.assertTrue(deploy.acceptance_errors(guest))
        guest = valid_guest()
        guest["smoke"]["commands"] = ["status"] * 4
        self.assertTrue(deploy.acceptance_errors(guest))

    def test_plan_detects_modified_template_or_parameters(self):
        for filename in ("azuredeploy.json", "parameters.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                plan = Path(temp)
                for name in ("azuredeploy.json", "parameters.json"):
                    (plan / name).write_text("{}\n")
                deploy.write_json(plan / "plan.json", {"hashes": {name: deploy.digest(plan / name)
                                                                  for name in ("azuredeploy.json", "parameters.json")}})
                deploy.verify_plan(plan)
                (plan / filename).write_text('{"changed":true}\n')
                with self.assertRaisesRegex(RuntimeError, "Prepared plan changed"):
                    deploy.verify_plan(plan)

    def test_malformed_missing_or_duplicate_status_is_not_accepted(self):
        messages = ("no markers", "THOX_STATUS_BEGIN not-json THOX_STATUS_END",
                    "THOX_STATUS_BEGIN {} THOX_STATUS_END THOX_STATUS_BEGIN {} THOX_STATUS_END")
        for message in messages:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp, \
                    patch.object(deploy, "azure", side_effect=[{"name": "test"}, {"value": [{"message": message}]}]), \
                    self.assertRaises(RuntimeError):
                deploy.collect_status(options(), Path(temp))

    def test_unsafe_whatif_cannot_start_resource_deployment(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(deploy, "verify_plan"), patch.object(deploy, "ensure_empty_group"), \
                patch.object(deploy, "azure", side_effect=[{}, {"changes": [{"changeType": "Modify"}]}]) as az, \
                patch.object(deploy, "collect_status") as status, \
                self.assertRaisesRegex(RuntimeError, "create-only"):
            deploy.deploy(options(), Path(temp))
        self.assertFalse(any(call.args[1:4] == ("deployment", "group", "create") for call in az.call_args_list))
        status.assert_not_called()

    def test_failed_arm_deployment_cannot_claim_guest_acceptance(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(deploy, "verify_plan"), patch.object(deploy, "ensure_empty_group"), \
                patch.object(deploy, "azure", side_effect=[{}, {"changes": [{"changeType": "Create"}]},
                                                          {"properties": {"provisioningState": "Failed"}}]), \
                patch.object(deploy, "collect_status") as status, \
                self.assertRaisesRegex(RuntimeError, "did not report Succeeded"):
            deploy.deploy(options(), Path(temp))
        status.assert_not_called()

    def test_concurrent_resource_creation_aborts_before_paid_deployment(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(deploy, "verify_plan"), \
                patch.object(deploy, "ensure_empty_group", side_effect=[None, RuntimeError("The resource group is not empty.")]), \
                patch.object(deploy, "azure", side_effect=[{}, {"changes": [{"changeType": "Create"}]}]) as az, \
                self.assertRaisesRegex(RuntimeError, "not empty"):
            deploy.deploy(options(), Path(temp))
        self.assertFalse(any(call.args[1:4] == ("deployment", "group", "create") for call in az.call_args_list))


if __name__ == "__main__":
    unittest.main()
