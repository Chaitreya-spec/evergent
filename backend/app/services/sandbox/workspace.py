"""Turning stored artifacts into files on disk, and back out as an archive."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath


def safe_relative(path: str) -> PurePosixPath:
    """A workspace path that cannot escape the workspace.

    Paths are already checked when artifacts are written; this is the second
    lock, because here they become real filesystem paths.
    """
    candidate = PurePosixPath(path.strip().lstrip("/"))
    if not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"Unsafe workspace path: {path!r}")
    return candidate


def materialize(files: Iterable[tuple[str, str]], root: Path) -> list[str]:
    """Write (path, content) pairs under root. Returns the paths written."""
    written: list[str] = []
    for path, content in files:
        relative = safe_relative(path)
        target = root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(relative))
    return written


def build_archive(files: Iterable[tuple[str, str]], *, folder: str) -> bytes:
    """A zip of the workspace, everything under one top-level folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files:
            archive.writestr(f"{folder}/{safe_relative(path)}", content)
    return buffer.getvalue()
