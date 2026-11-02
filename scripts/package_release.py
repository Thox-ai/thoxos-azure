#!/usr/bin/env python3
"""Build a deterministic, public deployment source archive from an allowlist."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Public deliverables only. AGENTS.md and private execution records are excluded.
FILES = (
    "README.md", "LICENSE", "NOTICE", "SECURITY.md", "CONTRIBUTING.md",
    "deployment-config.json", "azuredeploy.json", "guest-bootstrap.sh", "deploy.py",
    ".gitignore", ".github/workflows/validate.yml",
    "docs/README.md", "docs/USER_GUIDE.md", "docs/ARCHITECTURE.md", "docs/DEVELOPMENT.md",
    "docs/STATUS.md", "docs/RELEASE_NOTES.md", "docs/VALIDATION.json",
    "scripts/generate_template.py", "scripts/links.py", "scripts/validate.py",
    "scripts/publish.py", "scripts/package_release.py", "scripts/release_from_actions.py",
    "tests/test_deploy.py", "tests/test_template.py", "tests/test_publication.py",
    "tests/test_release_from_actions.py",
)


def package():
    data = json.loads((ROOT / "deployment-config.json").read_text())
    version = data["deploymentVersion"]
    if not isinstance(version, str) or not re.fullmatch(r"v\d+\.\d+\.\d+(?:-rc\.\d+)?", version):
        raise ValueError("Invalid deployment version for archive paths")
    prefix = f"thoxos-azure-{version}"
    out = ROOT / "dist"
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise RuntimeError("Archive output directory must be a regular directory")
    out.mkdir(exist_ok=True)
    archive = out / f"{prefix}.zip"
    checksums = out / f"{prefix}.sha256"
    for target in (archive, checksums):
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError("Archive output must be a regular file: " + target.name)
    manifest = {}
    # Stored entries avoid zlib-version differences between local publishers and
    # Actions. Fix platform metadata too, so identical source yields exact bytes.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as z:
        for name in FILES:
            path = ROOT / name
            root_resolved = ROOT.resolve()
            if root_resolved not in path.resolve().parents:
                raise RuntimeError(f"Source path escapes the repository: {name}")
            local_parts = [path, *[p for p in path.parents if p != ROOT and ROOT in p.parents]]
            if not path.is_file() or any(p.is_symlink() for p in local_parts):
                raise RuntimeError(f"Missing regular public source file: {name}")
            payload = path.read_bytes()
            manifest[name] = hashlib.sha256(payload).hexdigest()
            info = zipfile.ZipInfo(f"{prefix}/{name}", (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, payload)
        info = zipfile.ZipInfo(f"{prefix}/SHA256SUMS", (1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, "".join(f"{sha}  {name}\n" for name, sha in sorted(manifest.items())))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksums.write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
    return archive, checksums


if __name__ == "__main__":
    archive, checksums = package()
    print(archive)
    print(checksums)
