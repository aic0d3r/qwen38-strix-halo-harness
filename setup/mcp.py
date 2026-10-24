#!/usr/bin/env python3
"""Canonical pi MCP configuration: fresh write, stale-layout migration, explicit refresh, doctor check.

  python3 setup/mcp.py write    PATH   # no mcp.json yet: write serena + ddg + jina + semble
  python3 setup/mcp.py migrate  PATH   # existing: add --project-from-cwd/ddg/semble; upgrade an exact stale layout
  python3 setup/mcp.py refresh  PATH   # existing: reset serena/semble exposure and ensure ddg (backup first)
  python3 setup/mcp.py check    PATH   # print drift lines for --doctor, or nothing

Stdlib only. Only exact-match stale layouts are migrated; customized exposure is left alone.
"""
import json
import os
import shutil
import sys

SERENA_COMMAND = "~/.local/bin/serena"
SERENA_ARGS = ["start-mcp-server", "--project-from-cwd"]
SERENA_DESC = "Serena LSP toolbox: symbol-precise code navigation, diagnostics, and edits (activate_project first)"

CORE_DIRECT = {
    "activate_project": "direct",
    "find_symbol": "direct",
    "get_symbols_overview": "direct",
    "find_referencing_symbols": "direct",
    "search_for_pattern": "direct",
    "get_diagnostics_for_file": "direct",
    "replace_symbol_body": "direct",
    "insert_after_symbol": "direct",
    "rename_symbol": "direct",
}
HIDDEN = {
    "safe_delete_symbol": "hidden",
    "delete_memory": "hidden",
    "rename_memory": "hidden",
    "edit_memory": "hidden",
    "execute_shell_command": "hidden",
    "create_text_file": "hidden",
    "read_file": "hidden",
    "list_dir": "hidden",
    "find_file": "hidden",
    "open_dashboard": "hidden",
    "get_current_config": "hidden",
    "onboarding": "hidden",
    "initial_instructions": "hidden",
}

SEMBLE_COMMAND = "uvx"
SEMBLE_ARGS = ["--from", "semble[mcp]==0.6.2", "semble"]
SEMBLE_DESC = ("Semble instant semantic code search (local or remote git repos; replaced the NPU rag "
               "pipeline - A/B 17/18 vs 13/18, 2026-10-10)")
SEMBLE_EXPOSURE = {"search": "direct", "find_related": "direct", "*": "hidden"}

DDG_COMMAND = "uvx"
DDG_ARGS = ["--with", "duckduckgo-mcp-server[browser]", "duckduckgo-mcp-server"]
DDG_DESC = "DuckDuckGo web search + page reading (no API key; lightweight Jina alternative)"
DDG_EXPOSURE = {"search": "direct", "fetch_content": "direct", "expand_link": "direct", "*": "hidden"}
DDG_ENV = {"DDG_PARSE_MODE": "markdown"}

JINA_DESC = "Jina web search and page reading (enable via /mcp; needs the key file)"
JINA_KEY = "~/.config/opencode/jina.key"


def serena_exposure():
    """Shipped serena map: 9 core tools direct, destructive/duplicate tools hidden, rest script-only."""
    return {**CORE_DIRECT, **HIDDEN, "*": "codemode"}


# Exact layouts written by older setup.sh / config examples. Anything else is user customization.
OLD_GENERATED = {**CORE_DIRECT, "*": "hidden"}
OLD_16 = {
    "activate_project": "direct",
    "find_symbol": "direct",
    "get_symbols_overview": "direct",
    "find_referencing_symbols": "direct",
    "search_for_pattern": "direct",
    "get_diagnostics_for_file": "direct",
    "replace_symbol_body": "direct",
    "insert_after_symbol": "direct",
    "rename_symbol": "direct",
    "onboarding": "direct",
    "list_memories": "direct",
    "read_memory": "direct",
    "write_memory": "direct",
    "replace_content": "direct",
    "find_declaration": "direct",
    "find_implementations": "direct",
    "*": "hidden",
}


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def backup_once(path, suffix):
    """Keep the pre-change file; never overwrite the first backup on later runs."""
    bak = path + suffix
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


def semble_server():
    return {
        "command": SEMBLE_COMMAND,
        "args": list(SEMBLE_ARGS),
        "exposure": "hidden",
        "toolExposure": dict(SEMBLE_EXPOSURE),
        "description": SEMBLE_DESC,
    }


def ddg_server():
    return {
        "command": DDG_COMMAND,
        "args": list(DDG_ARGS),
        "enabled": True,
        "exposure": "hidden",
        "toolExposure": dict(DDG_EXPOSURE),
        "env": dict(DDG_ENV),
        "description": DDG_DESC,
    }


def canonical_servers():
    servers = {
        "serena": {
            "command": SERENA_COMMAND,
            "args": list(SERENA_ARGS),
            "exposure": "codemode",
            "toolExposure": serena_exposure(),
            "description": SERENA_DESC,
        },
        "ddg": ddg_server(),
        "jina": {
            "url": "https://mcp.jina.ai/v1",
            "headers": {"Authorization": "!echo Bearer $(cat %s)" % JINA_KEY},
            "enabled": False,
            "description": JINA_DESC,
        },
        "semble": semble_server(),
    }
    if not os.path.exists(os.path.expanduser(JINA_KEY)):
        servers["jina"]["headers"] = {"Authorization": "${JINA_API_KEY}"}
    return servers


def cmd_write(path):
    save(path, {"mcpServers": canonical_servers()})
    print("  wrote mcp.json (serena direct x9, ddg direct x3, jina disabled, semble direct x2)")


def cmd_migrate(path):
    m = load(path)
    servers = m.setdefault("mcpServers", {})
    changed = False

    s = servers.get("serena")
    if s is not None:
        args = s.setdefault("args", [])
        if "--project-from-cwd" not in args:
            args.append("--project-from-cwd")
            changed = True
            print("  serena: added --project-from-cwd (auto-activates the cwd project)")
        te = s.get("toolExposure")
        if s.get("exposure") == "hidden" and (te == OLD_GENERATED or te == OLD_16):
            s["exposure"] = "codemode"
            s["toolExposure"] = serena_exposure()
            changed = True
            print("  serena: migrated stale %d-direct hidden layout to 9 direct + codemode tail" % (len(te) - 1))

    if "ddg" not in servers:
        servers["ddg"] = ddg_server()
        changed = True
        print("  ddg: added to existing mcp.json (search + fetch, no key)")

    if "semble" not in servers:
        servers["semble"] = semble_server()
        changed = True
        print("  semble: added to existing mcp.json")

    if changed:
        backup_once(path, ".pre-mcp-migration")
        save(path, m)


def cmd_refresh(path):
    m = load(path)
    servers = m.setdefault("mcpServers", {})
    bak = backup_once(path, ".pre-refresh")

    if "ddg" not in servers:
        servers["ddg"] = ddg_server()
        print("  ddg: added to existing mcp.json (search + fetch, no key)")

    s = servers.setdefault("serena", {})
    s.setdefault("command", SERENA_COMMAND)
    args = s.setdefault("args", ["start-mcp-server"])
    if "--project-from-cwd" not in args:
        args.append("--project-from-cwd")
    s["exposure"] = "codemode"
    s["toolExposure"] = serena_exposure()
    s.setdefault("description", SERENA_DESC)

    sm = servers.setdefault("semble", {})
    sm.setdefault("command", SEMBLE_COMMAND)
    sm.setdefault("args", list(SEMBLE_ARGS))
    sm["exposure"] = "hidden"
    sm["toolExposure"] = dict(SEMBLE_EXPOSURE)
    sm.setdefault("description", SEMBLE_DESC)

    save(path, m)
    print("  refreshed serena + semble exposure; ddg ensured (backup: %s)" % bak)


def cmd_check(path):
    try:
        servers = load(path).get("mcpServers", {})
    except Exception as e:
        print("mcp.json unreadable (%s) - fix it or re-run ./setup.sh" % e)
        return

    s = servers.get("serena")
    if s is None:
        print("serena MCP server missing - ./setup.sh --refresh-mcp")
    else:
        te = s.get("toolExposure") or {}
        direct = sum(1 for v in te.values() if v == "direct")
        if s.get("exposure") == "hidden" and (te == OLD_GENERATED or te == OLD_16):
            print("serena MCP uses the old %d-direct hidden layout - ./setup.sh migrates it (or --refresh-mcp)" % direct)
        elif te.get("*") != "codemode" or direct != len(CORE_DIRECT):
            print("serena MCP exposure differs from the shipped config (%d direct, %s tail) - ./setup.sh --refresh-mcp"
                  % (direct, te.get("*") or "no wildcard"))
    if "ddg" not in servers:
        print("ddg MCP server missing - ./setup.sh")
    if "semble" not in servers:
        print("semble MCP server missing - ./setup.sh")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("write", "migrate", "refresh", "check"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cmd, path = sys.argv[1], sys.argv[2]
    try:
        globals()["cmd_" + cmd](path)
    except Exception as e:
        print("  mcp.py %s failed: %s" % (cmd, e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
