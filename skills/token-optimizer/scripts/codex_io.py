from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write(path: Path, content: str, mode: int = 0o600, *, crlf: bool = False,
                 replace_symlink: bool = False) -> None:
    """Write content to path atomically via tempfile+rename.

    When ``crlf`` is True, ``\\n`` in *content* is converted to ``\\r\\n``
    before writing so that CRLF line endings are preserved through the
    install/uninstall round-trip.

    By default a symlinked *path* is followed and its TARGET is replaced
    (dotfile managers point configs at a master copy and expect that).
    Callers that must replace the symlink ITSELF (never write through it to an
    attacker-chosen target) pass ``replace_symlink=True``.
    """
    if crlf:
        content = content.replace("\n", "\r\n")
    write_path = path if replace_symlink or not path.is_symlink() else path.resolve(strict=False)
    write_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{write_path.name}.", dir=str(write_path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, write_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_config_text(path: Path) -> tuple[str, bool]:
    """Read a TOML config file, returning ``(text_with_lf, uses_crlf)``.

    The text is decoded as UTF-8 with ``\\r\\n`` normalized to ``\\n`` so
    that downstream regex patterns (which assume LF) work unchanged.
    ``uses_crlf`` is ``True`` when the original file contained ``\\r\\n``,
    so callers can pass it to :func:`atomic_write` to restore CRLF on write.
    Raises ``OSError`` if the file cannot be read.
    """
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")
    return text, crlf


def atomic_write_json(path: Path, data: Any, mode: int = 0o600,
                      replace_symlink: bool = False) -> None:
    """Serialize data as JSON and write to path atomically."""
    atomic_write(path, json.dumps(data, indent=2, sort_keys=False) + "\n", mode=mode,
                 replace_symlink=replace_symlink)


def validate_codex_path(path: Path, home: Path) -> None:
    """Raise ValueError if path or its parent escapes home or is a symlink."""
    if home.exists():
        if home.is_symlink() or not home.is_dir():
            raise ValueError(f"{home} must be a real directory")
        home_resolved = home.resolve(strict=True)
    else:
        home_resolved = home.resolve(strict=False)

    parent = path.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError(f"{parent} must be a real directory")
        if not parent.resolve(strict=True).is_relative_to(home_resolved):
            raise ValueError(f"{parent} escapes Codex home")
    if path.exists():
        if path.is_symlink():
            target_resolved = path.resolve(strict=False)
            user_home = Path.home().resolve(strict=True)
            if not target_resolved.is_relative_to(user_home):
                raise ValueError(f"{path} symlink target escapes user home")
            return
        if not path.resolve(strict=True).is_relative_to(home_resolved):
            raise ValueError(f"{path} escapes Codex home")


def ensure_codex_child(home: Path, *parts: str, create: bool = True) -> Path:
    """Return a path under home, creating missing directories and validating symlinks."""
    if home.exists():
        if home.is_symlink() or not home.is_dir():
            raise ValueError(f"{home} must be a real directory")
    elif create:
        home.mkdir(mode=0o700)
    home_resolved = home.resolve(strict=home.exists())

    target = home.joinpath(*parts)
    parent = target.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError(f"{parent} must be a real directory")
        parent_resolved = parent.resolve(strict=True)
        if not parent_resolved.is_relative_to(home_resolved):
            raise ValueError(f"{parent} escapes Codex home")
    elif create:
        parent.mkdir(mode=0o700)
    if target.exists() and target.is_symlink():
        target_resolved = target.resolve(strict=False)
        user_home = Path.home().resolve(strict=True)
        if not target_resolved.is_relative_to(user_home):
            raise ValueError(f"{target} symlink target escapes user home")
        return target
    target_resolved = target.resolve(strict=target.exists())
    if not target_resolved.is_relative_to(home_resolved):
        raise ValueError(f"{target} escapes Codex home")
    return target
