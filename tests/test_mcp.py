"""python3 tests/test_mcp.py - setup/mcp.py rewrites the user's mcp.json: stale layouts migrate, customised ones stay."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "setup"))
import mcp

d = tempfile.mkdtemp()
p = os.path.join(d, "mcp.json")


def put(servers):
    json.dump({"mcpServers": servers}, open(p, "w"))


def get():
    return json.load(open(p))["mcpServers"]


# exact stale layout -> migrated, original kept once as a backup
put({"serena": {"command": "x", "args": ["start-mcp-server"], "exposure": "hidden", "toolExposure": dict(mcp.OLD_GENERATED)}})
mcp.cmd_migrate(p)
s = get()["serena"]
assert s["exposure"] == "codemode" and s["toolExposure"] == mcp.serena_exposure() and "--project-from-cwd" in s["args"]
assert "ddg" in get() and "semble" in get()
assert os.path.exists(p + ".pre-mcp-migration")

# a customised layout is left alone
custom = {"find_symbol": "direct", "*": "hidden"}
put({"serena": {"command": "x", "args": ["--project-from-cwd"], "exposure": "hidden", "toolExposure": custom}, "ddg": {}, "semble": {}})
mcp.cmd_migrate(p)
assert get()["serena"]["toolExposure"] == custom and get()["serena"]["exposure"] == "hidden"

# new writes pin every uvx tool (semble and ddg), so a re-pushed release cannot change what runs
assert all("==" in a for a in mcp.SEMBLE_ARGS[1:2] + mcp.DDG_ARGS[1:2])
print("ok")
