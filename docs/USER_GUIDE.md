# Deploy and operate ThoxOS on Azure

This deployment package, version **0.1.0**, provisions an Ubuntu Azure host and runs the actual **ThoxInux v0.9.1 engineering-preview kernel** in QEMU. These are separate version numbers. Check the [README](../README.md) for the repository's publication and validation status; this guide is not evidence of a completed Azure deployment.

Your AI. Your Data. Your Rules.™

## Before deploying

You need an Azure subscription with permission to create compute and network resources, sufficient regional quota, and permission to run commands on the VM. Use a **new, dedicated resource group**. The portal template cannot inspect and refuse every pre-existing resource the way the CLI helper does; deploying over existing names can update those resources.

Prepare an OpenSSH **public** key. Never paste a private key into the portal. The public key provisions the host administrator account; it does not open an inbound SSH port. To generate a dedicated key in Cloud Shell or a local terminal:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/thoxos-azure
cat ~/.ssh/thoxos-azure.pub
```

Keep the private file on your client. If either filename already exists, choose another filename instead of overwriting it.

This creates paid Azure resources: VM compute, a 128 GiB Standard SSD, a Standard public IPv4 address, and applicable transfer. There is no GPU, Bastion or NAT gateway. Review [Azure pricing](https://azure.microsoft.com/en-us/pricing/calculator/) for your subscription and region. No automatic shutdown is configured.

## Deploy with the button

1. Open the **Deploy to Azure** button in the [README](../README.md) once its published template URL is available. Sign in to the intended Azure account.
2. Select the subscription and choose **Create new** for the resource group. The suggested name is `rg-thoxos-open-release-v091`.
3. Select a region with available quota. Keep the generated unique VM name, or explicitly use `thoxos-open-v091` in the new resource group. Record the final VM name from the deployment outputs.
4. Use `Standard_D2s_v5` for the default 2-vCPU, 8-GiB host. `Standard_D4s_v5` provides 4 vCPUs and 16 GiB if more host capacity is needed. Paste the public SSH key. The host administrator account is fixed to `thoxadmin`.
5. Review the parameters and Azure's validation results, then submit the deployment. Azure creates the resources and executes the mandatory guest-verification Run Command after the VM is provisioned.

The portal host-image default is `latest` for Canonical's Ubuntu 24.04 Gen2 image. Azure resolves that image during deployment. An explicit available image version can be supplied for repeatable host provisioning. The **ThoxInux kernel, launcher and provenance files remain pinned to exact hashes** in both cases.

If the repository is not yet published, open Azure's **Template deployment**, select **Build your own template in editor**, paste the complete `azuredeploy.json`, and save it. Complete the same parameters. The standalone template embeds the bootstrap; it does not require a separate private script URL. See Microsoft's [custom-template deployment instructions](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-portal).

## Understand completion

The final verification command waits for cloud-init and checks the current guest. Inspect the overall deployment **and its `verify-thoxinux-release` child resource**. A VM can exist and incur charges even when bootstrap or guest verification fails. The acceptance receipt is available at `/var/lib/thoxinux-azure/azure-readiness.json` after the verification command writes it, inside a root-owned directory with mode `0700`; boot-specific guest logs remain separate.

Acceptance requires the exact release and EFI hash, verified payload files, an active guest service, QMP reporting `running`, current-boot kernel/controller/prompt markers, no observed panic marker, and successful `status`, `components`, `domain list` and `help` commands. Successful VM allocation alone does not establish a successful kernel boot.

This is a headless kernel/controller environment. There is no web desktop, RDP session or public guest-console endpoint. Native ThoxInux boot on Azure Hyper-V and a complete graphical ThoxOS release are not qualified by this package.

## Use the actual guest

In the VM's Azure portal page, open **Operations → Run command → RunShellScript**. Run:

```bash
/usr/local/sbin/thoxos-release-status
```

The output is JSON; `ok` must be `true` with the expected release `v0.9.1`. To issue individual controller commands:

```bash
/usr/local/sbin/thoxinux-console status
/usr/local/sbin/thoxinux-console components
/usr/local/sbin/thoxinux-console 'domain list'
/usr/local/sbin/thoxinux-console help
```

Run Command executes through the Azure VM agent without opening SSH. It is a noninteractive administration channel, not a streaming terminal. Action Run Command output is limited, so inspect large logs in bounded chunks. See [Microsoft's Run Command documentation](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command).

## CLI alternative and evidence collection

In a checked-out copy of this deployment repository, use an already authenticated Azure CLI and an explicit subscription ID:

```bash
python3 deploy.py deploy \
  --subscription YOUR_SUBSCRIPTION_UUID \
  --location centralus \
  --out thoxos-azure-plan
```

The CLI resolves an exact marketplace image version before preparing the plan, checks the selected SKU, refuses unrelated or nonempty resource groups, runs ARM validation and what-if, then provisions and collects guest evidence. It creates a dedicated client SSH key if no public key was supplied. The private key stays outside the deployment plan.

CLI defaults are `rg-thoxos-open-release-v091` and `thoxos-open-v091`. The portal can generate a different VM name. In the commands below, replace resource names with the actual deployment outputs when they differ.

`--out` must be a new or empty directory for preparation/deployment. Use `prepare` instead of `deploy` to produce a local plan without creating Azure resources. `render` is the offline mode and requires an explicit image version and public key.

Collect fresh status after a portal or CLI deployment:

```bash
python3 deploy.py status \
  --subscription YOUR_SUBSCRIPTION_UUID \
  --resource-group rg-thoxos-open-release-v091 \
  --vm-name thoxos-open-v091 \
  --out thoxos-azure-status
```

Record the marketplace image that Azure actually used:

```bash
az vm show \
  --subscription YOUR_SUBSCRIPTION_UUID \
  --resource-group rg-thoxos-open-release-v091 \
  --name thoxos-open-v091 \
  --query storageProfile.imageReference --output json
```

Retain the returned `exactVersion` when exposed. Do not record `latest` as an exact version or infer a missing value. Preserve deployment responses, guest status and relevant boot logs with the deployment source revision.

## Stop and resume

Deallocate the host when it is no longer needed:

```bash
az vm deallocate --subscription YOUR_SUBSCRIPTION_UUID \
  --resource-group rg-thoxos-open-release-v091 --name thoxos-open-v091
```

The persistent disk and static public IP remain billable. Resume with:

```bash
az vm start --subscription YOUR_SUBSCRIPTION_UUID \
  --resource-group rg-thoxos-open-release-v091 --name thoxos-open-v091
```

The guest service starts with the host and performs fresh controller checks for that boot. Run the status command again before relying on the guest. The deployment does not automatically delete resources or schedule shutdown.

## Troubleshooting and recovery

| Symptom | Action |
| --- | --- |
| Quota, SKU or policy rejection | Read Azure's exact error. Select an allowed region/size or resolve the subscription policy. Do not weaken the template's access controls to bypass it. |
| VM exists but deployment verification failed | Inspect cloud-init, the verification resource and guest service. Preserve the resource group and logs before deciding whether to retry or remove anything. |
| Bootstrap download or checksum failed | Preserve the error and verify the pinned release availability. Do not replace the expected hash with the downloaded file's hash merely to pass. |
| Guest status is incomplete or `ok: false` | Inspect the current boot log and service journal. An active service alone is insufficient. |
| Public IP does not accept SSH or HTTP | Expected: the NSG denies all inbound traffic. Use Azure Run Command. |
| CLI refuses the resource group or plan directory | Inspect the existing resources/evidence. Use `status` for an existing VM; create a separate empty plan directory for new preparation. |

Useful RunShellScript diagnostics:

```bash
cloud-init status --long
tail -n 80 /var/log/cloud-init-output.log
journalctl -u thoxinux.service -n 60 --no-pager
cat /var/log/thoxinux/current.json
```

Guest state is under `/var/lib/thoxinux`; boot-specific evidence is under `/var/log/thoxinux`. Preserve both before rebuilding. This package has no automatic backup or in-place kernel-upgrade process. For a release change, validate a separate host with the new pinned artifacts before retiring the old environment.
