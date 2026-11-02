# Development and publication

## Local requirements

Use Python 3.10 or later and Bash. The source checks use the Python standard library. No Azure identity, cloud resources, Docker installation or real kernel guest is needed for offline checks.

```bash
python3 scripts/validate.py
```

The checks parse source, compile embedded Python, validate bootstrap shell syntax, compare the generated ARM template byte for byte, verify the deployment button, and run focused tests. Azure calls and publication API calls in unit tests are mocked. Passing these tests is not evidence of an Azure deployment or kernel boot.

## Source map

| File | Responsibility |
| --- | --- |
| `guest-bootstrap.sh` | Install exact kernel assets and supervise the actual guest |
| `scripts/generate_template.py` | Generate the portal template and required acceptance child |
| `azuredeploy.json` | Standalone ARM template loaded by the deployment button |
| `deploy.py` | Subscription-explicit CLI preparation, validation, deployment and status |
| `deployment-config.json` | Deployment repository and package version used by the button |
| `scripts/links.py` | Generate/check the documented versioned Azure deployment URL |
| `scripts/publish.py` | Create/push the public deployment repo and verify its release |
| `scripts/release_from_actions.py` | Publish and verify the exact validated canonical `main` commit |
| `scripts/package_release.py` | Package an explicit public-file allowlist |
| `.github/workflows/validate.yml` | Source validation, then automatic publication for canonical `main` pushes |

Both deployment paths use the same bootstrap. The portal path gates ARM completion with a managed Run Command child resource; the CLI path performs Azure server validation/what-if and a separate post-provisioning status check. The CLI's template builder and the portal generator are each covered by focused tests.

After modifying the bootstrap or portal generator:

```bash
python3 scripts/generate_template.py
python3 scripts/validate.py
```

Do not hand-edit the generated ARM JSON. Review any metadata or behavior changes against README, the user guide and the architecture guide.

## Versioning and button

`v0.1.0` versions this deployment package; it does not rename kernel v0.9.1. The button points to the public raw ARM file at the package tag. A Git tag is a version reference, not a claim that GitHub immutable releases are enabled. Preserve issued tags. A future source change should use a new reviewed deployment version.

The supported URL format is:

```text
https://portal.azure.com/#create/Microsoft.Template/uri/<URL-encoded-public-raw-ARM-URL>
```

No undocumented custom form suffix is required. Azure cannot fetch an ordinary private GitHub raw URL using the viewer's GitHub login. Keep this integration source public if using the standard button. This does not authorize changing any existing private repository's visibility.

## Publication

### Automatic release from main

The public target repository is `Thox-ai/thoxos-azure`. Publish source through an authorized maintainer's reviewed push or pull-request merge into `main`. The workflow in `.github/workflows/validate.yml` runs the source-validation job first, with read-only content permission. For a push to this canonical repository's `main`, successful validation enables the release job with repository-scoped `contents: write` permission. Pull requests, forks and manual dispatch do not publish releases.

The release job checks out the exact triggering commit and invokes `scripts/release_from_actions.py`. The script validates the Actions repository, event, branch and expected commit; it also confirms that public `main` still identifies that source before publication mutations. It is not a standalone local publication command.

The automatic release performs these stages:

1. Verify the clean source checkout, active public repository and configured version.
2. Build the deterministic allowlisted ZIP and adjacent checksum.
3. Create the version tag at the validated commit, or verify that an existing tag already resolves to that same commit.
4. Create an engineering-prerelease draft, or resume an exactly matching draft by adding only missing assets.
5. Download the assets and compare their bytes; fetch the tagged ARM template anonymously and compare its bytes.
6. Publish the verified draft, then read back the published metadata, tag, assets and raw template again.
7. Write `dist/release-publication-receipt.json` and include its contents in the job summary.

The receipt records the exact source commit, package version, release URL, template digest, asset hashes and result. Azure deployment and real-kernel boot remain explicitly unrun. The workflow preserves available archives, checksums and the receipt in an artifact named `release-publication-<commit>` for 30 days. A failed receipt records the error; an uploaded artifact alone is not evidence that publication succeeded.

The release job never moves an issued tag, replaces an asset, edits an issued release, or deploys Azure. A rerun of the original push can verify an unchanged published release or resume an exactly matching draft. If metadata, asset bytes or the tag's source commit differ, inspect the failure; do not delete or overwrite remote state to make the job pass. If `main` has advanced, the older job refuses further publication mutations.

### Prepare each subsequent version

After a version has been issued, **every new commit on canonical `main` requires a new deployment-package version**, including documentation-only changes. Use a reviewed branch to update `deployment-config.json`'s `deploymentVersion`, `docs/RELEASE_NOTES.md`, the README package badge and all affected version references. Keep the kernel release unchanged unless the guest itself is intentionally being upgraded and verified.

Update the button and run the source gate before merge:

```bash
python3 scripts/links.py
python3 scripts/validate.py
```

Regenerate `azuredeploy.json` as well when its generator or bootstrap changes. Do not reuse the old version: a new commit under an existing tag is rejected, even if the change appears small. The package version accepts `vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-rc.NUMBER`; this workflow publishes engineering prereleases.

### Initial publication helper

For initial publication from one fresh source snapshot, the CLI helper can populate an empty or exactly matching public repository. Use GitHub CLI authenticated normally to an identity authorized to write the target repository and workflow files:

```bash
gh auth login
python3 scripts/publish.py --public --repository Thox-ai/thoxos-azure
```

The helper performs these stages:

1. Confirm the existing GitHub CLI identity and run offline validation.
2. Stage only explicitly allowed source paths and create a local commit if needed.
3. Verify an existing empty/matching public repository, or create a missing public repository when authorized.
4. Push `main` without force and read back its exact commit SHA.
5. Wait for the exact commit's workflow, including its canonical automatic release job.
6. Verify or publish the same version tag and engineering prerelease without replacing issued tags or assets; compare the public template and downloaded release assets with local bytes.
7. Record the repository, commit, release, CI run, template digest and button URL under ignored `.publication-receipts/`.

A failure may leave completed earlier stages in place. Preserve that checkout and inspect the error. A repeated attempt accepts only matching source state; it does not delete repositories, change private visibility, rewrite history or overwrite unrelated tags/releases.

Initial publication requires a single fresh snapshot commit. The helper refuses a checkout with additional history because deleted private material could otherwise be pushed. Use the reviewed source archive in a fresh directory for a new repository. The helper is for initial publication and an exact-state resume; subsequent development releases use the reviewed `main` workflow above.

The helper accepts `--repository OWNER/NAME` for an independently authorized initial target, updating configuration and the button before publication. The automatic Actions release is intentionally restricted to `Thox-ai/thoxos-azure`; changing a repository name does not authorize that release job elsewhere. Review repository-specific documentation and badges for any separately maintained copy.

### Required access and publication status

Creating a missing repository requires the owner's permission. Writing contents and adding workflows require actual token or app access to those operations; a public repository or reported user role does not supply integration access. GitHub Actions must also permit the release job's scoped content-write token. An HTTP 403 such as `Resource not accessible by integration` requires correcting the repository authorization, not removing checks or changing visibility. See [STATUS.md](STATUS.md) for the recorded publication state.

## Public release archive

```bash
python3 scripts/package_release.py
```

The archive includes only the files listed in that script. It excludes `AGENTS.md`, internal handoff material, credentials, local plans, private keys, repository internals and publication receipts. The ZIP contains its own file-checksum inventory, and an adjacent checksum covers the ZIP bytes.

After a release is published, use the README's [download and verification commands](../README.md#download-and-verify-a-release) to check both the archive and extracted inventory. Keep the exact release commit and workflow receipt with any operational evidence you retain.

## Live acceptance still required

Actual Azure validation, quota/policy checks, host provisioning, artifact download, guest boot and later host-restart acceptance require the intended Azure subscription. Record real outputs separately from source validation. A controlled stub process may test console handling, but it is never real-kernel evidence.

Sources: [Microsoft deployment-button guidance](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-to-azure-button), [managed Run Command API](https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/2024-11-01/virtualmachines/runcommands), and [GitHub repository creation CLI](https://cli.github.com/manual/gh_repo_create).
