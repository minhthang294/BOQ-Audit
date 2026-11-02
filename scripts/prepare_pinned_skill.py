"""Pack or extract the exact BOQ Audit skill pinned by the backend manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "backend/app/boq_audit_skill_manifest.json"
ARCHIVE = ROOT / "skill-bundle/boq-audit-v6.4.zip"
DEFAULT_SKILL = ROOT / ".orchestration/skill"


def pinned_files() -> dict[str, str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = {row["path"]: row["sha256"] for row in manifest["files"]}
    if len(files) != len(manifest["files"]) or any(
        path.startswith("/") or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/"))
        for path in files
    ):
        raise ValueError("unsafe or duplicate skill manifest path")
    canonical = json.dumps(
        [{"path": path, "sha256": files[path]} for path in sorted(files)],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != manifest["digest"]:
        raise ValueError("skill manifest digest mismatch")
    return files


def verify_tree(root: Path, files: dict[str, str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("skill root must be a real directory")
    paths = [path for path in root.rglob("*") if "__pycache__" not in path.parts and path.suffix != ".pyc"]
    actual = {path.relative_to(root).as_posix() for path in paths if path.is_file()}
    if actual != set(files) or any(path.is_symlink() for path in paths):
        raise ValueError("skill file set differs from the pin")
    for name, digest in files.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"skill hash mismatch: {name}")


def pack(source: Path, files: dict[str, str]) -> None:
    verify_tree(source, files)
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=ARCHIVE.parent, delete=False) as staged:
        temporary = Path(staged.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, (source / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        verify_archive(files, temporary)
        os.replace(temporary, ARCHIVE)
    finally:
        temporary.unlink(missing_ok=True)


def verify_archive(files: dict[str, str], path: Path = ARCHIVE) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != len(files) or {entry.filename for entry in entries} != set(files):
            raise ValueError("archive file set differs from the pin")
        for entry in entries:
            if stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG or entry.file_size > 16 * 1024 * 1024:
                raise ValueError(f"unsafe skill archive entry: {entry.filename}")
            with archive.open(entry) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != files[entry.filename]:
                raise ValueError(f"archive hash mismatch: {entry.filename}")


def extract(destination: Path, files: dict[str, str]) -> None:
    verify_archive(files)
    if destination.exists():
        verify_tree(destination, files)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".skill-stage-", dir=destination.parent))
    try:
        with zipfile.ZipFile(ARCHIVE) as archive:
            for name in sorted(files):
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        verify_tree(temporary, files)
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pack", "verify", "extract", "verify-dir"))
    parser.add_argument("--destination", type=Path, default=DEFAULT_SKILL)
    args = parser.parse_args(argv)
    pinned = pinned_files()
    if args.action == "pack":
        pack(args.destination, pinned)
    elif args.action == "verify":
        verify_archive(pinned)
    elif args.action == "verify-dir":
        verify_tree(args.destination, pinned)
    else:
        extract(args.destination, pinned)
    print(f"BOQ Audit skill {args.action}: {len(pinned)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
