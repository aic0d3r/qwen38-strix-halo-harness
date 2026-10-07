#!/usr/bin/env python3
"""Token Optimizer — self-heal reconcile for dev-symlink / plugin-cache shadow.

Detects when ``~/.claude/skills/token-optimizer`` is a symlink to a dev repo
AND a version-pinned plugin cache copy exists under
``~/.claude/plugins/cache/*/token-optimizer/<version>/`` (read from
``installed_plugins.json``), or when the loaded skill version mismatches the
pinned plugin cache version. When the canonical target is UNAMBIGUOUS (the dev
symlink is at a NEWER VERSION than the pinned cache), it auto-reconciles to
a single canonical skill, non-destructively: the displaced/stale copy is backed
up under ``<claude_home>/_backups/token-optimizer/<timestamp>/`` BEFORE anything
is moved, then the stale skill tree is removed so only the canonical skill
loads. When AMBIGUOUS, it warns loudly and does NOT act.

Safety contract:
- NEVER delete without a backup first. If the backup dir is unwritable, abort
  the reconcile and warn, leaving state untouched.
- No-op for normal single-install users (plugin cache only, no symlink).
- No-op when running under a foreign runtime (detect_runtime() != "claude") so
  OpenCode/Copilot sessions never touch ~/.claude.
- Safe to call from the SessionStart ensure-health path: cheap, idempotent,
  never raises into the caller (errors are returned in the result dict).

The reconcile target is the SKILL tree only (``skills/token-optimizer/``
inside a cache version dir). The plugin's hooks/commands/etc. in the cache
version dir are preserved so the plugin keeps functioning; only the duplicate
skill is de-duplicated in favor of the canonical dev symlink. The cache skill
tree returns on the next plugin update (at which point versions match or the
user re-runs reconcile).

Usage:
    python3 install_reconcile.py [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from runtime_env import claude_home, detect_runtime  # noqa: E402

_PLUGIN_NAME = "token-optimizer"
_SKILL_DIR_NAME = "token-optimizer"
_BACKUP_ROOT_NAME = "_backups"
_MANIFEST_NAME = "installed_plugins.json"
_PLUGIN_JSON_NAME = "plugin.json"
# Bound JSON reads so a pathological file never chews the SessionStart budget.
_MAX_MANIFEST_BYTES = 1_048_576


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _parse_version(text: str) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple.

    Non-numeric segments are dropped; ``"unknown"`` / empty -> ``(0,)`` so any
    real version sorts above it. Never raises.
    """
    if not text:
        return (0,)
    parts: list[int] = []
    for chunk in str(text).strip().split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _read_plugin_version(skill_dir: Path) -> str | None:
    """Return the ``version`` field from ``<skill_dir>/../../plugin.json``.

    A cache version dir layout is ``<cache>/<mkt>/token-optimizer/<ver>/`` with
    ``plugin.json`` at the version-dir root and the skill at
    ``<ver>/skills/token-optimizer/``. A dev repo layout has ``plugin.json`` at
    the repo root and the skill at ``<repo>/skills/token-optimizer/``. We probe
    a few candidate parents so both resolve. Never raises.
    """
    candidates: list[Path] = []
    # Cache version dir: skill_dir = <ver>/skills/token-optimizer -> plugin.json
    # at <ver>/plugin.json (2 parents up).
    if skill_dir.parent.name == "skills":
        candidates.append(skill_dir.parent.parent / _PLUGIN_JSON_NAME)
    # Dev repo: skill_dir = <repo>/skills/token-optimizer -> plugin.json at
    # <repo>/plugin.json (3 parents up).
    candidates.append(skill_dir.parent.parent.parent / _PLUGIN_JSON_NAME)
    # Also try a plugin.json directly inside the skill dir (defensive).
    candidates.append(skill_dir / _PLUGIN_JSON_NAME)
    for cand in candidates:
        try:
            if cand.is_file() and cand.stat().st_size <= _MAX_MANIFEST_BYTES:
                with cand.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("version"), str):
                    return data["version"]
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def _load_installed_plugins(claude_home_dir: Path) -> dict:
    """Return the parsed installed_plugins.json, or {} on any failure."""
    manifest = claude_home_dir / "plugins" / _MANIFEST_NAME
    try:
        if not manifest.is_file() or manifest.stat().st_size > _MAX_MANIFEST_BYTES:
            return {}
        with manifest.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _active_cache_entries(claude_home_dir: Path) -> list[dict]:
    """Return active token-optimizer cache entries from installed_plugins.json.

    Each entry: {"marketplace": str, "version": str, "install_path": Path,
    "skill_dir": Path}.
    """
    registry = _load_installed_plugins(claude_home_dir)
    plugins = registry.get("plugins", {})
    if not isinstance(plugins, dict):
        return []
    entries: list[dict] = []
    for key, installs in plugins.items():
        if not isinstance(key, str) or not key.startswith(_PLUGIN_NAME + "@"):
            continue
        marketplace = key.split("@", 1)[1]
        if not isinstance(installs, list):
            continue
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            raw = inst.get("installPath", "")
            version = inst.get("version", "")
            if not raw:
                continue
            install_path = Path(raw)
            skill_dir = install_path / "skills" / _SKILL_DIR_NAME
            entries.append({
                "marketplace": marketplace,
                "version": str(version) if version else "",
                "install_path": install_path,
                "skill_dir": skill_dir,
            })
    return entries


def _all_cache_skill_dirs(claude_home_dir: Path) -> list[Path]:
    """Glob every token-optimizer skill dir under the plugin cache tree."""
    cache_root = claude_home_dir / "plugins" / "cache"
    found: list[Path] = []
    try:
        if not cache_root.is_dir():
            return found
        for mkt in sorted(cache_root.iterdir()):
            if not mkt.is_dir():
                continue
            plugin_dir = mkt / _PLUGIN_NAME
            if not plugin_dir.is_dir():
                continue
            for ver in sorted(plugin_dir.iterdir()):
                if not ver.is_dir():
                    continue
                skill_dir = ver / "skills" / _SKILL_DIR_NAME
                if skill_dir.is_dir():
                    found.append(skill_dir)
    except OSError:
        pass
    return found


def _backup_dir_writable(backup_root: Path) -> bool:
    """True if we can create+write a fresh subdir under backup_root.

    Probes without persisting an empty dir: if backup_root did not exist before
    the probe, it is removed again afterward, so an aborted/dry-run reconcile
    leaves ~/.claude genuinely untouched (the real reconcile re-creates it via
    backup_dest.mkdir when it actually needs it).
    """
    existed = backup_root.exists()
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        probe = backup_root / (".write-probe-" + _timestamp())
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
    finally:
        if not existed:
            try:
                backup_root.rmdir()  # only succeeds if still empty
            except OSError:
                pass


def _is_confined_to_cache(target: Path, home: Path) -> bool:
    """True only if `target` is a real path under <home>/plugins/cache with NO
    symlinked component along the way.

    Guards ``shutil.rmtree`` against an intermediate symlinked path component
    (or a crafted ``installPath`` in installed_plugins.json) redirecting a
    delete outside the plugin cache tree. Resolving
    both paths catches symlink escapes (a symlinked component would resolve
    outside the cache root); the per-component check additionally refuses to
    rmtree *through* any symlink even when it resolves back inside the cache.
    """
    cache_root = home / "plugins" / "cache"
    try:
        cache_root_resolved = cache_root.resolve(strict=True)
        target_resolved = target.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if not target_resolved.is_relative_to(cache_root_resolved):
        return False
    probe = cache_root
    if probe.is_symlink():
        return False
    try:
        rel_parts = target.relative_to(cache_root).parts
    except ValueError:
        return False
    for part in rel_parts:
        probe = probe / part
        if probe.is_symlink():
            return False
    return True


def _backup_tree(src: Path, backup_dest_parent: Path) -> Path | None:
    """Copy ``src`` tree into ``backup_dest_parent/<src-name>``. Returns dest or None.

    ``symlinks=True`` preserves any symlinks inside the tree as links rather
    than dereferencing them (so a link to a large/external target is backed up
    as a link, not a deep copy of someone else's data).
    """
    try:
        dest = backup_dest_parent / src.name
        # If a same-named backup already exists (rare), suffix to avoid clobber.
        i = 1
        while dest.exists():
            dest = backup_dest_parent / f"{src.name}-{i}"
            i += 1
        shutil.copytree(src, dest, symlinks=True)
        return dest
    except OSError:
        return None


def detect_and_reconcile(
    *,
    dry_run: bool = False,
    claude_home_override: Path | None = None,
) -> dict:
    """Detect a dev-symlink / plugin-cache skill shadow and reconcile if safe.

    Returns a result dict:
      {
        "action": "no-op" | "reconciled" | "warn" | "aborted",
        "reason": str,
        "dry_run": bool,
        "runtime": str,
        "symlink": str | None,        # path the symlink points at, if any
        "loaded_version": str | None,
        "cache_entries": [...],       # active entries from installed_plugins.json
        "backup": str | None,         # backup dir created, if any
        "removed": [str, ...],        # skill dirs removed (empty if none)
        "canonical": str | None,      # the canonical skill path left active
        "warnings": [str, ...],
      }

    Never raises: all errors are captured into ``action``/``reason``/``warnings``.
    """
    runtime = detect_runtime()
    result: dict = {
        "action": "no-op",
        "reason": "",
        "dry_run": dry_run,
        "runtime": runtime,
        "symlink": None,
        "loaded_version": None,
        "cache_entries": [],
        "backup": None,
        "removed": [],
        "canonical": None,
        "warnings": [],
    }

    # Guard: foreign runtime must never touch ~/.claude.
    if runtime != "claude":
        result["reason"] = f"foreign runtime ({runtime}); not modifying ~/.claude"
        return result

    home = claude_home_override if claude_home_override is not None else claude_home()
    skills_link = home / "skills" / _SKILL_DIR_NAME
    backup_root = home / _BACKUP_ROOT_NAME / _PLUGIN_NAME

    # Resolve the dev symlink (if any).
    symlink_target: Path | None = None
    is_symlink = False
    try:
        is_symlink = skills_link.is_symlink()
        if is_symlink:
            symlink_target = Path(os.readlink(skills_link))
            if not symlink_target.is_absolute():
                symlink_target = (skills_link.parent / symlink_target).resolve(strict=False)
            result["symlink"] = str(symlink_target)
    except OSError:
        result["warnings"].append(f"could not read symlink at {skills_link}")

    # Loaded skill version (from the symlink target's plugin.json, or the dir
    # itself if it is a real directory).
    loaded_version: str | None = None
    try:
        if is_symlink and symlink_target is not None:
            loaded_version = _read_plugin_version(symlink_target)
        elif skills_link.is_dir():
            loaded_version = _read_plugin_version(skills_link)
    except OSError:
        pass
    result["loaded_version"] = loaded_version

    cache_entries = _active_cache_entries(home)
    result["cache_entries"] = [
        {
            "marketplace": e["marketplace"],
            "version": e["version"],
            "install_path": str(e["install_path"]),
            "skill_dir": str(e["skill_dir"]),
            "skill_dir_exists": e["skill_dir"].is_dir(),
        }
        for e in cache_entries
    ]

    cache_skill_dirs_with_version: list[tuple[Path, str]] = []
    for e in cache_entries:
        if e["skill_dir"].is_dir():
            ver = e["version"] or _read_plugin_version(e["skill_dir"]) or ""
            cache_skill_dirs_with_version.append((e["skill_dir"], ver))

    # Also discover cache skill dirs NOT in installed_plugins.json (orphan
    # caches) so the ambiguous-two-caches case is detectable.
    active_skill_dir_paths = {e["skill_dir"] for e in cache_entries}
    for sd in _all_cache_skill_dirs(home):
        if sd not in active_skill_dir_paths:
            ver = _read_plugin_version(sd) or ""
            cache_skill_dirs_with_version.append((sd, ver))

    # --- Classification ---
    # Case: normal single-install user — plugin cache only, no symlink.
    if not is_symlink:
        if len(cache_skill_dirs_with_version) <= 1:
            result["reason"] = "no dev symlink and <=1 cache skill; nothing to reconcile"
            return result
        # Two+ cache skill dirs, no symlink, differing versions -> AMBIGUOUS.
        versions = {v for _, v in cache_skill_dirs_with_version if v}
        if len(versions) > 1:
            result["action"] = "warn"
            result["reason"] = "ambiguous: multiple plugin cache skill copies with differing versions and no dev symlink to disambiguate"
            conflict_desc = ", ".join(
                f"{sd.parent.parent.name}={v or 'unknown'}" for sd, v in sorted(cache_skill_dirs_with_version)
            )
            msg = (
                f"[Token Optimizer] CONFLICT: multiple token-optimizer skill copies in "
                f"{home / 'plugins' / 'cache'} ({conflict_desc}). "
                f"Manual reconcile: review {home / 'plugins' / 'installed_plugins.json'} "
                f"and remove the stale cache version dir(s) you do not want."
            )
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
            return result
        # Two+ caches but same version -> not a real conflict, no-op.
        result["reason"] = "multiple cache skill copies but same version; no reconcile needed"
        return result

    # --- A dev symlink exists ---
    if not cache_skill_dirs_with_version:
        # Symlink present, no cache skill -> already canonical, nothing to do.
        result["reason"] = "dev symlink present and no plugin cache skill to reconcile"
        result["canonical"] = str(skills_link)
        return result

    # Compare loaded (symlink) version vs each cache version.
    loaded_ver_tuple = _parse_version(loaded_version or "")
    # Find cache skill dirs whose version differs from the loaded one.
    stale_caches: list[tuple[Path, str]] = []
    for sd, v in cache_skill_dirs_with_version:
        if _parse_version(v) == loaded_ver_tuple and loaded_ver_tuple != (0,):
            # Same version as the loaded skill -> not stale, leave it.
            continue
        elif _parse_version(v) < loaded_ver_tuple:
            stale_caches.append((sd, v))
        else:
            # Cache is NEWER than the symlink, or versions incomparable.
            # Removing a dev symlink the user placed intentionally is destructive
            # -> treat as ambiguous (warn), do not act.
            pass

    newer_than_symlink = [
        (sd, v) for sd, v in cache_skill_dirs_with_version
        if _parse_version(v) > loaded_ver_tuple
    ]

    if newer_than_symlink:
        result["action"] = "warn"
        conflict_desc = ", ".join(f"{sd.parent.parent.name}={v or 'unknown'}" for sd, v in newer_than_symlink)
        msg = (
            f"[Token Optimizer] CONFLICT: dev symlink {skills_link} -> "
            f"{symlink_target} (version {loaded_version or 'unknown'}) is OLDER than "
            f"plugin cache skill copy ({conflict_desc}). "
            f"Manual reconcile: `git pull` in {symlink_target} to update the dev repo, "
            f"or remove the symlink with `rm {skills_link}` to use the plugin cache."
        )
        result["reason"] = "ambiguous: dev symlink is older than the plugin cache"
        result["warnings"].append(msg)
        print(msg, file=sys.stderr)
        return result

    if not stale_caches:
        # Symlink + cache at same version -> no-op (no false reconcile).
        result["reason"] = "dev symlink and plugin cache skill at the same version; no reconcile needed"
        result["canonical"] = str(skills_link)
        return result

    # --- UNAMBIGUOUS: symlink is newer than one or more stale caches. ---
    # Reconcile: back up each stale cache skill tree, then remove it so only the
    # dev symlink remains canonical. Never delete without a backup first.
    if dry_run:
        # Pre-flight the backup dir writability so the dry-run preview is honest.
        writable = _backup_dir_writable(backup_root)
        if not writable:
            result["action"] = "aborted"
            result["reason"] = f"backup dir {backup_root} is not writable; would abort reconcile"
            msg = f"[Token Optimizer] ABORT: backup dir {backup_root} is not writable; cannot reconcile safely."
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
            return result
        stale_desc = ", ".join(f"{sd.parent.parent.name}={v or 'unknown'}" for sd, v in stale_caches)
        result["action"] = "reconciled"
        result["reason"] = f"dry-run: would back up and remove stale cache skill(s) ({stale_desc}); dev symlink stays canonical"
        result["canonical"] = str(skills_link)
        result["backup"] = str(backup_root / _timestamp())
        return result

    # Verify backup dir is writable BEFORE touching anything.
    if not _backup_dir_writable(backup_root):
        result["action"] = "aborted"
        result["reason"] = f"backup dir {backup_root} is not writable; aborted before any change"
        msg = (
            f"[Token Optimizer] ABORT: backup dir {backup_root} is not writable. "
            "Reconcile aborted; ~/.claude left untouched. Fix permissions and re-run."
        )
        result["warnings"].append(msg)
        print(msg, file=sys.stderr)
        return result

    stamp = _timestamp()
    backup_dest = backup_root / stamp
    try:
        backup_dest.mkdir(parents=True, exist_ok=False)
    except OSError:
        result["action"] = "aborted"
        result["reason"] = f"could not create backup dir {backup_dest}"
        msg = f"[Token Optimizer] ABORT: could not create backup dir {backup_dest}; ~/.claude left untouched."
        result["warnings"].append(msg)
        print(msg, file=sys.stderr)
        return result

    removed: list[str] = []
    backups_made: list[str] = []
    aborted = False
    for sd, v in stale_caches:
        # Confinement guard: never rmtree a target that resolves outside the
        # plugin cache, or through a symlinked path component. Skip + warn
        # rather than risk deleting outside ~/.claude/plugins/cache.
        if not _is_confined_to_cache(sd, home):
            msg = (
                f"[Token Optimizer] SKIP: stale cache candidate {sd} is not safely "
                "confined to the plugin cache (symlinked component or resolves "
                "outside the cache tree). Not removed."
            )
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
            continue
        # Back up FIRST.
        backed = _backup_tree(sd, backup_dest)
        if backed is None:
            aborted = True
            msg = (
                f"[Token Optimizer] ABORT: failed to back up {sd} before removing it. "
                "Reconcile aborted; the stale cache skill was NOT removed. "
                f"Partial backups may exist under {backup_dest}."
            )
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
            break
        backups_made.append(str(backed))
        # Only now remove the stale skill tree.
        try:
            shutil.rmtree(sd)
            removed.append(str(sd))
            print(
                f"[Token Optimizer] Reconciled: backed up stale cache skill {sd} "
                f"(version {v or 'unknown'}) to {backed}, then removed it. "
                f"Canonical skill: {skills_link} -> {symlink_target}."
            )
        except OSError as exc:
            aborted = True
            msg = (
                f"[Token Optimizer] ABORT: backed up {sd} to {backed} but failed to remove it ({exc}). "
                "Backup retained; reconcile incomplete. Re-run after resolving the error."
            )
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
            break

    result["backup"] = str(backup_dest)
    result["removed"] = removed  # full list of removed cache skill dirs
    result["canonical"] = str(skills_link)
    if aborted:
        result["action"] = "aborted"
        result["reason"] = "reconcile aborted mid-way; backups retained"
    elif removed:
        result["action"] = "reconciled"
        result["reason"] = (
            f"backed up {len(backups_made)} stale cache skill(s) to {backup_dest} "
            f"and removed them; dev symlink {skills_link} is the canonical skill"
        )
    else:
        result["action"] = "no-op"
        result["reason"] = "no stale cache skill was removed"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect and reconcile a dev-symlink / plugin-cache skill shadow.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without modifying anything.",
    )
    return parser


# ---------------------------------------------------------------------------
# Uninstall-direction manifest reconcile
# ---------------------------------------------------------------------------

_KNOWN_MARKETPLACES_NAME = "known_marketplaces.json"
# A marketplace entry is "ours" when its name OR installLocation references
# token-optimizer. Our marketplace names derive from the repo, so they always
# contain "token-optimizer"; matching the installLocation too catches a
# renamed entry that still points at our marketplace dir.
_TOKEN_MARKER = "token-optimizer"


def _is_our_marketplace(name: str, entry: object, our_marketplaces=None) -> bool:
    """True only when we can EVIDENCE that ``name`` is our own marketplace.

    The old rule was a bare substring test -- any marketplace
    whose name merely contained "token-optimizer" was claimed as ours and
    deleted. A user's own marketplace (a fork, a "my-token-optimizer-tweaks"
    registry, a colleague's mirror) is not ours to remove, and
    known_marketplaces.json is Claude Code's file, not ours.

    Ownership now requires one of:
      * ``our_marketplaces`` -- the marketplace suffixes taken from OUR OWN
        installed_plugins keys (``token-optimizer@<marketplace>``), i.e. the
        registry itself says we installed a plugin from it; or
      * an ``installLocation`` whose final path component is a plugin-cache
        dir for our plugin name (``.../<something>token-optimizer<something>``
        is NOT enough -- the directory must actually be a marketplace dir we
        installed into, matched on an exact path segment).

    Anything else is reported (so the user can see it) but never removed.
    """
    if not isinstance(name, str):
        return False
    if our_marketplaces and name in our_marketplaces:
        return True
    if isinstance(entry, dict):
        loc = entry.get("installLocation", "")
        if isinstance(loc, str) and loc:
            try:
                segments = {seg.lower() for seg in Path(loc).parts}
            except (OSError, ValueError):
                segments = set()
            # Exact path-segment match, not a substring of the whole path:
            # ".../marketplaces/alexgreensh-token-optimizer" counts only when
            # that segment is one WE are installed from (checked above), while
            # a bare ".../token-optimizer" cache dir is ours by construction.
            if _PLUGIN_NAME in segments:
                return True
    return False


def _our_marketplace_names(registry: dict) -> set:
    """Marketplace suffixes from our own installed_plugins keys.

    ``token-optimizer@alexgreensh-token-optimizer`` -> the marketplace name
    after the ``@``. This is the registry's own statement that we installed a
    plugin from that marketplace, which is the only self-evident proof of
    ownership available.
    """
    names = set()
    for key in _our_plugin_keys(registry):
        _, _, suffix = key.partition("@")
        if suffix:
            names.add(suffix)
    return names


def _load_known_marketplaces(claude_home_dir: Path) -> dict:
    """Return the parsed known_marketplaces.json, or {} on any failure."""
    path = claude_home_dir / "plugins" / _KNOWN_MARKETPLACES_NAME
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _our_plugin_keys(registry: dict) -> list[str]:
    """Return our ``token-optimizer@<marketplace>`` keys in installed_plugins."""
    plugins = registry.get("plugins", {})
    if not isinstance(plugins, dict):
        return []
    return sorted(
        k for k in plugins
        if isinstance(k, str) and k.startswith(_PLUGIN_NAME + "@")
    )


def _entry_is_stale(install_path: str) -> bool:
    """True iff the entry's installPath no longer exists on disk."""
    if not install_path:
        return True
    try:
        return not Path(install_path).is_dir()
    except (OSError, ValueError):
        return True


# Default dashboard daemon port (mirrors measure.DAEMON_PORT). Read from the
# env so tests can point the probe at a closed port without importing measure.
_DEFAULT_DAEMON_PORT = 24842


def _daemon_port() -> int:
    """The dashboard daemon port, overridable via TOKEN_OPTIMIZER_DAEMON_PORT."""
    try:
        return int(os.environ.get("TOKEN_OPTIMIZER_DAEMON_PORT", _DEFAULT_DAEMON_PORT))
    except (TypeError, ValueError):
        return _DEFAULT_DAEMON_PORT


def _dashboard_daemon_alive(port: int | None = None, timeout: float = 1.0) -> bool:
    """True iff OUR dashboard daemon is currently serving on the loopback port.

    Self-contained liveness probe (no ``measure`` import -- that would be
    circular: measure imports this module). Used by ``reconcile_uninstall`` to
    protect a LIVE daemon's manifest entry from a stale-path misclassification
    during a routine plugin update: when the marketplace cache path moves on
    update, the active identity's ``installPath`` is momentarily gone, so the
    bare "installPath missing -> stale" rule would drop the running daemon's
    manifest entry and orphan it (a later sweep then tears down the plist). A
    live daemon on the port disproves "stale", so the entry is kept.

    Identity-checked: only trusts the response when ``server ==
    "token-optimizer-daemon"`` (mirrors ``measure._verify_daemon_port``), so a
    foreign listener on the port never fakes liveness. Fail-open: any error ->
    False, so a probe failure falls back to the existing installPath rule and
    can never block a legitimate stale removal -- and never raises.
    """
    if port is None:
        port = _daemon_port()
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=timeout
        ) as resp:
            body = resp.read(2048).decode("utf-8", "replace")
        data = json.loads(body)
        return isinstance(data, dict) and data.get("server") == "token-optimizer-daemon"
    except Exception:  # noqa: BLE001 -- probe must never raise
        return False


def _atomic_write_json(path: Path, data) -> bool:
    """Write JSON to ``path`` via a temp file + os.replace. Returns success."""
    import tempfile
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, str(path))
            return True
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except OSError:
        return False


def _backup_file(src: Path, backup_dest_parent: Path) -> Path | None:
    """Copy a single file into ``backup_dest_parent/<src.name>``. Returns dest or None."""
    try:
        dest = backup_dest_parent / src.name
        i = 1
        while dest.exists():
            dest = backup_dest_parent / f"{src.name}-{i}"
            i += 1
        shutil.copy2(src, dest)
        return dest
    except OSError:
        return None


def reconcile_uninstall(
    claude_home_dir: Path,
    *,
    dry_run: bool = False,
    remove: bool = False,
    daemon_already_stopped: bool = False,
) -> dict:
    """Report (and optionally remove) our stale host-manifest entries.

    Two Claude Code state files are reconciled on the UNINSTALL direction:

    - ``installed_plugins.json``: our ``token-optimizer@*`` entries whose
      ``installPath`` no longer exists (left dangling after
      ``/plugin uninstall`` removed the cache dir but not the manifest key).
    - ``known_marketplaces.json``: our marketplace entries (name or
      installLocation references token-optimizer).

    By default (``remove=False``) this only REPORTS what is stale. Under the
    explicit cleanup command (``remove=True``) it writes a backup of each file
    first, then removes our STALE entries, leaving other plugins' entries
    byte-identical. Never raises; all errors land in ``warnings``.

    When *daemon_already_stopped* is True (set by ``measure.cleanup()`` after
    step 1 already stopped the daemon), the live-daemon probe is SKIPPED and
    any key whose installPath is gone is classified as stale. This prevents a
    false-alive probe (e.g. a real daemon on a different port responding to
    the health check, or a port collision in a test) from blocking manifest
    pruning during cleanup.

    Returns a result dict with ``reported`` and ``removed`` lists plus
    ``backup`` (the backup dir created, when any) and ``warnings``.
    """
    result: dict = {
        "dry_run": dry_run,
        "remove": remove,
        "reported": [],
        "removed": [],
        "backup": None,
        "warnings": [],
    }
    home = claude_home_dir
    plugins_dir = home / "plugins"
    installed_path = plugins_dir / _MANIFEST_NAME
    marketplaces_path = plugins_dir / _KNOWN_MARKETPLACES_NAME

    registry = _load_installed_plugins(home)
    our_keys = _our_plugin_keys(registry)
    # FIX-SPEC-DAEMON req 1: a LIVE dashboard daemon disproves "stale". During a
    # routine plugin update the marketplace cache path can move, so the active
    # identity's installPath is momentarily gone and the bare "installPath
    # missing -> stale" rule would drop the running daemon's manifest entry
    # (orphaning it -- a later sweep then tears down the plist). Probe the daemon
    # once; if it is alive, no our-entry is classified stale, so a routine update
    # never removes the active daemon's manifest entry. Fail-open: a probe error
    # -> not alive (the installPath rule still removes genuinely-stale entries),
    # and the call site below also guards so the protection can never raise.
    daemon_alive = False
    if our_keys and not daemon_already_stopped:
        try:
            daemon_alive = _dashboard_daemon_alive()
        except Exception:  # noqa: BLE001 -- protection must never raise
            daemon_alive = False
    stale_keys: list[str] = []
    for key in our_keys:
        installs = registry.get("plugins", {}).get(key, [])
        if not isinstance(installs, list) or not installs:
            # No install records -- stale unless a live daemon protects it.
            if not daemon_alive:
                stale_keys.append(key)
            continue
        # An entry is stale if ALL its install records point at gone paths.
        all_gone = all(
            _entry_is_stale(str(inst.get("installPath", "")))
            for inst in installs if isinstance(inst, dict)
        ) or not any(isinstance(inst, dict) for inst in installs)
        # FIX-SPEC-DAEMON req 1: a live daemon protects the active identity
        # from a stale-path misclassification during a plugin update (the
        # marketplace cache path moved, so installPath is gone but the daemon
        # is still serving). Classified active/kept, never removed by reconcile.
        if all_gone and daemon_alive:
            all_gone = False
        if all_gone:
            stale_keys.append(key)
        result["reported"].append({
            "file": str(installed_path),
            "key": key,
            "stale": all_gone,
            "daemon_alive": daemon_alive,
            "install_path": installs[0].get("installPath", "") if isinstance(installs[0], dict) else "",
        })

    marketplaces = _load_known_marketplaces(home)
    # Ownership must be evidenced by our own installed_plugins
    # keys (or an exact plugin-name path segment), never a bare name substring.
    our_marketplace_names = _our_marketplace_names(registry)
    our_mkt_keys = sorted(
        k for k, v in marketplaces.items()
        if _is_our_marketplace(k, v, our_marketplace_names)
    )
    for key in our_mkt_keys:
        entry = marketplaces.get(key, {})
        loc = entry.get("installLocation", "") if isinstance(entry, dict) else ""
        stale = bool(loc) and not Path(loc).is_dir()
        result["reported"].append({
            "file": str(marketplaces_path),
            "key": key,
            "stale": stale,
            "install_location": loc,
        })

    if not remove:
        return result

    # Removal path: back up both files first, then drop our STALE entries.
    to_remove_installed = [k for k in stale_keys]
    to_remove_marketplaces = [
        k for k in our_mkt_keys
        if (isinstance(marketplaces.get(k), dict)
            and _entry_is_stale(str(marketplaces[k].get("installLocation", ""))))
    ]
    if not to_remove_installed and not to_remove_marketplaces:
        return result

    if dry_run:
        # Dry-run + remove: disclose what WOULD be removed, touch nothing
        # (no backup dir created, no files written).
        for key in to_remove_installed:
            result["removed"].append(f"{installed_path}::{key}")
        for key in to_remove_marketplaces:
            result["removed"].append(f"{marketplaces_path}::{key}")
        return result

    backup_root = home / _BACKUP_ROOT_NAME / _PLUGIN_NAME
    if not _backup_dir_writable(backup_root):
        msg = f"[Token Optimizer] ABORT: backup dir {backup_root} not writable; manifest entries left in place."
        result["warnings"].append(msg)
        print(msg, file=sys.stderr)
        return result
    stamp = _timestamp()
    backup_dest = backup_root / stamp
    try:
        backup_dest.mkdir(parents=True, exist_ok=False)
    except OSError:
        msg = f"[Token Optimizer] ABORT: could not create backup dir {backup_dest}; manifest entries left in place."
        result["warnings"].append(msg)
        print(msg, file=sys.stderr)
        return result
    result["backup"] = str(backup_dest)

    if to_remove_installed and installed_path.is_file():
        backed = _backup_file(installed_path, backup_dest)
        if backed is None:
            msg = f"[Token Optimizer] ABORT: could not back up {installed_path}; installed_plugins entries left in place."
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
        else:
            plugins = registry.get("plugins", {})
            for key in to_remove_installed:
                plugins.pop(key, None)
                result["removed"].append(f"{installed_path}::{key}")
            if not _atomic_write_json(installed_path, registry):
                msg = f"[Token Optimizer] WARNING: failed to write {installed_path}; entries may remain."
                result["warnings"].append(msg)
                print(msg, file=sys.stderr)

    if to_remove_marketplaces and marketplaces_path.is_file():
        backed = _backup_file(marketplaces_path, backup_dest)
        if backed is None:
            msg = f"[Token Optimizer] ABORT: could not back up {marketplaces_path}; known_marketplaces entries left in place."
            result["warnings"].append(msg)
            print(msg, file=sys.stderr)
        else:
            for key in to_remove_marketplaces:
                marketplaces.pop(key, None)
                result["removed"].append(f"{marketplaces_path}::{key}")
            if not _atomic_write_json(marketplaces_path, marketplaces):
                msg = f"[Token Optimizer] WARNING: failed to write {marketplaces_path}; entries may remain."
                result["warnings"].append(msg)
                print(msg, file=sys.stderr)

    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = detect_and_reconcile(dry_run=args.dry_run)
    action = result["action"]
    if action == "reconciled":
        prefix = "Would reconcile" if args.dry_run else "Reconciled"
        print(f"[Token Optimizer] {prefix}: {result['reason']}")
        if result.get("backup"):
            print(f"[Token Optimizer]   backup: {result['backup']}")
        if result.get("canonical"):
            print(f"[Token Optimizer]   canonical: {result['canonical']}")
        return 0
    if action == "warn":
        # Warnings already printed inside detect_and_reconcile.
        return 0
    if action == "aborted":
        return 1
    # no-op
    if not args.dry_run:
        # Keep the ensure-health path quiet on a clean no-op.
        return 0
    print(f"[Token Optimizer] no-op: {result['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
