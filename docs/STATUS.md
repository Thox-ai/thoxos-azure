# Validation and publication status

Record date: November 2, 2026, UTC. This is the source-preparation snapshot before public source/release verification, not a live service-status endpoint. The target repository's existence and the publication access boundary were checked through GitHub.

| Stage | State at preparation |
| --- | --- |
| Canonical ThoxInux v0.9.1 release metadata and source pins | Verified through GitHub |
| Local deployment repository and end-to-end resource definitions | Prepared |
| Offline source, template, privilege-boundary and acceptance checks | See `VALIDATION.json` for actual results |
| Public GitHub repository | Verified: `Thox-ai/thoxos-azure`, default branch `main`; source was not populated at the publication attempt |
| Repository write access through the connected integration | Blocked: GitHub returned HTTP 403, `Resource not accessible by integration`; no source file was created |
| Source push, formal GitHub Release and remote CI | Pending authorized repository/workflow write access and verified publication |
| Public raw-template URL and deployment-button execution | Not verified; publication required |
| Azure server validation and what-if | Not run |
| Azure resource provisioning | Not run |
| Actual kernel boot on Azure and host-restart acceptance | Not run |

The deployment repository is [Thox-ai/thoxos-azure](https://github.com/Thox-ai/thoxos-azure). It exists publicly; the remaining publication requirement is write access for the integration or authorized publisher. Repository visibility and reported user permissions do not establish an integration's access to repository contents or workflows. The rejected write did not publish source, a tag or a release.

Once authorized source publication succeeds, the included workflow validates a canonical `main` push and then publishes an engineering prerelease at the exact validated commit. Review the [workflow](https://github.com/Thox-ai/thoxos-azure/actions/workflows/validate.yml), its publication receipt and [GitHub Releases](https://github.com/Thox-ai/thoxos-azure/releases) for completion after this snapshot. The publication process requires exact-commit verification and anonymous raw-template readback before reporting a working public button. A local repository, correctly formatted URL or release archive alone does not establish those remote states.

No Azure resources or paid infrastructure were created during preparation. Azure subscription access and live acceptance remain separate from GitHub publication.

## Remaining acceptance evidence

Public source, CI and a deployment release require their own remote records. Cloud acceptance then requires ARM server validation, subscription quota/policy acceptance, successful host bootstrap, the pinned real kernel's current-boot evidence, and a repeated check after a host restart. Record failures and partially created resources explicitly. Do not convert unit-test results or inherited upstream evidence into a new Azure acceptance claim.
