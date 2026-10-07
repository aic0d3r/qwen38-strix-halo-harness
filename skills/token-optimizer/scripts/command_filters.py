#!/usr/bin/env python3
"""User-editable TOML command-filter schema + loader.

Lets a user opt NEW read-only commands into existing compression handlers
and exclude commands from compression, without weakening safety. The loader
is pure stdlib (tomllib with a no-tomllib fallback), performs no file writes,
and gates every ``add`` entry through ``_is_safe_add`` before it reaches the
effective allowlist.

File location: ``<runtime-home>/token-optimizer/command-filters.toml``
(sibling of the existing ``config.json``), overridable via
``TOKEN_OPTIMIZER_COMMAND_FILTERS``.

Schema (illustrative)::

    # Add read-only commands to an existing handler.
    # pattern_name = { command = "...", handler = "git_status|pytest|lint|..." }
    [filters.add]
    my-tests = { command = "cargo test", handler = "pytest" }
    my-lint = { command = "ruff check", handler = "lint" }

    # Exclude commands from compression (glob patterns supported).
    [filters.exclude]
    commands = ["git status", "ls -la"]

Rules:
  * ``add`` entries are appended only if they pass ``_is_safe_add``
    (read-only, not a shell interpreter, not sudo/su, no write subcommand).
  * ``exclude`` entries remove commands from the effective set.
  * Categorical exclusions (dangerous chars, git write subcmds, interpreters)
    are enforced downstream in bash_hook/bash_compress, never overridden
    here.
  * A missing file is a no-op (empty config). Malformed TOML is fail-open
    (empty config + one-time stderr warning).
"""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_FILTERS_PATH = "TOKEN_OPTIMIZER_COMMAND_FILTERS"
DEFAULT_FILTERS_FILENAME = "command-filters.toml"

# Valid handler names (the keys of bash_compress._PATTERN_HANDLERS). Listed
# here so the loader can reject unknown handlers without importing
# bash_compress (which would create a circular dependency on the hot path).
VALID_HANDLERS = frozenset({
    "git_status", "git_log", "git_diff", "pytest", "jest", "npm_install",
    "ls", "lint", "logs", "tree", "progress", "list", "build", "sqlite3",
    "disk_stats", "docker_output", "json", "csv", "stack_trace", "k8s",
    "cloud_cli", "search_results",
})

# Commands that are NEVER safe to add (shell interpreters, privilege
# escalation, destructive utilities). Mirrors bash_hook's safety gates.
_UNSAFE_INTERPRETERS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "fish", "csh", "tcsh",
    "python", "python3", "python2", "ruby", "perl", "node", "deno", "bun",
    "lua", "tcl", "awk", "sed", "eval", "exec", "source", ".",
})
_UNSAFE_PREFIXES = frozenset({"sudo", "su", "doas", "pkexec", "gksudo"})
_UNSAFE_WRITE_SUBCMDS = frozenset({
    "rm", "mv", "cp", "chmod", "chown", "chgrp", "mkdir", "rmdir", "ln",
    "dd", "mkfs", "fdisk", "mount", "umount", "kill", "killall", "pkill",
    "shutdown", "reboot", "halt", "poweroff",
})
_DANGEROUS_CHARS = frozenset(";|&`$(){}><\n\r\x00")

_warned_paths: set = set()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AddEntry:
    """A user-declared add rule: map a command to an existing handler."""
    name: str
    command: str
    handler: str


@dataclass(frozen=True)
class CommandFilterConfig:
    """Normalized filter config loaded from TOML."""
    adds: Tuple[AddEntry, ...] = ()
    excludes: Tuple[str, ...] = ()

    def is_excluded(self, command_str: str) -> bool:
        """Return True if ``command_str`` matches any exclude glob/pattern."""
        for pattern in self.excludes:
            if fnmatch.fnmatch(command_str, pattern):
                return True
        return False

    def find_add(self, command_str: str) -> Optional[AddEntry]:
        """Return the first add entry whose command matches ``command_str``,
        or None."""
        for entry in self.adds:
            if entry.command == command_str:
                return entry
            # Also support glob matching on the add command.
            if fnmatch.fnmatch(command_str, entry.command):
                return entry
        return None


@dataclass(frozen=True)
class EffectiveFilters:
    """The merged result of builtin + user filters, ready for bash_hook /
    bash_compress to consult on the hot path."""
    user_adds: Tuple[AddEntry, ...] = field(default_factory=tuple)
    user_excludes: Tuple[str, ...] = field(default_factory=tuple)

    def is_user_excluded(self, command_str: str) -> bool:
        for pattern in self.user_excludes:
            if fnmatch.fnmatch(command_str, pattern):
                return True
        return False

    def find_user_add(self, command_str: str) -> Optional[AddEntry]:
        for entry in self.user_adds:
            if entry.command == command_str:
                return entry
            if fnmatch.fnmatch(command_str, entry.command):
                return entry
        return None


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------

def _is_all_wildcard(token: str) -> bool:
    """True when ``token`` is a non-empty run of only ``*`` (e.g. ``*``/``**``).
    Such a pattern matches every command via fnmatch and is far too broad to
    opt into a compression handler."""
    return bool(token) and set(token) <= {"*"}


def _effective_cmd0(tokens: List[str]) -> Optional[str]:
    """Return the basename of the REAL command being run.

    Normalizes away the path (so ``/bin/bash``, ``./python``, ``/usr/bin/sudo``
    are recognized as ``bash``/``python``/``sudo``) and resolves a leading
    ``env`` invocation by skipping ``VAR=val`` assignments and env's own options
    (``-i``, and the operand-taking ``-u``/``-C``/``-S``) to reach the command
    env would exec. Returns None when no command token remains.
    """
    if not tokens:
        return None
    i = 0
    if os.path.basename(tokens[0]) == "env":
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if "=" in tok and not tok.startswith("-"):
                i += 1  # VAR=val assignment
                continue
            if tok in ("-i", "--ignore-environment", "-", "-v", "--debug"):
                i += 1
                continue
            if tok in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string"):
                i += 2  # option consumes its operand
                continue
            break  # first non-option, non-assignment token = the command
    if i >= len(tokens):
        return None
    return os.path.basename(tokens[i])


def _is_safe_add(command: str, handler: str) -> bool:
    """Gate a user ``add`` entry before it reaches the effective allowlist.

    Rejects:
      * Empty command or handler.
      * Unknown handler (not in VALID_HANDLERS).
      * Commands containing dangerous shell metacharacters.
      * Bare/overly-broad wildcard patterns (just ``*`` or ``**``).
      * Shell interpreters (bash, python, node, etc.) as argv[0] -- recognized
        even when given by absolute/relative path or via ``env``.
      * Privilege escalation prefixes (sudo, su, doas, pkexec) -- likewise
        path- and ``env``-normalized.
      * Destructive write subcommands (rm, mv, chmod, etc.) as argv[0].
    Returns True only for read-only commands mapped to a known handler.

    Defense-in-depth: dangerous shell metacharacters are already blocked
    upstream (bash_hook main, before the whitelist), so there is no injection
    here; this gate exists to deliver exactly what its docstring claims.
    """
    if not command or not command.strip():
        return False
    if handler not in VALID_HANDLERS:
        return False
    for ch in command:
        if ch in _DANGEROUS_CHARS:
            return False
    stripped = command.strip()
    if _is_all_wildcard(stripped):
        return False
    tokens = stripped.split()
    if not tokens:
        return False
    cmd0 = _effective_cmd0(tokens)
    if cmd0 is None:
        return False
    if _is_all_wildcard(cmd0):
        return False
    if cmd0 in _UNSAFE_INTERPRETERS:
        return False
    if cmd0 in _UNSAFE_PREFIXES:
        return False
    if cmd0 in _UNSAFE_WRITE_SUBCMDS:
        return False
    return True


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _filters_path() -> Optional[Path]:
    """Resolve the TOML file path from env override or the default runtime
    home. Returns None when no path is resolvable."""
    env_path = os.environ.get(ENV_FILTERS_PATH)
    if env_path:
        return Path(env_path)
    # Default: <runtime-home>/token-optimizer/command-filters.toml
    # The runtime home is the same dir as config.json. We resolve it via
    # the same plugin_env helper used by the config loader, but avoid
    # importing plugin_env on the hot path when the file doesn't exist.
    try:
        from plugin_env import resolve_plugin_data_dir, _USER_CONFIG_DIR
        candidates = [_USER_CONFIG_DIR / DEFAULT_FILTERS_FILENAME]
        pdd = resolve_plugin_data_dir()
        if pdd is not None:
            candidates.append(pdd / "config" / DEFAULT_FILTERS_FILENAME)
        for c in candidates:
            if c.exists():
                return c
    except Exception:
        pass
    return None


def _warn_once(path: Path, message: str) -> None:
    """One-time stderr warning per path (avoids spamming every hook call)."""
    key = str(path)
    if key in _warned_paths:
        return
    _warned_paths.add(key)
    print(f"[Command Filters] {message}: {path}", file=sys.stderr)


def load_filters() -> CommandFilterConfig:
    """Load and validate the TOML command-filter file.

    Returns a normalized ``CommandFilterConfig``. A missing file is a no-op
    (empty config). Malformed TOML is fail-open (empty config + one-time
    warning). Malformed entries are skipped with a one-time warning. Never
    raises. Performs no file writes.
    """
    path = _filters_path()
    if path is None or not path.exists():
        return CommandFilterConfig()
    if tomllib is None:
        _warn_once(path, "tomllib not available; ignoring")
        return CommandFilterConfig()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _warn_once(path, f"malformed TOML ({exc}); ignoring")
        return CommandFilterConfig()
    return _parse_filters(data, path)


def _parse_filters(data: dict, path: Path) -> CommandFilterConfig:
    """Parse the raw TOML dict into a normalized CommandFilterConfig.

    Validates structure; skips malformed entries with a one-time warning.
    """
    filters = data.get("filters")
    if not isinstance(filters, dict):
        return CommandFilterConfig()
    adds: List[AddEntry] = []
    excludes: List[str] = []
    # [filters.add] — mapping of pattern_name = { command, handler }
    add_section = filters.get("add")
    if isinstance(add_section, dict):
        for name, spec in add_section.items():
            if not isinstance(spec, dict):
                _warn_once(path, f"add entry '{name}' is not a table; skipping")
                continue
            command = spec.get("command")
            handler = spec.get("handler")
            if not isinstance(command, str) or not isinstance(handler, str):
                _warn_once(path, f"add entry '{name}' missing command/handler; skipping")
                continue
            if not _is_safe_add(command, handler):
                _warn_once(path, f"add entry '{name}' failed safety gate; skipping")
                continue
            adds.append(AddEntry(name=name, command=command.strip(), handler=handler))
    # [filters.exclude] — table with a `commands` array
    exclude_section = filters.get("exclude")
    if isinstance(exclude_section, dict):
        commands = exclude_section.get("commands")
        if isinstance(commands, list):
            for item in commands:
                if isinstance(item, str) and item.strip():
                    excludes.append(item.strip())
                else:
                    _warn_once(path, "exclude entry is not a string; skipping")
    return CommandFilterConfig(adds=tuple(adds), excludes=tuple(excludes))


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_filters(user: CommandFilterConfig) -> EffectiveFilters:
    """Compute the effective filter set from user config.

    The builtin allowlist/dispatch is not passed in (it lives in bash_hook /
    bash_compress and is consulted first on the hot path). This function
    simply wraps the user config into an ``EffectiveFilters`` object ready
    for downstream consultation. User ``add`` entries have already passed
    ``_is_safe_add`` in the loader; user ``exclude`` entries are glob
    patterns checked against the command string.
    """
    return EffectiveFilters(
        user_adds=user.adds,
        user_excludes=user.excludes,
    )


# ---------------------------------------------------------------------------
# Cached accessor for the hot path
# ---------------------------------------------------------------------------

_cached: Optional[EffectiveFilters] = None


def get_effective_filters() -> EffectiveFilters:
    """Return the merged effective filters, loading once and caching for the
    process lifetime. The hot path (bash_hook / bash_compress) calls this
    instead of load_filters + merge_filters directly."""
    global _cached
    if _cached is not None:
        return _cached
    _cached = merge_filters(load_filters())
    return _cached


def reset_cache() -> None:
    """Reset the process-level cache (for tests)."""
    global _cached
    _cached = None


__all__ = [
    "AddEntry",
    "CommandFilterConfig",
    "ENV_FILTERS_PATH",
    "EffectiveFilters",
    "VALID_HANDLERS",
    "get_effective_filters",
    "load_filters",
    "merge_filters",
    "reset_cache",
]
