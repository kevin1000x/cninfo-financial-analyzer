"""Export the pinned FinAudit runtime from Git objects without copying local work.

The upstream Dockerfile determines the runtime directories. The destination
must not exist: this command never removes or overwrites an existing directory.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"src", "metrics", "data/extracted", "eval/frozen-01/fixtures"}
REQUIRED = ("src/service/api.py", "src/semantic_layer/resolve.py", "metrics/_flags.yaml")


def git(source: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(source), *args])


def prepare(source: Path, destination: Path, commit: str) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new empty destination path.")
    if destination == source or destination in source.parents:
        raise ValueError("Destination must not contain the source repository.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full pinned commit SHA is required.")
    resolved = git(source, "rev-parse", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise ValueError("Source commit does not match release lock.")

    dockerfile = git(source, "show", commit + ":Dockerfile").decode()
    targets = []
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            parts = line.split()
            if len(parts) != 3 or parts[1].rstrip("/") not in ALLOWED:
                raise ValueError("Upstream runtime COPY list changed; review before exporting.")
            targets.append(parts[1].rstrip("/"))
    if set(targets) != ALLOWED:
        raise ValueError("Upstream runtime COPY list does not match the reviewed scope.")

    # LICENSE accompanies redistributed runtime source; raw PDFs and workshop
    # records are deliberately outside the upstream runtime COPY list.
    archive = git(source, "archive", "--format=tar", commit, *targets, "LICENSE")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".finaudit-stage-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "payload"
        stage.mkdir()
        hashes = {}
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe archive path.")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("Links and special files are not allowed in runtime payloads.")
                handle = tar.extractfile(member)
                if handle is None:
                    raise ValueError("Missing archive content.")
                data = handle.read()
                output = stage / path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                hashes[str(path)] = hashlib.sha256(data).hexdigest()
        if any(not (stage / item).is_file() for item in REQUIRED):
            raise ValueError("FinAudit runtime shape is incomplete.")
        if not list((stage / "data/extracted").glob("*.yaml")):
            raise ValueError("FinAudit coverage data is missing.")
        manifest = {"commit": commit, "files": hashes}
        (stage / "payload-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # mkdir provides a non-overwriting destination claim, even if another
        # invocation raced the initial existence check.
        destination.mkdir()
        for item in stage.iterdir():
            item.rename(destination / item.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="FinAudit Git checkout containing the pinned commit")
    parser.add_argument("--output", type=Path, default=ROOT / "build/finaudit")
    args = parser.parse_args()
    lock = json.loads((ROOT / "release-lock.json").read_text())
    try:
        manifest = prepare(args.source, args.output, lock["finaudit"]["commit"])
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Unable to prepare runtime: {error}\n")
    print(f"Prepared {len(manifest['files'])} tracked files at {args.output}")
    print(f"FinAudit commit: {manifest['commit']}")


if __name__ == "__main__":
    main()
