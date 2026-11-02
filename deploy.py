#!/usr/bin/env python3
"""Prepare or deploy the real ThoxInux v0.9.1 guest on an Azure Ubuntu host.

Requires Python 3.10+, authenticated Azure CLI, and an explicit subscription.
Render and prepare do not provision Azure resources. Status runs read-only
host/guest checks through Azure Run Command.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
RELEASE = "v0.9.1"
SOURCE_COMMIT = "84b4a7e740496297ca79dff735834ca2a8f2bad3"
EFI_SHA256 = "1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89"
NETWORK_API = "2024-05-01"
COMPUTE_API = "2024-11-01"


def fail(message: str) -> None:
    raise RuntimeError(message)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tags(args: argparse.Namespace) -> dict[str, str]:
    return {"thox-project": "thoxinux", "thox-deployment": args.vm_name,
            "thox-release": RELEASE, "purpose": "open-source-release-test"}


def validate_inputs(args: argparse.Namespace) -> None:
    try:
        parsed = uuid.UUID(args.subscription)
    except (ValueError, AttributeError):
        fail("Supply the actual Azure subscription UUID with --subscription.")
    if parsed.int == 0:
        fail("The zero subscription UUID is a placeholder, not a deployment target.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,47}", args.vm_name):
        fail("VM name must be 2-48 lowercase letters, digits, or hyphens, starting with a letter.")
    if not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", args.resource_group):
        fail("Unsupported resource-group name.")
    if not re.fullmatch(r"[a-z0-9]+", args.location):
        fail("Use an Azure region identifier, for example centralus.")
    if args.image_version and not re.fullmatch(r"\d+\.\d+\.\d+", args.image_version):
        fail("Image version must be an explicit numeric version, never latest.")


def azure(args: argparse.Namespace, *words: str) -> object:
    if not shutil.which("az"):
        fail("Azure CLI is unavailable. Use an authenticated Azure Cloud Shell or install Azure CLI.")
    command = ["az", *words, "--subscription", args.subscription,
               "--only-show-errors", "--output", "json"]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode:
        fail(f"Azure command failed ({' '.join(words[:3])}):\n{result.stderr.strip()}")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        fail("Azure CLI returned invalid JSON. No success is inferred.")


def authenticated_account(args: argparse.Namespace) -> dict:
    account = azure(args, "account", "show")
    if account.get("id", "").lower() != args.subscription.lower() or account.get("state") != "Enabled":
        fail("The explicit subscription is not enabled or did not match the authenticated account.")
    safe = {key: account.get(key) for key in ("id", "tenantId", "name", "state")}
    print(json.dumps({"selected_account": safe}, indent=2), flush=True)
    return safe


def resolve_image(args: argparse.Namespace) -> str:
    selected = args.image_version or "latest"
    image = azure(args, "vm", "image", "show", "--location", args.location,
                  "--urn", f"Canonical:ubuntu-24_04-lts:server:{selected}")
    version = str(image.get("name", ""))
    if image.get("hyperVGeneration") != "V2" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail("Azure did not resolve an exact Ubuntu 24.04 Gen2 image version.")
    if args.image_version and version != args.image_version:
        fail("Azure returned a different image version than the explicitly selected version.")
    return version


def check_sku(args: argparse.Namespace) -> dict:
    inventory = azure(args, "vm", "list-skus", "--location", args.location,
                      "--resource-type", "virtualMachines", "--size", args.vm_size, "--all")
    matches = [item for item in inventory if item.get("name") == args.vm_size]
    if len(matches) != 1:
        fail(f"Could not resolve {args.vm_size} in {args.location} for this subscription.")
    sku = matches[0]
    if any(item.get("type") == "Location" for item in sku.get("restrictions", [])):
        fail("Azure reports a regional SKU restriction. Select an available region or resolve quota.")
    caps = {item["name"]: item["value"] for item in sku.get("capabilities", [])}
    if "V2" not in caps.get("HyperVGenerations", ""):
        fail("The selected SKU does not advertise Gen2 support.")
    if caps.get("TrustedLaunchDisabled", "False").lower() == "true":
        fail("Trusted Launch is unavailable for this SKU.")
    if caps.get("AcceleratedNetworkingEnabled", "False").lower() != "true":
        fail("The selected SKU does not advertise accelerated networking.")
    return {"name": sku["name"], "location": args.location, "capabilities": caps,
            "restrictions": sku.get("restrictions", [])}


def public_key(args: argparse.Namespace) -> str:
    if args.ssh_public_key:
        path = Path(args.ssh_public_key).expanduser().resolve()
    elif args.action == "render":
        fail("Offline render requires --ssh-public-key pointing to a real public key.")
    else:
        directory = Path.home() / ".ssh"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        private = directory / f"{args.vm_name}-azure"
        path = Path(str(private) + ".pub")
        if not path.exists():
            if private.exists():
                fail(f"Existing private key has no public companion: {private}. Refusing to overwrite it.")
            if not shutil.which("ssh-keygen"):
                fail("Supply --ssh-public-key; ssh-keygen is unavailable.")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private),
                            "-C", f"thoxos-azure-{RELEASE}"], check=True)
            print(f"Created an administration key at {private}; keep its private file on this client.")
    text = path.read_text().strip()
    if "PRIVATE KEY" in text or "\n" in text or not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa) [A-Za-z0-9+/]+=*(?: [^\r\n]+)?", text):
        fail("Expected a single OpenSSH public key, not a private key or certificate.")
    try:
        raw = base64.b64decode(text.split()[1], validate=True)
        key_type = text.split()[0].encode()
        if len(raw) < 20 or int.from_bytes(raw[:4], "big") != len(key_type) or raw[4:4+len(key_type)] != key_type:
            fail("SSH public key wire-format type does not match its declared type.")
    except ValueError:
        fail("Invalid base64 in SSH public key.")
    return text


def make_template(args: argparse.Namespace, image_version: str, key: str, bootstrap: bytes) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", image_version):
        fail("Refusing an unpinned image version.")
    if len(bootstrap) > 65535:
        fail("Bootstrap exceeds Azure custom-data size limit.")
    vm = args.vm_name
    common = {"location": args.location, "tags": tags(args)}
    def rid(kind: str, name: str) -> str:
        return f"[resourceId('{kind}', '{name}')]"
    nsg = rid("Microsoft.Network/networkSecurityGroups", f"{vm}-nsg")
    pip = rid("Microsoft.Network/publicIPAddresses", f"{vm}-egress")
    vnet = rid("Microsoft.Network/virtualNetworks", f"{vm}-vnet")
    nic = rid("Microsoft.Network/networkInterfaces", f"{vm}-nic")
    resources = [
        {"type": "Microsoft.Network/networkSecurityGroups", "apiVersion": NETWORK_API,
         "name": f"{vm}-nsg", **common,
         "properties": {"securityRules": [{"name": "ThoxDenyAllInbound", "properties": {
             "priority": 4096, "direction": "Inbound", "access": "Deny", "protocol": "*",
             "sourcePortRange": "*", "destinationPortRange": "*",
             "sourceAddressPrefix": "*", "destinationAddressPrefix": "*"}}]}},
        {"type": "Microsoft.Network/publicIPAddresses", "apiVersion": NETWORK_API,
         "name": f"{vm}-egress", **common, "sku": {"name": "Standard", "tier": "Regional"},
         "properties": {"publicIPAllocationMethod": "Static", "publicIPAddressVersion": "IPv4"}},
        {"type": "Microsoft.Network/virtualNetworks", "apiVersion": NETWORK_API,
         "name": f"{vm}-vnet", **common,
         "properties": {"addressSpace": {"addressPrefixes": ["10.93.0.0/16"]},
                        "subnets": [{"name": "host", "properties": {
                            "addressPrefix": "10.93.1.0/24", "defaultOutboundAccess": False}}]}},
        {"type": "Microsoft.Network/networkInterfaces", "apiVersion": NETWORK_API,
         "name": f"{vm}-nic", **common, "dependsOn": [nsg, pip, vnet],
         "properties": {"enableAcceleratedNetworking": True, "enableIPForwarding": False,
                        "networkSecurityGroup": {"id": nsg},
                        "ipConfigurations": [{"name": "primary", "properties": {
                            "primary": True, "privateIPAllocationMethod": "Dynamic",
                            "subnet": {"id": f"[resourceId('Microsoft.Network/virtualNetworks/subnets', '{vm}-vnet', 'host')]"},
                            "publicIPAddress": {"id": pip}}}]}},
        {"type": "Microsoft.Compute/virtualMachines", "apiVersion": COMPUTE_API,
         "name": vm, **common, "dependsOn": [nic],
         "properties": {"hardwareProfile": {"vmSize": args.vm_size},
             "securityProfile": {"securityType": "TrustedLaunch",
                                 "uefiSettings": {"secureBootEnabled": True, "vTpmEnabled": True}},
             "storageProfile": {"imageReference": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts",
                                                   "sku": "server", "version": image_version},
                 "osDisk": {"name": f"{vm}-os", "createOption": "FromImage", "diskSizeGB": 128,
                            "caching": "ReadWrite", "deleteOption": "Detach",
                            "managedDisk": {"storageAccountType": "StandardSSD_LRS"}}},
             "osProfile": {"computerName": vm, "adminUsername": "thoxadmin",
                 "customData": "[parameters('bootstrapBase64')]",
                 "linuxConfiguration": {"disablePasswordAuthentication": True, "provisionVMAgent": True,
                     "ssh": {"publicKeys": [{"path": "/home/thoxadmin/.ssh/authorized_keys",
                                             "keyData": "[parameters('sshPublicKey')]"}]}}},
             "networkProfile": {"networkInterfaces": [{"id": nic, "properties": {"primary": True,
                                                                                     "deleteOption": "Detach"}}]},
             "diagnosticsProfile": {"bootDiagnostics": {"enabled": True}}}},
    ]
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "metadata": {"description": "Actual ThoxInux engineering preview in QEMU TCG on Azure Ubuntu",
                         "release": RELEASE, "sourceCommit": SOURCE_COMMIT, "efiSha256": EFI_SHA256},
            "parameters": {"sshPublicKey": {"type": "string"}, "bootstrapBase64": {"type": "string"}},
            "resources": resources,
            "outputs": {"vmResourceId": {"type": "string", "value": rid("Microsoft.Compute/virtualMachines", vm)},
                        "consoleAccess": {"type": "string", "value": "Azure Run Command; inbound connections denied"}}}


def prepare(args: argparse.Namespace, account: dict | None) -> Path:
    plan = Path(args.out).expanduser().resolve()
    if plan.exists() and any(plan.iterdir()):
        fail(f"Plan directory is not empty: {plan}. Choose a new --out directory to preserve evidence.")
    image = args.image_version if args.action == "render" else resolve_image(args)
    if not image:
        fail("Offline render requires --image-version from an earlier Azure image lookup.")
    sku = None if args.action == "render" else check_sku(args)
    key = public_key(args)
    bootstrap = (ROOT / "guest-bootstrap.sh").read_bytes()
    template = make_template(args, image, key, bootstrap)
    write_json(plan / "azuredeploy.json", template)
    write_json(plan / "parameters.json", {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0", "parameters": {
            "sshPublicKey": {"value": key},
            "bootstrapBase64": {"value": base64.b64encode(bootstrap).decode()}}})
    write_json(plan / "plan.json", {
        "state": "prepared", "azure_deployment": "not_run", "guest_acceptance": "not_run",
        "subscription": args.subscription, "account": account, "resource_group": args.resource_group,
        "vm_name": args.vm_name, "location": args.location, "vm_size": args.vm_size,
        "image_version": image, "sku": sku, "release": RELEASE,
        "source_commit": SOURCE_COMMIT, "efi_sha256": EFI_SHA256,
        "bootstrap_sha256": hashlib.sha256(bootstrap).hexdigest(),
        "hashes": {name: digest(plan / name) for name in ("azuredeploy.json", "parameters.json")},
        "costs": "Paid Azure compute, Standard SSD, Standard public IPv4 and applicable transfer. No quoted price.",
        "access": "All inbound denied. Standard public IPv4 supplies explicit outbound connectivity.",
        "limits": "QEMU TCG kernel/controller preview. Native Azure kernel boot and a full graphical desktop are not accepted."})
    print(f"Prepared immutable plan: {plan}", flush=True)
    return plan


def ensure_empty_group(args: argparse.Namespace) -> None:
    exists = azure(args, "group", "exists", "--name", args.resource_group)
    if exists:
        group = azure(args, "group", "show", "--name", args.resource_group)
        if group.get("location") != args.location:
            fail("Existing resource group is in another region; choose a new dedicated group.")
        if not all((group.get("tags") or {}).get(k) == v for k, v in tags(args).items()):
            fail("Existing resource group is not tagged for this exact deployment. Refusing to modify it.")
        items = azure(args, "resource", "list", "--resource-group", args.resource_group)
        if items:
            fail("The resource group is not empty. Use status for an existing deployment; inspect partial deployments before any retry.")
    else:
        azure(args, "group", "create", "--name", args.resource_group, "--location", args.location,
              "--tags", *[f"{k}={v}" for k, v in tags(args).items()])


def verify_plan(plan: Path) -> None:
    manifest = json.loads((plan / "plan.json").read_text())
    if set(manifest.get("hashes", {})) != {"azuredeploy.json", "parameters.json"}:
        fail("Invalid prepared-plan file inventory.")
    for name, expected in manifest["hashes"].items():
        if digest(plan / name) != expected:
            fail(f"Prepared plan changed: {name}. Prepare a new plan.")


def acceptance_errors(guest: dict) -> list[str]:
    expected = {
        "marker": "THOXOS_RELEASE_STATUS_V1", "release": RELEASE,
        "source_commit": SOURCE_COMMIT, "efi_sha256": EFI_SHA256,
        "service_state": "active", "qmp_status": "running",
    }
    errors = [f"{key} mismatch" for key, value in expected.items() if guest.get(key) != value]
    for key in ("ok", "checksums_verified"):
        if guest.get(key) is not True:
            errors.append(f"{key} is not true")
    markers = guest.get("serial_markers")
    if not isinstance(markers, dict):
        errors.append("serial_markers is not an object")
        markers = {}
    for key in ("unified_identity", "controller", "prompt"):
        if markers.get(key) is not True:
            errors.append(f"missing current boot serial marker: {key}")
    if markers.get("panic") is not False:
        errors.append("panic check did not explicitly pass")
    smoke = guest.get("smoke")
    if not isinstance(smoke, dict):
        errors.append("smoke is not an object")
        smoke = {}
    if smoke.get("ok") is not True or smoke.get("current_boot") is not True:
        errors.append("current boot guest command checks did not pass")
    if smoke.get("commands") != ["status", "components", "domain list", "help"]:
        errors.append("guest command inventory mismatch")
    if guest.get("errors") != []:
        errors.append("guest reported errors or omitted its error inventory")
    return errors


def collect_status(args: argparse.Namespace, out: Path, wait: bool = False) -> dict:
    vm = azure(args, "vm", "get-instance-view", "--resource-group", args.resource_group, "--name", args.vm_name)
    write_json(out / "instance-view.json", vm)
    safe_status = {"name": vm.get("name"), "statuses": vm.get("instanceView", {}).get("statuses", [])}
    print(json.dumps({"azure_instance": safe_status}, indent=2), flush=True)
    script = ("timeout 1200 cloud-init status --wait >/dev/null 2>&1 || true\n" if wait else "") + """printf 'THOX_STATUS_BEGIN\\n'
if [ -x /usr/local/sbin/thoxos-release-status ]; then
  /usr/local/sbin/thoxos-release-status
else
  printf '{"ok":false,"reason":"Guest status helper not installed; inspect cloud-init logs"}\\n'
fi
printf 'THOX_STATUS_END\\n'
"""
    result = azure(args, "vm", "run-command", "invoke", "--resource-group", args.resource_group,
                   "--name", args.vm_name, "--command-id", "RunShellScript", "--scripts", script)
    write_json(out / "run-command-status.json", result)
    joined = "\n".join(item.get("message", "") for item in result.get("value", []))
    matches = re.findall(r"THOX_STATUS_BEGIN\s*(.*?)\s*THOX_STATUS_END", joined, re.S)
    if len(matches) != 1:
        fail("No unambiguous guest status JSON returned. Read the saved Run Command result.")
    try:
        guest = json.loads(matches[0])
    except json.JSONDecodeError:
        fail("Guest status is not valid JSON. No boot acceptance is inferred.")
    if not isinstance(guest, dict):
        fail("Guest status is not a JSON object. No boot acceptance is inferred.")
    errors = acceptance_errors(guest)
    guest["controller_acceptance_errors"] = errors
    if errors:
        guest["ok"] = False
    write_json(out / "guest-status.json", guest)
    print(json.dumps({"guest_status": guest}, indent=2), flush=True)
    print(f"Azure portal: https://portal.azure.com/#resource/subscriptions/{args.subscription}/resourceGroups/{args.resource_group}/providers/Microsoft.Compute/virtualMachines/{args.vm_name}/overview")
    return guest


def deploy(args: argparse.Namespace, plan: Path) -> None:
    verify_plan(plan)
    ensure_empty_group(args)
    base = ["--resource-group", args.resource_group, "--template-file", str(plan / "azuredeploy.json"),
            "--parameters", "@" + str(plan / "parameters.json")]
    print("Validating the actual Azure plan; an empty dedicated resource group may now exist.", flush=True)
    write_json(plan / "arm-validation.json", azure(args, "deployment", "group", "validate", *base))
    preview = azure(args, "deployment", "group", "what-if", *base, "--result-format", "FullResourcePayloads", "--no-pretty-print")
    write_json(plan / "what-if.json", preview)
    changes = preview.get("changes", [])
    if not changes or any(c.get("changeType") not in ("Create", "NoChange") for c in changes):
        fail("What-if did not return a create-only plan. Inspect what-if.json; no VM deployment was started.")
    verify_plan(plan)
    ensure_empty_group(args)
    print("Server validation passed. Creating one paid VM with inbound traffic denied.", flush=True)
    result = azure(args, "deployment", "group", "create", *base, "--name", f"{args.vm_name}-deployment", "--mode", "Incremental")
    write_json(plan / "arm-deployment.json", result)
    if result.get("properties", {}).get("provisioningState") != "Succeeded":
        fail("Azure deployment did not report Succeeded. Preserve the evidence and inspect Azure.")
    print("Azure resource provisioning succeeded. Waiting for host bootstrap and real guest evidence.", flush=True)
    guest = collect_status(args, plan, wait=True)
    write_json(plan / "execution-result.json", {"azure_provisioning": "succeeded", "guest_acceptance": guest,
                                              "source_commit": SOURCE_COMMIT, "release": RELEASE})
    if guest.get("ok") is not True:
        fail("The VM was created, but guest acceptance has not passed. See saved evidence; do not call the release boot verified.")
    print("The actual ThoxInux release guest is running and the collected acceptance checks passed.")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("render", "prepare", "deploy", "status"))
    p.add_argument("--subscription", required=True, help="Explicit intended Azure subscription UUID")
    p.add_argument("--location", default="centralus")
    p.add_argument("--resource-group", default="rg-thoxos-open-release-v091")
    p.add_argument("--vm-name", default="thoxos-open-v091")
    p.add_argument("--vm-size", choices=("Standard_D2s_v5", "Standard_D4s_v5"), default="Standard_D2s_v5")
    p.add_argument("--ssh-public-key", help="Existing public key path; otherwise generate a dedicated client key")
    p.add_argument("--image-version", help="Exact Ubuntu24.04 Gen2 version; authenticated mode resolves if omitted")
    p.add_argument("--out", default="thoxos-azure-plan", help="New plan/evidence directory")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        validate_inputs(args)
        account = None if args.action == "render" else authenticated_account(args)
        if args.action == "status":
            guest = collect_status(args, Path(args.out).expanduser().resolve())
            return 0 if guest.get("ok") is True else 2
        plan = prepare(args, account)
        if args.action == "deploy":
            deploy(args, plan)
        return 0
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
