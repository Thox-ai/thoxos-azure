# ThoxOS Azure documentation

This repository provisions an Ubuntu Azure host and runs the actual ThoxInux v0.9.1 engineering-preview kernel in QEMU. Deployment-package v0.1.0 and kernel v0.9.1 are separate releases. Begin with the [repository README](../README.md) for the Deploy to Azure button, resource defaults and publication status.

| Goal | Guide |
| --- | --- |
| Deploy, use the controller, stop/start and recover | [User guide](USER_GUIDE.md) |
| Understand the host, guest, network and verification boundaries | [Architecture](ARCHITECTURE.md) |
| Validate source, generate resources and publish a versioned release | [Development and publication](DEVELOPMENT.md) |
| Review what has actually been prepared, published or executed | [Status](STATUS.md) and [validation evidence](VALIDATION.json) |
| Check the current package's contents and limitations | [Release notes](RELEASE_NOTES.md) |
| Download and check the public archive and file inventory | [Release verification](../README.md#download-and-verify-a-release) |
| Propose a change or report a vulnerability | [Contributing](../CONTRIBUTING.md) and [security](../SECURITY.md) |

## Evidence boundaries

Local source tests, remote GitHub publication, Azure provisioning, current guest boot and host-restart acceptance are distinct results. The validation record identifies executed source checks. A successful Actions publication receipt identifies the exact released commit, public template and downloaded asset hashes. Actual cloud and guest acceptance require the intended Azure subscription and fresh runtime evidence.

The template's managed verification resource checks the current real guest before ARM deployment can succeed. A failed check can still leave billable resources; use the user guide's recovery instructions. No full graphical desktop, arbitrary Linux application compatibility, native Azure kernel boot or production certification is implied.
