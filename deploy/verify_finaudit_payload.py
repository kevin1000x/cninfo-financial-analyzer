"""Fail the image build if the pinned runtime was edited after export."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def verify(payload: Path, lock: Path) -> None:
    manifest = json.loads((payload / "payload-manifest.json").read_text())
    expected_commit = json.loads(lock.read_text())["finaudit"]["commit"]
    if manifest["commit"] != expected_commit:
        raise ValueError("FinAudit commit differs from release-lock.json")
    actual = {
        p.relative_to(payload).as_posix()
        for p in payload.rglob("*")
        if p.is_file() and p.name != "payload-manifest.json"
        and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    }
    if actual != set(manifest["files"]):
        raise ValueError("FinAudit file set differs from the exported manifest")
    for name, expected_hash in manifest["files"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or (payload / path).is_symlink():
            raise ValueError("Unsafe FinAudit payload path")
        digest = hashlib.sha256((payload / path).read_bytes()).hexdigest()
        if digest != expected_hash:
            raise ValueError(f"FinAudit file changed after export: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("lock", type=Path)
    args = parser.parse_args()
    verify(args.payload, args.lock)
    print("FinAudit payload hashes verified")
