#!/usr/bin/env python3
"""Generate the standalone Deploy to Azure ARM template from the reviewed bootstrap.

The supported button uses the normal Microsoft.Template/uri deployment form.
No linked templates, unsigned bootstrap download, or custom portal UI is needed.

Primary API references:
https://learn.microsoft.com/azure/azure-resource-manager/templates/deploy-to-azure-button
https://learn.microsoft.com/azure/templates/microsoft.compute/2024-11-01/virtualmachines
https://learn.microsoft.com/azure/templates/microsoft.compute/2024-11-01/virtualmachines/runcommands
https://learn.microsoft.com/azure/templates/microsoft.network/2024-05-01/networkinterfaces
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "v0.9.1"
PACKAGE_VERSION = "v0.1.0"
SOURCE_COMMIT = "84b4a7e740496297ca79dff735834ca2a8f2bad3"
EFI_SHA256 = "1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89"
NETWORK_API = "2024-05-01"
COMPUTE_API = "2024-11-01"


def acceptance_python() -> str:
    """Independent checks on the real status receipt; an ok flag is insufficient."""
    return '''import json,sys

def rejection_reasons(receipt, status_exit):
    failures=[]
    if not isinstance(receipt,dict):
        return ['Guest status is not a JSON object']
    if status_exit!=0:
        failures.append('Guest status command did not exit zero')
    expected={
      'marker':'THOXOS_RELEASE_STATUS_V1',
      'release':'v0.9.1',
      'source_commit':'84b4a7e740496297ca79dff735834ca2a8f2bad3',
      'efi_sha256':'1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89',
      'service_state':'active',
      'qmp_status':'running',
    }
    for key,value in expected.items():
        if receipt.get(key)!=value:
            failures.append('Unexpected '+key)
    for key in ('ok','checksums_verified'):
        if receipt.get(key) is not True:
            failures.append(key+' is not true')
    markers=receipt.get('serial_markers')
    if not isinstance(markers,dict):
        failures.append('Serial marker evidence is missing')
    else:
        for key in ('unified_identity','controller','prompt'):
            if markers.get(key) is not True:
                failures.append('Missing serial marker: '+key)
        if markers.get('panic') is not False:
            failures.append('Serial panic check did not pass')
    smoke=receipt.get('smoke')
    if not isinstance(smoke,dict):
        failures.append('Current-boot smoke evidence is missing')
    else:
        for key in ('ok','current_boot'):
            if smoke.get(key) is not True:
                failures.append('Smoke '+key+' is not true')
        if smoke.get('commands')!=['status','components','domain list','help']:
            failures.append('Expected smoke command list is missing')
    if not isinstance(receipt.get('boot_id'),str) or not receipt['boot_id']:
        failures.append('Boot identity is missing')
    if receipt.get('errors')!=[]:
        failures.append('Status contains errors or an invalid error inventory')
    return failures

if __name__=='__main__':
    with open(sys.argv[2],encoding='utf-8') as stream:
        receipt=json.load(stream)
    reasons=rejection_reasons(receipt,int(sys.argv[1]))
    print(json.dumps({'azure_guest_acceptance':'passed' if not reasons else 'failed',
                      'reasons':reasons,'receipt_path':sys.argv[2],'guest_status':receipt},indent=2))
    raise SystemExit(1 if reasons else 0)
'''


def readiness_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
# Runs independently of cloud-init, after the Azure VM agent is available.
# A nonzero cloud-init result or guest readiness result fails ARM deployment.
timeout 1500 cloud-init status --wait --long
test -x /usr/local/sbin/thoxos-release-status
umask 077
# Root writes only in a root-owned directory, never a guest-writable log path.
test ! -L /var/lib/thoxinux-azure
install -d -o root -g root -m 0700 /var/lib/thoxinux-azure
receipt=$(mktemp /var/lib/thoxinux-azure/readiness.XXXXXXXX.json)
status_exit=0
/usr/local/sbin/thoxos-release-status >"$receipt" || status_exit=$?
cp -- "$receipt" /var/lib/thoxinux-azure/azure-readiness.json
python3 - "$status_exit" "$receipt" <<'PY'
""" + acceptance_python() + "PY\n"


def make_template(bootstrap: bytes) -> dict:
    if not bootstrap.startswith(b"#!/usr/bin/env bash\n"):
        raise ValueError("Expected the reviewed bash bootstrap, not an empty file or a download URL")
    if b"\x00" in bootstrap:
        raise ValueError("Bootstrap contains NUL bytes")
    # Azure's decoded customData maximum is 64 KiB. The encoded guard is more
    # conservative and also keeps the payload easy to inspect and transport.
    encoded = base64.b64encode(bootstrap).decode("ascii")
    if len(bootstrap) > 65535 or len(encoded) > 65535:
        raise ValueError("Bootstrap exceeds the conservative Azure customData size limit")

    def rid(kind: str, variable: str) -> str:
        return f"[resourceId('{kind}', variables('{variable}'))]"

    vm_id = "[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]"
    nsg = rid("Microsoft.Network/networkSecurityGroups", "nsgName")
    pip = rid("Microsoft.Network/publicIPAddresses", "egressIpName")
    vnet = rid("Microsoft.Network/virtualNetworks", "vnetName")
    nic = rid("Microsoft.Network/networkInterfaces", "nicName")
    common = {"location": "[parameters('location')]", "tags": "[variables('resourceTags')]"}
    parameters = {
        "vmName": {
            "type": "string", "minLength": 2, "maxLength": 48,
            "defaultValue": "[concat('thoxos-', uniqueString(resourceGroup().id, deployment().name))]",
            "metadata": {"description": "Name of the new VM. Use 2-48 lowercase letters, digits or hyphens, starting with a letter. Deploy into a NEW dedicated resource group; do not reuse an unrelated VM name. The generated default is scoped to this deployment."},
        },
        "location": {
            "type": "string", "defaultValue": "[resourceGroup().location]",
            "metadata": {"description": "Azure region with quota and capacity for the selected Dsv5 VM size and Ubuntu 24.04 Gen2. The resource-group region is the default."},
        },
        "vmSize": {
            "type": "string", "defaultValue": "Standard_D2s_v5",
            "allowedValues": ["Standard_D2s_v5", "Standard_D4s_v5"],
            "metadata": {"description": "D2s_v5: 2 vCPU / 8 GiB host RAM. D4s_v5: 4 vCPU / 16 GiB. The ThoxInux TCG guest uses 1 vCPU / 2 GiB. Compute, 128 GiB disk and Standard public IP incur Azure charges; this VM does not shut down automatically."},
        },
        "adminUsername": {
            "type": "string", "defaultValue": "thoxadmin", "minLength": 1, "maxLength": 32,
            "allowedValues": ["thoxadmin"],
            "metadata": {"description": "Linux host administrator. Fixed to thoxadmin to prevent collision with the isolated thoxinux service account. Password authentication is disabled; guest administration uses Azure Run Command with no inbound ports opened."},
        },
        "sshPublicKey": {
            "type": "string", "minLength": 40, "maxLength": 16384,
            "metadata": {"description": "Paste your EXISTING OpenSSH PUBLIC key, such as the full contents of id_ed25519.pub or id_rsa.pub. Never paste a private key. This stores the host administration public key; the template opens no SSH port."},
        },
        "imageVersion": {
            "type": "string", "defaultValue": "latest", "minLength": 5, "maxLength": 80,
            "metadata": {"description": "Ubuntu host marketplace image version, or latest for the current regional Ubuntu 24.04 Gen2 image. The resolved imageReference is output for recording. Use an explicit numeric version for reproducible host builds. The guest remains pinned to ThoxInux v0.9.1 regardless."},
        },
    }
    variables = {
        "nsgName": "[concat(parameters('vmName'), '-nsg')]",
        "egressIpName": "[concat(parameters('vmName'), '-egress')]",
        "vnetName": "[concat(parameters('vmName'), '-vnet')]",
        "nicName": "[concat(parameters('vmName'), '-nic')]",
        "resourceTags": {
            "thox-project": "thoxinux", "thox-deployment": "[parameters('vmName')]",
            "thox-release": RELEASE, "thox-package": PACKAGE_VERSION,
            "purpose": "open-source-release-test",
        },
        # Literal data, not a giant ARM expression: Azure limits expressions
        # independently from the VM's customData size.
        "bootstrapBase64": encoded,
    }
    resources = [
        {"type": "Microsoft.Network/networkSecurityGroups", "apiVersion": NETWORK_API,
         "name": "[variables('nsgName')]", **common,
         "properties": {"securityRules": [{"name": "ThoxDenyAllInbound", "properties": {
             "priority": 4096, "direction": "Inbound", "access": "Deny", "protocol": "*",
             "sourcePortRange": "*", "destinationPortRange": "*",
             "sourceAddressPrefix": "*", "destinationAddressPrefix": "*"}}]}},
        {"type": "Microsoft.Network/publicIPAddresses", "apiVersion": NETWORK_API,
         "name": "[variables('egressIpName')]", **common,
         "sku": {"name": "Standard", "tier": "Regional"},
         "properties": {"publicIPAllocationMethod": "Static", "publicIPAddressVersion": "IPv4"}},
        {"type": "Microsoft.Network/virtualNetworks", "apiVersion": NETWORK_API,
         "name": "[variables('vnetName')]", **common,
         "properties": {"addressSpace": {"addressPrefixes": ["10.93.0.0/16"]},
                        "subnets": [{"name": "host", "properties": {
                            "addressPrefix": "10.93.1.0/24", "defaultOutboundAccess": False}}]}},
        {"type": "Microsoft.Network/networkInterfaces", "apiVersion": NETWORK_API,
         "name": "[variables('nicName')]", **common, "dependsOn": [nsg, pip, vnet],
         "properties": {"enableAcceleratedNetworking": True, "enableIPForwarding": False,
                        "networkSecurityGroup": {"id": nsg},
                        "ipConfigurations": [{"name": "primary", "properties": {
                            "primary": True, "privateIPAllocationMethod": "Dynamic",
                            "privateIPAddressVersion": "IPv4",
                            "subnet": {"id": "[resourceId('Microsoft.Network/virtualNetworks/subnets', variables('vnetName'), 'host')]"},
                            "publicIPAddress": {"id": pip}}}]}},
        {"type": "Microsoft.Compute/virtualMachines", "apiVersion": COMPUTE_API,
         "name": "[parameters('vmName')]", **common, "dependsOn": [nic],
         "properties": {
             "hardwareProfile": {"vmSize": "[parameters('vmSize')]"},
             "securityProfile": {"securityType": "TrustedLaunch",
                                 "uefiSettings": {"secureBootEnabled": True, "vTpmEnabled": True}},
             "storageProfile": {
                 "imageReference": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts",
                                    "sku": "server", "version": "[parameters('imageVersion')]"},
                 "osDisk": {"name": "[concat(parameters('vmName'), '-os')]",
                            "createOption": "FromImage", "diskSizeGB": 128,
                            "caching": "ReadWrite", "deleteOption": "Detach",
                            "managedDisk": {"storageAccountType": "StandardSSD_LRS"}}},
             "osProfile": {
                 "computerName": "[parameters('vmName')]", "adminUsername": "[parameters('adminUsername')]",
                 "customData": "[variables('bootstrapBase64')]",
                 "allowExtensionOperations": True,
                 "linuxConfiguration": {"disablePasswordAuthentication": True, "provisionVMAgent": True,
                     "ssh": {"publicKeys": [{"path": "[format('/home/{0}/.ssh/authorized_keys', parameters('adminUsername'))]",
                                             "keyData": "[parameters('sshPublicKey')]"}]}}},
             "networkProfile": {"networkInterfaces": [{"id": nic,
                 "properties": {"primary": True, "deleteOption": "Detach"}}]},
             "diagnosticsProfile": {"bootDiagnostics": {"enabled": True}},
         }},
        {"type": "Microsoft.Compute/virtualMachines/runCommands", "apiVersion": COMPUTE_API,
         "name": "[format('{0}/verify-thoxinux-release', parameters('vmName'))]",
         **common, "dependsOn": [vm_id],
         "properties": {"asyncExecution": False, "timeoutInSeconds": 1800,
                        "treatFailureAsDeploymentFailure": True,
                        "source": {"script": readiness_script()}}},
    ]
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "metadata": {
            "description": "Deploy a paid Azure Ubuntu 24.04 host running the genuine ThoxInux v0.9.1 open source engineering-preview kernel in a persistent nonroot QEMU TCG guest. Native Ginux Hyper-V boot and a graphical ThoxOS desktop are not qualified by this package. All inbound ports are denied; manage through Azure Run Command. Deployment waits for actual current-boot acceptance.",
            "packageVersion": PACKAGE_VERSION, "release": RELEASE,
            "sourceCommit": SOURCE_COMMIT, "efiSha256": EFI_SHA256,
            "bootstrapSha256": hashlib.sha256(bootstrap).hexdigest(),
            "bootstrapBytes": len(bootstrap),
            "hostImagePolicy": "Regional latest by default; exact imageReference is exposed in outputs. Guest release is pinned.",
            "validationBoundary": "Local checks do not replace Azure validate/what-if, actual resource deployment, or on-host guest acceptance.",
            "generatedBy": "scripts/generate_template.py",
        },
        "parameters": parameters, "variables": variables, "resources": resources,
        "outputs": {
            "vmResourceId": {"type": "string", "value": vm_id},
            "vmName": {"type": "string", "value": "[parameters('vmName')]"},
            "resourceGroup": {"type": "string", "value": "[resourceGroup().name]"},
            "location": {"type": "string", "value": "[parameters('location')]"},
            "hostImageReference": {"type": "object", "value": "[reference(resourceId('Microsoft.Compute/virtualMachines', parameters('vmName')), '2024-11-01').storageProfile.imageReference]"},
            "release": {"type": "string", "value": RELEASE},
            "expectedEfiSha256": {"type": "string", "value": EFI_SHA256},
            "consoleAccess": {"type": "string", "value": "Azure Run Command. Inbound network connections are denied. Guest console and QMP use local UNIX sockets."},
            "verifyCommand": {"type": "string", "value": "[format('az vm run-command invoke --subscription \"{0}\" --resource-group \"{1}\" --name \"{2}\" --command-id RunShellScript --scripts /usr/local/sbin/thoxos-release-status', subscription().subscriptionId, resourceGroup().name, parameters('vmName'))]"},
            "acceptanceResourceId": {"type": "string", "value": "[resourceId('Microsoft.Compute/virtualMachines/runCommands', parameters('vmName'), 'verify-thoxinux-release')]"},
            "acceptanceScope": {"type": "string", "value": "Managed Run Command gates provisioning on the released EFI hash, active guest, live QMP status, actual serial identity and four current-boot controller checks. Inspect execution state and run fresh status after later changes."},
        },
    }


def render(bootstrap: bytes) -> bytes:
    return (json.dumps(make_template(bootstrap), indent=2) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", type=Path, default=ROOT / "guest-bootstrap.sh")
    parser.add_argument("--output", type=Path, default=ROOT / "azuredeploy.json")
    parser.add_argument("--check", action="store_true", help="Check generated bytes without writing")
    args = parser.parse_args()
    try:
        generated = render(args.bootstrap.read_bytes())
        if args.check:
            if not args.output.is_file() or args.output.read_bytes() != generated:
                parser.error("azuredeploy.json differs from the reviewed bootstrap/generator; regenerate it")
            print("ARM template matches the bootstrap and generator byte for byte.")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(generated)
            print(f"Generated {args.output} ({len(generated)} bytes); Azure execution not implied.")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
