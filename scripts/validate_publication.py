#!/usr/bin/env python3
"""Validate public files and the one-publication-file pull-request boundary."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.publication import (
    PUBLICATION_PATH_RE,
    PublicationValidationError,
    load_publication,
)

Change = tuple[str, str, str | None]
_ARTIFACT_NAME_RE = re.compile(
    r"(?:^|[._-])(final|draft|eval|raw|logs?)(?:\.(?:json|md|txt|csv)|$)", re.I
)
_ARTIFACT_SUFFIXES = {".log", ".jsonl", ".sqlite", ".sqlite3", ".db"}


def validate_changed_paths(changes: list[str] | list[Change], root: Path = ROOT) -> Path | None:
    normalized: list[Change] = []
    for change in changes:
        if isinstance(change, str):
            normalized.append(("M", change, None))
        else:
            status, path, old_path = change
            normalized.append((status, path, old_path))
    publication_changes = [
        change for change in normalized
        if change[1].startswith("publications/")
        or (change[2] is not None and change[2].startswith("publications/"))
    ]
    if not publication_changes:
        return None
    if len(normalized) != 1 or len(publication_changes) != 1:
        raise PublicationValidationError(
            "a content PR must change exactly one publication file and no other committed or working-tree path"
        )
    status, relative, old_path = publication_changes[0]
    if status not in {"A", "M"} or old_path is not None:
        raise PublicationValidationError(
            f"publication deletion, rename, copy, or type change is forbidden (status={status})"
        )
    match = PUBLICATION_PATH_RE.fullmatch(relative)
    if not match:
        raise PublicationValidationError(
            "the only allowed content path is publications/<content_id>/publication.json"
        )
    path = root / relative
    publication = load_publication(path)
    if publication["content_id"] != match.group("content_id"):
        raise PublicationValidationError("content_id must match its directory name")
    return path


def _decode_git_path(raw: bytes) -> str:
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublicationValidationError("git path is not valid UTF-8") from exc
    if any(ord(character) < 32 for character in path):
        raise PublicationValidationError(f"git path contains control characters: {path!r}")
    return path


def _parse_diff(raw: bytes) -> list[Change]:
    changes: list[Change] = []
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    index = 0
    while index < len(fields):
        status = _decode_git_path(fields[index])
        index += 1
        needed = 2 if status.startswith(("R", "C")) else 1
        if index + needed > len(fields):
            raise PublicationValidationError("malformed NUL-delimited git name-status output")
        if needed == 2:
            old_path = _decode_git_path(fields[index])
            path = _decode_git_path(fields[index + 1])
            index += 2
        else:
            path = _decode_git_path(fields[index])
            old_path = None
            index += 1
        changes.append((status, path, old_path))
    return changes


def changed_paths_from_git(base: str, head: str, root: Path = ROOT) -> list[Change]:
    completed = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--find-renames", base, head, "--"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    combined = _parse_diff(completed.stdout)
    status_fields = status.stdout.split(b"\0")
    if status_fields and status_fields[-1] == b"":
        status_fields.pop()
    index = 0
    while index < len(status_fields):
        field = status_fields[index]
        if len(field) < 4 or field[2:3] != b" ":
            raise PublicationValidationError("malformed NUL-delimited git status output")
        code = field[:2].decode("ascii", errors="strict")
        path = _decode_git_path(field[3:])
        index += 1
        old = None
        if "R" in code or "C" in code:
            if index >= len(status_fields):
                raise PublicationValidationError("malformed git rename status output")
            old = _decode_git_path(status_fields[index])
            index += 1
        combined.append((code.strip() or code, path, old))
    return list(dict.fromkeys(combined))


def repository_paths_from_git(root: Path = ROOT) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return [_decode_git_path(raw) for raw in completed.stdout.split(b"\0") if raw]


def validate_repository_paths(paths: list[str]) -> None:
    forbidden = []
    for raw in paths:
        if raw != PurePosixPath(raw).as_posix() or raw.startswith(("/", "../")):
            raise PublicationValidationError(f"repository contains a malformed path: {raw!r}")
        path = PurePosixPath(raw)
        posix = raw
        if posix.startswith("publications/") and PUBLICATION_PATH_RE.fullmatch(posix) is None:
            raise PublicationValidationError(f"unexpected path under publications/: {posix!r}")
        if path.name == ".gitkeep":
            continue
        if posix.startswith(("output/", "inbox/")) or path.suffix.lower() in _ARTIFACT_SUFFIXES or _ARTIFACT_NAME_RE.search(path.name):
            forbidden.append(posix)
    if forbidden:
        raise PublicationValidationError(f"repository contains forbidden generated/raw artifacts: {sorted(forbidden)}")


def validate_all(root: Path = ROOT, repository_paths: list[str] | None = None) -> list[Path]:
    repo_paths = repository_paths if repository_paths is not None else repository_paths_from_git(root)
    validate_repository_paths(repo_paths)
    paths = sorted(root / path for path in repo_paths if PUBLICATION_PATH_RE.fullmatch(Path(path).as_posix()))
    slugs: dict[str, Path] = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        match = PUBLICATION_PATH_RE.fullmatch(relative)
        if not match:
            raise PublicationValidationError(f"invalid publication path: {relative}")
        publication = load_publication(path)
        if publication["content_id"] != match.group("content_id"):
            raise PublicationValidationError(f"content_id does not match path: {relative}")
        slug = publication["slug"]
        if slug in slugs:
            raise PublicationValidationError(
                f"duplicate publication slug {slug!r}: {slugs[slug].relative_to(root)} and {relative}"
            )
        slugs[slug] = path
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="validate public publication files")
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--file", type=Path)
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--changed-files", nargs="+")
    mode.add_argument("--git-diff", nargs=2, metavar=("BASE", "HEAD"))
    args = parser.parse_args()
    root = args.repository_root.resolve()

    try:
        if args.file:
            path = args.file if args.file.is_absolute() else root / args.file
            load_publication(path)
            print(f"[validate-publication] valid: {path.relative_to(root)}")
        elif args.all:
            paths = validate_all(root)
            print(f"[validate-publication] valid publications: {len(paths)}")
        else:
            changes = args.changed_files or changed_paths_from_git(*args.git_diff, root=root)
            validate_all(root)
            publication_path = validate_changed_paths(changes, root=root)
            if publication_path:
                print(f"[validate-publication] valid content PR: {publication_path.relative_to(root)}")
            else:
                print("[validate-publication] code-only PR: no publication payload")
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError, PublicationValidationError) as exc:
        raise SystemExit(f"[validate-publication] blocked: {exc}") from exc


if __name__ == "__main__":
    main()
