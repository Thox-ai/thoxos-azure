# AGENTS.md — ThoxOS on Azure

Guidance for any agent or contributor working in this repository.
This file is the agent entry point and the standing handoff record.
It is tracked in the repository but deliberately excluded from the
release archive, which contains only the allowlisted deployment files.

## What this repository is

ThoxOS on Azure deploys the ThoxInux v0.9.1 open source engineering
release on one Azure VM: it provisions an Ubuntu host, boots the actual
independent Rust kernel in QEMU, and verifies the running guest before
the deployment can succeed. It is an engineering preview. See
`README.md` for the product description, `docs/ARCHITECTURE.md` for the
host, guest and network boundaries, and `docs/STATUS.md` for the
preparation snapshot and claim limits.

## Current state and handoff

- Target repository: https://github.com/Thox-ai/thoxos-azure (public).
- Deployment package version: v0.1.0 (see `deployment-config.json`);
  pinned kernel: ThoxInux v0.9.1 from Thox-ai/thoxinux.
- Initial publication is a single snapshot commit of the reviewed
  source, published with `scripts/publish.py` through an authorized
  GitHub CLI account. The workflow in `.github/workflows/validate.yml`
  validates the exact commit, and its release job publishes or verifies
  the v0.1.0 engineering prerelease at that same commit.
- The Deploy to Azure button targets the versioned raw template
  `v0.1.0/azuredeploy.json`. It is usable only once the tag and release
  are public and the anonymous raw bytes match `azuredeploy.json`.
- Live state is read from GitHub (Actions runs, Releases, the raw
  template URL), never from this file. `docs/STATUS.md` is a dated
  preparation snapshot, not a live status endpoint.

## What is proven, and what is not

- Proven by the offline suite: 67 tests covering deployment guards,
  template and privilege boundaries, publication safeguards and release
  automation, plus generated-template consistency and documentation
  link checks (`python3 scripts/validate.py`).
- Not proven: Azure provisioning, a real guest boot on Azure, and
  host-restart acceptance have never been run. Do not convert unit
  tests or upstream kernel evidence into an Azure acceptance claim.

## Working rules

- Verify before claiming. A workflow file is not a CI pass, a local
  test run is not an Azure deployment, and a version string is not a
  release. Claims name the evidence they rest on.
- Run `python3 scripts/validate.py` and the full test suite before
  pushing. The checked-in `azuredeploy.json` must exactly bind the
  bootstrap; regenerate it with `scripts/generate_template.py` when the
  bootstrap changes.
- Issued tags and release assets are never moved, replaced or
  overwritten. The release automation binds the v0.1.0 tag to its exact
  source commit, so any later source change requires a deployment
  version bump in `deployment-config.json` with matching release notes.
  A commit that does not bump the version will fail the release job by
  design; do not land one casually.
- Never fabricate evidence. No simulated Azure portal screenshots and
  no invented deployment records. Diagrams are labelled as diagrams.
- Keep the public-package boundary: the release archive contains only
  the allowlisted files in `scripts/package_release.py`. Credentials,
  subscription details, internal assignments and private review records
  never enter this repository or its archives.

## Next steps, in order

1. Verify the publication sequence completed: Actions validation run
   successful for the snapshot commit, v0.1.0 prerelease published with
   its source ZIP and checksum, downloaded assets byte-identical, and
   the anonymous raw template matching the committed template.
2. Run the first real Azure deployment in a new dedicated resource
   group on the intended subscription, then record the deployment
   result, the resolved host image, and the guest verification output,
   including a repeated check after a host restart.
3. If the GitHub App route is wanted for future automation, install or
   configure the app for the Thox-ai organization with thoxos-azure
   selected. CLI publication does not depend on it.

## Recovery

- If the release job fails, inspect its receipt and the remote tag and
  release state before any retry. Rerunning the workflow for the same
  commit resumes safely; a different commit requires a version bump.
- If the remote gains unexpected branches or commits, inspect and
  integrate that work; never force-push over it.
