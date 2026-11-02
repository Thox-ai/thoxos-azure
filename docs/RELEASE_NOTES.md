# ThoxOS Azure Deployment v0.1.0

Engineering prerelease of deployment resources for the actual ThoxInux v0.9.1 open source kernel.

These notes describe the versioned source package. The [GitHub Releases entry](https://github.com/Thox-ai/thoxos-azure/releases) determines whether this version has been published; the notes alone do not establish publication or Azure acceptance.

## Included

- A standard Deploy to Azure button and standalone ARM template.
- Ubuntu 24.04 Gen2 host with Trusted Launch, 128 GiB Standard SSD, explicit outbound connectivity and all inbound traffic denied.
- Nonroot persistent QEMU TCG guest with exact release/launcher/receipt checksums.
- A required managed Run Command that verifies the real current guest boot before ARM deployment can succeed.
- Root-owned Azure verification receipts and privilege-separated local console operations.
- Source validation CI followed by an automatic engineering-prerelease job for canonical `main` pushes, with exact-commit publication receipts.
- A guarded CLI deployment path, an initial repository-publication helper and reproducible source archives.
- User, architecture, development, security and troubleshooting documentation.

## Installation and operation

Use the README's **Deploy to Azure** button after the public package tag and its raw template are available. Choose a new dedicated resource group, supply a public SSH key, and review Azure's validation and costs. The host administrator is `thoxadmin`. A CLI path is also provided for subscription-explicit preparation, validation, what-if, deployment and status collection.

The default host is `Standard_D2s_v5` with 2 vCPUs, 8 GiB RAM and a 128 GiB Standard SSD. `Standard_D4s_v5` is optional. The actual guest retains one vCPU and 2 GiB RAM. Use Azure Run Command for controller access; no inbound SSH, RDP, VNC or web endpoint is opened.

See the [user guide](https://github.com/Thox-ai/thoxos-azure/blob/v0.1.0/docs/USER_GUIDE.md) for setup, current-boot verification, deallocation/start and failure recovery. A failed managed-verification resource can leave active billable resources. Preserve the error and logs before retrying.

## Acceptance limits

Source validation covers resource generation, artifact pins, process/receipt boundaries, deployment guards and truthful acceptance parsing. Actual Azure deployment and real-kernel execution were not available in the source-preparation session. The template requires those checks when deployed in the user's account.

The [validation record](https://github.com/Thox-ai/thoxos-azure/blob/v0.1.0/docs/VALIDATION.json) states the executed commands, test counts, source hashes and unrun stages. Remote CI is a separate result tied to the published source commit. Deterministic release archives include an internal `SHA256SUMS` inventory and an adjacent checksum for the ZIP; follow the [download verification commands](https://github.com/Thox-ai/thoxos-azure/blob/v0.1.0/README.md#download-and-verify-a-release) before using a downloaded package. The Actions publication receipt records the exact source commit, version, template hash and release-asset hashes after remote readback.

This environment runs an actual independent ThoxInux kernel in QEMU on Ubuntu. It does not establish native Hyper-V boot, a complete graphical ThoxOS desktop, arbitrary Linux workloads, physical hardware acceptance or production readiness. Proprietary applications and firmware are not included.

The deployment package is v0.1.0; the pinned guest release remains v0.9.1. Host imageVersion defaults to regional latest and is recorded in outputs. There is no automatic shutdown schedule. Azure compute, disk, public IPv4 and applicable transfer costs apply.

## Upgrade and recovery limits

This initial deployment package does not provide an in-place kernel upgrade or automatic backup. Validate a separate environment with a newly pinned release before retiring an existing one. Persistent guest state and boot evidence should be preserved before rebuilding. Issued tags and assets are never overwritten. A later `main` commit requires a new deployment-package version before its release job can succeed, even when the source change only updates documentation.

Host Secure Boot/vTPM are separate from the guest's unsigned OVMF boot. The guest uses QEMU TCG; no nested KVM performance or Microsoft qualification of the independent kernel is claimed.
