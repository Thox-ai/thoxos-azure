# Contributing

Contributions should make the ThoxOS Azure deployment easier to operate and verify while preserving the exact release identity and isolation boundaries.

## Development flow

1. Read the [README](README.md), [architecture](docs/ARCHITECTURE.md) and [security guidance](SECURITY.md).
2. Work on a focused branch. Preserve unrelated changes and existing release tags.
3. Update the source generator when changing generated deployment resources. Regenerate the checked-in template using the documented build command.
4. Run the repository's validation command and relevant tests. Record which checks were offline, simulated, or executed on Azure.
5. Update affected user documentation, parameter descriptions, limitations and recovery instructions in the same change.
6. Submit a pull request describing the problem, resulting behavior, tests and remaining limits. Do not claim an Azure deployment or guest boot that was not observed.

Run the complete source-validation gate from the repository root:

```bash
python3 scripts/validate.py
```

Focused local checks are:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile deploy.py
bash -n guest-bootstrap.sh
```

See the [development guide](docs/DEVELOPMENT.md) for template-generation and publication commands. Tests using a stub guest exercise protocol handling; they are not kernel acceptance tests.

Review the rendered README and affected guides after each source change, including documentation-only changes. Check commands, parameter names, links, default values and evidence limits against the committed implementation. CI badges must remain dynamic; never replace a failing or missing run with a static success badge.

## Change boundaries

- Keep release artifact and source hashes explicit. A new kernel release requires provenance verification and fresh boot evidence.
- Preserve default-deny inbound networking, nonroot guest execution, artifact checks and current-boot acceptance. Do not remove a failed check to obtain a passing result.
- Keep credentials, private keys, subscription-specific private data and unredacted logs out of commits, issues and artifacts.
- Distinguish deployment-package versions from ThoxInux kernel versions.
- Use an isolated, authorized Azure test resource group for live testing. Live tests create costs and must not target another person's existing environment.
- Preserve upstream license and notice files. Include attribution for reused source and contributions.

For a release, retain the exact source revision, dependency/runtime versions, artifact hashes, actual test outcomes and explicit unrun stages. Public deployment links must resolve to the intended published template. A successful static check cannot establish subscription quota, Azure policy acceptance or a functioning guest.

The publication helper is scoped to initial publication from one fresh snapshot and resuming that exact state. Subsequent development releases use the reviewed `main` workflow: successful canonical `main` pushes run validation, then the release job for the exact commit. Once a version has been issued, every subsequent `main` commit must include a new `deploymentVersion`, updated button, release notes and affected version references. This includes documentation-only changes. Preserve issued tags and release assets; the release job rejects reuse of a version that belongs to another commit. See the [release process](docs/DEVELOPMENT.md#automatic-release-from-main).

Report security-sensitive findings using [SECURITY.md](SECURITY.md) rather than including exploit details or secrets in a public issue.
