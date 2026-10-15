#!/usr/bin/env python3
"""usage-stats.py — how often MCP servers, skills, extension tools and built-ins get used.
  ./scripts/usage-stats.py [session.jsonl|session-id]   one session (default: newest)
  ./scripts/usage-stats.py --all                        lifetime, all sessions
  ./scripts/usage-stats.py --json                       same data as one JSON object
  ./scripts/usage-stats.py --top N                      rows per section (default 8)
Sources: toolCall blocks in ~/.pi/agent/sessions, ~/.pi/agent/harness-events.jsonl (sidecar/NPU hooks).
Skills = reads of */SKILL.md. MCP = direct mcp__server__tool calls plus tools.mcp__... inside codemode.
Installed-but-never-used MCP servers and skills are listed so dead weight is visible.
"""
import collections, glob, json, os, re, sys

A = os.path.expanduser("~/.pi/agent")
BUILTIN = {"bash", "read", "write", "edit", "grep", "find", "ls"}
argv = sys.argv[1:]
allm = "--all" in argv
json_out = "--json" in argv
argv = [a for a in argv if a != "--json"]
top = 8
if "--top" in argv:
    i = argv.index("--top"); top = int(argv[i + 1]); del argv[i:i + 2]
args = [a for a in argv if a != "--all"]
files = sorted(glob.glob(f"{A}/sessions/*/*.jsonl"), key=os.path.getmtime)
if not allm:
    files = ([f for f in files if args[0] in f] or ([args[0]] if os.path.isfile(args[0]) else [])) if args else files[-1:]
if not files: sys.exit("no session found")

tty = sys.stdout.isatty()
def col(code, s): return f"\033[{code}m{s}\033[0m" if tty else s
dim, bold, grn = (lambda s: col(2, s)), (lambda s: col(1, s)), (lambda s: col(32, s))

mcp, skills, ext, builtin = (collections.Counter() for _ in range(4))
first = last = ""
daily = collections.Counter()
for f in files:
    for line in open(f):
        try: d = json.loads(line)
        except ValueError: continue
        t = d.get("timestamp", "") if isinstance(d.get("timestamp"), str) else ""
        if t: first, last = first or t, max(last, t)
        m = d.get("message", {})
        if m.get("role") != "assistant": continue
        for c in m.get("content", []):
            if c.get("type") != "toolCall": continue
            daily[t[:10]] += 1
            n, a = c["name"], c.get("arguments", {})
            if mm := re.match(r"mcp__(.+?)__(.+)", n): mcp[f"{mm[1]}/{mm[2]}"] += 1
            elif n.lower() in BUILTIN: builtin[n.lower()] += 1
            else: ext[n] += 1
            if n == "codemode":
                for s, t2 in re.findall(r"tools\.mcp__(\w+?)__(\w+)", a.get("code", "")): mcp[f"{s}/{t2}"] += 1
            p = str(a.get("path", ""))
            if n.lower() == "read" and p.endswith("SKILL.md"): skills[os.path.basename(os.path.dirname(p))] += 1

hooks = collections.Counter()
try:
    for line in open(f"{A}/harness-events.jsonl"):
        try: e = json.loads(line)
        except ValueError: continue
        if allm or (first[:19] <= e.get("t", "") <= last[:19] + "~"): hooks[e.get("c", "?") + (":" + e["k"] if e.get("k") else "")] += 1
except OSError: pass

# installed inventory, for the "never used" lines
try: configured = set(json.load(open(f"{A}/mcp.json")).get("mcpServers", {}))
except (OSError, ValueError): configured = set()
installed_skills = {os.path.basename(os.path.dirname(p)) for r in (f"{A}/skills", os.path.expanduser("~/.agents/skills"), f"{A}/git")
                    for p in glob.glob(f"{r}/**/SKILL.md", recursive=True)}

if json_out:
    srv = collections.Counter()
    for k, v in mcp.items(): srv[k.split("/")[0]] += v
    print(json.dumps({"sessions": len(files), "first": first, "last": last, "mcp_servers": srv, "mcp_tools": mcp,
                      "skills": skills, "skills_unused": sorted(installed_skills - set(skills)),
                      "mcp_unused": sorted(configured - set(srv)), "extension_tools": ext, "hooks": hooks, "builtins": builtin, "extensions_silent": silent, "tool_calls_per_day": dict(zip(days, vals))}, indent=1))
    sys.exit()

# extensions with no logged trace: stem tokens vs hook/tool names. shortcut: heuristic, extensions that never log (npu-retrieval, turn-timer...) show up here even when active.
seen = " ".join(list(hooks) + list(ext)).lower()
silent = sorted(os.path.basename(f)[:-3] for f in glob.glob(f"{A}/extensions/*.ts")
                if not any(t in seen for t in os.path.basename(f)[:-3].split("-") if len(t) > 3 and t not in ("tiny", "ling")))
import datetime
today = datetime.date.today()
days = [(today - datetime.timedelta(d)).isoformat() for d in range(13, -1, -1)]
vals = [daily[d] for d in days]
spark = "".join(" ▁▂▃▄▅▆▇█"[round(8 * v / max(vals))] if max(vals) else " " for v in vals)

def show(title, c, unused=(), label="never used"):
    total = sum(c.values())
    print(f"\n{bold(title)} {dim(f'({total})')}")
    rows = c.most_common(top)
    w = max([len(k) for k, _ in rows] + [1]); mx = rows[0][1] if rows else 1
    for k, v in rows:
        print(f"  {k:<{w}}  {v:>6}  {grn('█' * max(1, round(18 * v / mx)))}")
    if len(c) > top: print(dim(f"  … +{len(c) - top} more (--top {len(c)})"))
    if not c: print(dim("  none"))
    if unused: print(dim(f"  {label}: " + ", ".join(sorted(unused))))

span = f"{first[:10]} → {last[:10]}" if first else ""
print(bold("usage-stats"), dim(f"lifetime · {len(files)} sessions · {span}" if allm else f"{os.path.basename(files[0])} · {span}"))
srv = collections.Counter()
for k, v in mcp.items(): srv[k.split("/")[0]] += v
show("MCP servers", srv, configured - set(srv))
show("MCP tools", mcp)
show("Skills read", skills, installed_skills - set(skills))
show("Extension tools", ext)
show("Extension hooks", hooks, silent, "no logged activity")
show("Built-ins", builtin)
if allm: print(f"\n{bold('Tool calls, last 14 days')} {spark} {dim(f'peak {max(vals)}/day')}")
