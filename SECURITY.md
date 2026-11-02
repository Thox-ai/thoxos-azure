# Security

This repository provides an **engineering-preview deployment**, not a production security certification. The package and the ThoxInux kernel are separate versioned components. Check the README and release notes for their exact versions and observed validation.

## Intended boundaries

- The Azure Ubuntu host supplies cloud drivers and the VM agent. The independent ThoxInux kernel runs in a nonroot QEMU TCG process.
- The NSG denies all inbound connections. A Standard public IPv4 address provides explicit outbound access; it is not an exposed guest console.
- QMP and guest command interfaces use host-local UNIX sockets. No VNC, RDP or public application service is installed.
- The bootstrap uses HTTPS, fixed artifact hashes, a complete checksum inventory and pinned source files before launching the guest.
- Host Secure Boot/vTPM do not imply guest Secure Boot. The released guest EFI runs with guest Secure Boot disabled.
- The template contains no private SSH key or cloud credential. Operators must provide only a public key to the deployment form.

The guest has outbound networking through QEMU's user-mode network. The host downloads Ubuntu packages and public GitHub release assets. Do not describe this Azure environment as fully offline or as keeping all traffic on a physical local device.

## Operator responsibilities

Use a new dedicated resource group and limit Azure permissions to the operators who need them. Azure Run Command is privileged host administration; control-plane access must be protected even though inbound ports are closed. Apply your organization's access, patching, backup and retention requirements.

Do not treat the engineering guest as a sufficient boundary for hostile workloads or sensitive production data. Review logs before sharing them. They can include guest command output, resource identifiers and workload content. No automatic backup, secret-management integration or incident-response service is supplied.

If a digest check fails, retain the error and investigate the artifact's provenance. Never change the expected digest solely to accept a downloaded payload. Preserve the failed deployment and its evidence before recovery; a failed verification step can leave active billable resources.

## Report a vulnerability

Use the repository's **Security → Report a vulnerability** feature when it is enabled. If a private-reporting channel is not available, open an issue requesting a private contact **without vulnerability details**, credentials or sensitive logs. Do not assume private reporting has been enabled merely because this document exists.

Include the deployment-package version/source commit, kernel release and hash, affected component, concise reproduction, expected versus observed behavior, and impact. Send sensitive evidence only through the confirmed private channel. There is no guaranteed response time or security-support SLA for this preview.

For an active compromise, restrict the affected Azure identity or VM using your incident process, preserve relevant evidence, and assess the environment before restarting or reusing it. This project does not automatically revoke credentials or remediate compromised resources.
