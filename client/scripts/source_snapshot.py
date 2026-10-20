"""Deterministic SHA-256 inventory of source trees for owner-copy builds.

Public release archives have no .git directory, so this walks explicitly
provided roots directly instead of asking Git. Links, junctions and other
reparse points are rejected rather than followed, so the report only covers
files that physically live under each root.
"""

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path

__all__ = ["SourceSnapshotError", "snapshot_sources"]

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CHUNK_SIZE = 1024 * 1024
EXCLUDED_NAMES = frozenset({".git", "__pycache__"})
EXCLUDED_SUFFIXES = (".pyc",)
FORBIDDEN_LABEL_CHARS = frozenset("/\\:\0")


class SourceSnapshotError(ValueError):
    """Raised when a trustworthy snapshot cannot be produced."""


def snapshot_sources(roots: Mapping[str, Path]) -> dict[str, str]:
    """Return {"label/posix/relative/path": sha256_hex} sorted by key."""
    if not isinstance(roots, Mapping) or not roots:
        raise SourceSnapshotError("at least one source root is required")
    report: dict[str, str] = {}
    for label, root in _check_roots(roots):
        _walk(label, root, report)
    return dict(sorted(report.items()))


def _check_roots(roots: Mapping[str, Path]) -> list[tuple[str, str]]:
    labels: dict[str, str] = {}
    checked: list[tuple[str, str]] = []
    for label, root in roots.items():
        _check_label(label)
        folded = label.casefold()
        if folded in labels:
            raise SourceSnapshotError(
                f"labels {labels[folded]!r} and {label!r} differ only by case")
        labels[folded] = label
        checked.append((label, _check_root(label, root)))

    keys = [(label, tuple(part.casefold() for part in Path(path).parts))
            for label, path in checked]
    for index, (label_a, parts_a) in enumerate(keys):
        for label_b, parts_b in keys[index + 1:]:
            shorter = min(len(parts_a), len(parts_b))
            if parts_a[:shorter] == parts_b[:shorter]:
                raise SourceSnapshotError(
                    f"source roots {label_a!r} and {label_b!r} overlap")
    return checked


def _check_label(label: object) -> None:
    if not isinstance(label, str) or label in ("", ".", ".."):
        raise SourceSnapshotError(f"invalid source label: {label!r}")
    if FORBIDDEN_LABEL_CHARS & set(label):
        raise SourceSnapshotError(
            f"source label contains a path separator or colon: {label!r}")


def _check_root(label: str, root: object) -> str:
    try:
        text = os.fspath(root)
    except TypeError:
        raise SourceSnapshotError(
            f"source root for {label!r} is not a path") from None
    if not isinstance(text, str) or not text:
        raise SourceSnapshotError(f"source root for {label!r} is not a path")

    # Normalise lexically only; resolving would silently follow links.
    absolute = os.path.abspath(text)
    parts = Path(absolute).parts
    current = parts[0]
    info = _lstat(current)
    for part in parts[1:]:
        current = os.path.join(current, part)
        info = _lstat(current)
        if _is_link(info):
            raise SourceSnapshotError(
                f"source root for {label!r} passes through a link or "
                f"reparse point: {current}")
    if not stat.S_ISDIR(info.st_mode):
        raise SourceSnapshotError(
            f"source root for {label!r} is not a directory: {absolute}")
    return absolute


def _walk(label: str, root: str, report: dict[str, str]) -> None:
    seen: dict[str, str] = {}
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        info = _lstat(directory)
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise SourceSnapshotError(f"source directory changed before traversal: {directory}")
        try:
            with os.scandir(directory) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError as exc:
            raise SourceSnapshotError(
                f"cannot list {directory}: {exc.strerror or exc}") from exc

        for name in names:
            if _excluded(name):
                continue
            relative = prefix + name
            if "\\" in name:
                raise SourceSnapshotError(
                    f"non-portable name in {label!r}: {relative!r}")
            folded = relative.casefold()
            if folded in seen:
                raise SourceSnapshotError(
                    f"case-insensitive name collision in {label!r}: "
                    f"{seen[folded]!r} and {relative!r}")
            seen[folded] = relative

            path = os.path.join(directory, name)
            info = _lstat(path)
            if _is_link(info):
                raise SourceSnapshotError(
                    f"link or reparse point in {label!r}: {relative!r}")
            if stat.S_ISDIR(info.st_mode):
                pending.append((path, relative + "/"))
            elif stat.S_ISREG(info.st_mode):
                report[f"{label}/{relative}"] = _hash_file(
                    path, info, f"{label}/{relative}")
            else:
                raise SourceSnapshotError(
                    f"special file in {label!r}: {relative!r}")


def _excluded(name: str) -> bool:
    folded = name.casefold()
    return folded in EXCLUDED_NAMES or folded.endswith(EXCLUDED_SUFFIXES)


def _hash_file(path: str, before: os.stat_result, key: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SourceSnapshotError(
            f"cannot open {key!r}: {exc.strerror or exc}") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise SourceSnapshotError(f"{key!r} changed before hashing")
        digest = hashlib.sha256()
        while chunk := os.read(fd, CHUNK_SIZE):
            digest.update(chunk)
        after = os.fstat(fd)
    except OSError as exc:
        raise SourceSnapshotError(
            f"cannot read {key!r}: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)

    final = _lstat(path)
    if (_identity(after) != _identity(opened) or _is_link(final)
            or _identity(final) != _identity(opened)):
        raise SourceSnapshotError(f"{key!r} changed while hashing")
    return digest.hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0) or 0
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _lstat(path: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise SourceSnapshotError(
            f"cannot inspect {path}: {exc.strerror or exc}") from exc
