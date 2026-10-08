"""Pinning and non-destructive filesystem contracts for release assembly."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "prepare_finaudit", Path(__file__).parents[1] / "scripts/prepare_finaudit.py"
)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

verify_spec = importlib.util.spec_from_file_location(
    "verify_finaudit_payload", Path(__file__).parents[1] / "deploy/verify_finaudit_payload.py"
)
assert verify_spec and verify_spec.loader
verifier = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verifier)


def run_git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    run_git(root, "init", "-q")
    for path in builder.REQUIRED + ("data/extracted/company.yaml", "eval/frozen-01/fixtures/demo.yaml", "LICENSE"):
        item = root / path
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("reviewed content\n")
    (root / "Dockerfile").write_text("\n".join(f"COPY {path}/ /app/{path}/" for path in sorted(builder.ALLOWED)))
    run_git(root, "add", ".")
    run_git(root, "-c", "user.name=Payload Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture")
    return root, run_git(root, "rev-parse", "HEAD")


def test_exports_pinned_objects_not_modified_or_private_files(source, tmp_path):
    root, commit = source
    (root / "src/service/api.py").write_text("uncommitted edit")
    (root / "src/private.env").write_text("must not be copied")
    destination = tmp_path / "output"
    manifest = builder.prepare(root, destination, commit)
    assert (destination / "src/service/api.py").read_text() == "reviewed content\n"
    assert not (destination / "src/private.env").exists()
    assert manifest["commit"] == commit
    assert (destination / "payload-manifest.json").is_file()


def test_existing_destination_is_never_removed(source, tmp_path):
    root, commit = source
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("user work")
    with pytest.raises(ValueError, match="already exists"):
        builder.prepare(root, destination, commit)
    assert marker.read_text() == "user work"


def test_refuses_unreviewed_copy_scope(source, tmp_path):
    root, _ = source
    with (root / "Dockerfile").open("a") as handle:
        handle.write("\nCOPY data/raw/ /app/data/raw/\n")
    run_git(root, "add", "Dockerfile")
    run_git(root, "-c", "user.name=Payload Test", "-c", "user.email=test@example.com", "commit", "-qm", "scope change")
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="COPY list changed"):
        builder.prepare(root, destination, run_git(root, "rev-parse", "HEAD"))
    assert not destination.exists()


def test_build_guard_rejects_changed_file(source, tmp_path):
    root, commit = source
    destination = tmp_path / "output"
    builder.prepare(root, destination, commit)
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"finaudit": {"commit": commit}}))
    verifier.verify(destination, lock)
    (destination / "src/service/api.py").write_text("edited after export")
    with pytest.raises(ValueError, match="changed after export"):
        verifier.verify(destination, lock)


def test_build_guard_rejects_extra_file(source, tmp_path):
    root, commit = source
    destination = tmp_path / "output"
    builder.prepare(root, destination, commit)
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"finaudit": {"commit": commit}}))
    (destination / "extra.env").write_text("must not be in image")
    with pytest.raises(ValueError, match="file set differs"):
        verifier.verify(destination, lock)
