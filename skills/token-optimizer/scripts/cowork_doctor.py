#!/usr/bin/env python3
"""Cowork adapter readiness checks for Token Optimizer.

Reports what can be verified FROM THIS MACHINE: desktop build, domain
allowlist state (the live dxt:allowlist* keys in the Claude desktop
config), local VM session tree, probe fire-matrix, payload built, OTel /
collector wiring. What it cannot see — the org admin plugin console state
and whether hooks fired in a CLOUD session — it reports as NEEDS-LIVE so
the live-verification checklist is explicit rather than silently green.

Stdlib-only on purpose: must run on a machine with nothing but python3.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ONE source of truth: import the event tuple the packager actually
# emits instead of duplicating it. The doctor previously hardcoded
# ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"), so it FAILed a
# correctly built payload -- SessionStart does NOT fire in Cowork and PostToolUse
# does -- and told the operator not to ship. cowork_doctor.py ships beside
# cowork_install.py in the same scripts dir, so this import always resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_install import COWORK_EVENTS  # noqa: E402


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _repo_root() -> Path:
    """Walk up to the plugin root (holds .claude-plugin/plugin.json).

    Mirrors cowork_install._repo_root: parents[3] is wrong from the
    Codex-marketplace mirror tree, so walk up for the marker and fall back to the
    legacy index if none is found. Keeps the doctor from reading payload/version
    off the wrong root in the mirror.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            return parent
    parents = here.parents
    return parents[3] if len(parents) > 3 else here.parent


def _desktop_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude"
    return Path.home() / ".config" / "Claude"


def _desktop_build_checks() -> list[dict[str, str]]:
    if sys.platform != "darwin":
        return [_check("WARN", "Cowork desktop build", f"unsupported platform {sys.platform}; check the app version manually")]
    plist = Path("/Applications/Claude.app/Contents/Info.plist")
    if not plist.exists():
        return [_check("WARN", "Cowork desktop build", "/Applications/Claude.app not found")]
    try:
        info = plistlib.loads(plist.read_bytes())
    except Exception as exc:  # noqa: BLE001 - report, never crash the doctor
        return [_check("WARN", "Cowork desktop build", f"could not read Info.plist: {exc}")]
    version = info.get("CFBundleShortVersionString", "unknown")
    # OTel needs Desktop >= 1.1.4173 (local) / >= 1.22209.3 (cloud), Team or
    # Enterprise (claude.com/docs/cowork/monitoring, fetched 2026-08-13).
    return [_check("OK", "Cowork desktop build", f"Claude.app {version} (OTel needs >= 1.1.4173 local / 1.22209.3 cloud)")]


def _allowlist_checks() -> list[dict[str, str]]:
    config = _desktop_support_dir() / "config.json"
    if not config.exists():
        return [_check("WARN", "Domain allowlist", f"{config} not found; is the Claude desktop app installed?")]
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_check("WARN", "Domain allowlist", f"could not read {config}: {exc}")]

    checks: list[dict[str, str]] = []
    keys = {k: v for k, v in data.items() if k.startswith("dxt:allowlist")}
    if not keys:
        return [_check("WARN", "Domain allowlist", "no dxt:allowlist* keys in desktop config; allowlist machinery not active on this machine")]
    enabled_keys = {k: v for k, v in keys.items() if k.startswith("dxt:allowlistEnabled")}
    for key, value in sorted(enabled_keys.items()):
        scope = "global" if key == "dxt:allowlistEnabled" else f"env {key.split(':', 2)[2][:8]}…"
        updated = data.get(key.replace("allowlistEnabled", "allowlistLastUpdated"), "?")
        if value:
            checks.append(_check("OK", f"Domain allowlist ({scope})", f"ENFORCED (updated {updated}) — the TO collector domain MUST be on the org allowlist or phone-home/OTel POSTs are blocked"))
        else:
            checks.append(_check("OK", f"Domain allowlist ({scope})", f"not enforced (enabled=false, updated {updated}) — outbound hook POSTs are not domain-filtered here"))
    # The allowlist contents live server-side; the local cache is opaque.
    checks.append(_check("NEEDS-LIVE", "Allowlist contents", "dxt:allowlistCache is an opaque blob; confirm the TO collector domain in the org admin console, not locally"))
    return checks


def _session_tree_checks() -> list[dict[str, str]]:
    tree = _desktop_support_dir() / "local-agent-mode-sessions"
    if not tree.exists():
        return [_check("WARN", "Local VM session tree", f"{tree} not found — no local Cowork sessions on this machine yet")]
    sessions = [d for d in tree.iterdir() if d.is_dir() and d.name != "skills-plugin"]
    checks = [_check("OK", "Local VM session tree", f"{tree} ({len(sessions)} session dir(s))")]
    audits = sorted(tree.glob("*/**/audit.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if audits:
        checks.append(_check("OK", "audit.jsonl", f"{len(audits)} found; newest: {audits[0]}"))
    else:
        checks.append(_check("WARN", "audit.jsonl", "none found under the session tree (cloud-only usage, or format changed)"))
    bridge = list(tree.glob("*/**/bridge-state.json"))
    if bridge:
        checks.append(_check("OK", "Cloud-vs-local marker", f"bridge-state.json present ({len(bridge)}) — local VM sessions confirmed"))
    else:
        checks.append(_check("WARN", "Cloud-vs-local marker", "no bridge-state.json found; cannot distinguish cloud vs local from disk"))
    return checks


def _probe_checks() -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    fired: dict[str, int] = {}
    ledgers = [
        Path(os.environ.get("TO_PROBE_DIR", "") or (Path.home() / ".to-hook-probe")) / "fired.log",
        Path("/tmp/to-hook-probe/fired.log"),
    ]
    seen_ledger = None
    for ledger in ledgers:
        try:
            for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    fired[parts[1]] = fired.get(parts[1], 0) + 1
            seen_ledger = seen_ledger or ledger
        except OSError:
            continue
    if not fired:
        checks.append(_check("NEEDS-LIVE", "Hook fire matrix", "no to-hook-probe ledger found — push to-hook-probe via the org console and run one Cowork session (see cowork/to-hook-probe/README.md)"))
        return checks
    checks.append(_check("OK", "Probe ledger", f"{seen_ledger}"))
    for event in COWORK_EVENTS:
        if event in fired:
            checks.append(_check("OK", f"Hook fires: {event}", f"{fired[event]} time(s)"))
        else:
            checks.append(_check("FAIL", f"Hook fires: {event}", "expected for the Cowork payload but never seen in the probe ledger"))
    extras = sorted(set(fired) - set(COWORK_EVENTS))
    if extras:
        checks.append(_check("OK", "Bonus events firing", f"{', '.join(extras)} — consider widening COWORK_EVENTS in cowork_install.py"))
    return checks


def _payload_checks(root: Path) -> list[dict[str, str]]:
    try:
        version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "?")
    except (OSError, json.JSONDecodeError):
        version = "?"
    dist = root / "dist" / "cowork"
    zips = sorted(dist.glob("*.zip")) if dist.exists() else []
    if not zips:
        return [_check("WARN", "Cowork payload", "not built yet; run: bash install.sh --cowork")]
    checks = [_check("OK", "Cowork payload", ", ".join(z.name for z in zips))]
    if not any(version in z.name for z in zips if "token-optimizer" in z.name):
        checks.append(_check("WARN", "Payload version", f"no zip matches plugin.json version {version}; rebuild with: bash install.sh --cowork"))
    hooks_file = dist / f"token-optimizer-cowork-{version}" / "hooks" / "hooks.json"
    try:
        events = sorted(json.loads(hooks_file.read_text(encoding="utf-8")).get("hooks", {}))
        missing = [e for e in COWORK_EVENTS if e not in events]
        if missing:
            checks.append(_check("FAIL", "Payload hook events", f"missing {', '.join(missing)} (has: {', '.join(events)})"))
        else:
            checks.append(_check("OK", "Payload hook events", ", ".join(events)))
    except (OSError, json.JSONDecodeError):
        checks.append(_check("WARN", "Payload hook events", f"could not read {hooks_file}"))
    return checks


def _telemetry_checks() -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    otel = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if otel:
        checks.append(_check("OK", "OTel endpoint (env)", otel))
    else:
        checks.append(_check("NEEDS-LIVE", "OTel endpoint", "org-admin setting (Organization settings -> Cowork); not readable from this machine. Team/Enterprise only; http/protobuf (no gRPC for Cowork)"))
    collector = os.environ.get("TO_COWORK_COLLECTOR_URL", "").strip() or os.environ.get("TO_PROBE_URL", "").strip()
    if not collector:
        checks.append(_check("WARN", "TO collector", "TO_COWORK_COLLECTOR_URL/TO_PROBE_URL not set; start cowork/collector/to_collector.py and set probe.env before packaging"))
    else:
        try:
            if any(ord(char) < 32 or ord(char) == 127 for char in collector):
                raise ValueError("control character")
            parsed = urllib.parse.urlsplit(collector)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("expected an HTTP(S) base URL without credentials, query, or fragment")
            _ = parsed.port  # Validate malformed ports before the request.
        except ValueError:
            checks.append(_check("WARN", "TO collector", "invalid collector URL; use an HTTP(S) base URL without credentials, query, or fragment"))
            return checks

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                return None

        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(f"{collector.rstrip('/')}/healthz", timeout=2) as resp:
                ok = resp.status == 200
            checks.append(_check("OK" if ok else "WARN", "TO collector", f"{collector} healthz {'OK' if ok else 'unexpected status'}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("WARN", "TO collector", f"{collector} unreachable: {exc}"))
    return checks


def _org_console_checks() -> list[dict[str, str]]:
    return [
        _check("NEEDS-LIVE", "Org console: plugin pushed", "confirm token-optimizer (and to-hook-probe first) is registered as available/default/required in the Anthropic admin console"),
        _check("NEEDS-LIVE", "Org console: allowlist entry", "confirm the TO collector domain is on Cowork's domain allowlist"),
        _check("NEEDS-LIVE", "Cloud session evidence", "cloud sessions leave no local disk; verify via collector probe.jsonl POSTs from a cloud run"),
    ]


def run_checks() -> list[dict[str, str]]:
    root = _repo_root()
    checks = [_check("OK", "Repo root", str(root))]
    checks.extend(_desktop_build_checks())
    checks.extend(_allowlist_checks())
    checks.extend(_session_tree_checks())
    checks.extend(_payload_checks(root))
    checks.extend(_probe_checks())
    checks.extend(_telemetry_checks())
    checks.extend(_org_console_checks())
    return checks


def _print_text(checks: list[dict[str, str]]) -> None:
    print("\nToken Optimizer Cowork Doctor")
    print("=" * 29)
    for check in checks:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("OK", "WARN", "FAIL", "NEEDS-LIVE")}
    print(f"\nSummary: {counts['OK']} OK, {counts['WARN']} WARN, {counts['FAIL']} FAIL, {counts['NEEDS-LIVE']} need a live org-console/Cowork session")
    print("\nNEEDS-LIVE items are the hand-off checklist: they can only be")
    print("verified with org-admin console access plus a live Cowork run.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Token Optimizer Cowork adapter readiness.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    args = parser.parse_args(argv)
    checks = run_checks()
    if args.json:
        print(json.dumps({"checks": checks}, indent=2))
    else:
        _print_text(checks)
    return 1 if any(c["status"] == "FAIL" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
