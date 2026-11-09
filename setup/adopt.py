#!/usr/bin/env python3
"""Install the harness into pi against an engine that is already running. Cross-platform (Linux, Windows, macOS):
setup.sh calls the sections below, setup.ps1 calls `all`. Stdlib only. Never installs or starts an engine.

  adopt.py extensions [--no-halogen]   copy extensions/ into pi (the NPU ones only with halogen); write the harness-repo marker
  adopt.py provider URL ENGINE         add a provider for a running engine (strata, gufo) to models.json, from its /v1/models
  adopt.py templates | skills | packages | agents
  adopt.py all [--engine strata|gufo] [--url URL]   everything above + mcp.json + image cap + sidecar off + engine doctor
"""
import filecmp, glob, hashlib, json, os, platform, shutil, subprocess, sys, tarfile, tempfile, urllib.request, zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_HOME = os.environ.get("PI_HOME") or os.path.join(os.path.expanduser("~"), ".pi")
AGENT = os.path.join(PI_HOME, "agent")
EXT_DIR = os.path.join(AGENT, "extensions")
NPU_EXTENSIONS = ("npu-retrieval.ts", "auto-guard.ts")  # need halogen on :8731

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # the Windows console codepage cannot print the check marks; lines stay in order with child output


def _say(color, mark, text):
    c, r = (f"\033[{color}m", "\033[0m") if sys.stdout.isatty() else ("", "")
    print(f"  {c}{mark}{r} {text}")


def ok(t): _say(32, "✓", t)
def warn(t): _say(33, "!", t)
def fail(t): _say(31, "✗", t)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default  # only a missing file starts empty: a parse error must stop, not overwrite the user's file


def write_json(path, data, indent):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)


def files_under(d):
    return sorted(os.path.relpath(os.path.join(dp, f), d) for dp, _, fs in os.walk(d) for f in fs)


def same_tree(a, b):
    """`diff -rq a b` is clean: same files, same bytes."""
    fa = files_under(a)
    return os.path.isdir(b) and fa == files_under(b) and all(filecmp.cmp(os.path.join(a, f), os.path.join(b, f), shallow=False) for f in fa)


def copy_tree(src, dst):
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def subdirs(parent):
    return sorted(d for d in glob.glob(os.path.join(parent, "*", "")) if os.path.isdir(d))


def pi_cmd():
    return shutil.which("pi")  # which() applies PATHEXT: pi may be pi.cmd on Windows


# ---------- sections ----------
def extensions(no_halogen=False):
    os.makedirs(EXT_DIR, exist_ok=True)
    with open(os.path.join(AGENT, "harness-repo"), "w", encoding="utf-8") as f:
        f.write(REPO + "\n")  # lets harness-status find scripts/usage-stats.py
    n = 0
    for src in sorted(glob.glob(os.path.join(REPO, "extensions", "*.ts"))):
        b = os.path.basename(src)
        if no_halogen and b in NPU_EXTENSIONS:
            continue
        dst = os.path.join(EXT_DIR, b)
        if not (os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False)):
            shutil.copy(src, dst)
            print(f"  installed {b}")
            n += 1
    for old in ("context-prune", "npu-triage", "ling-tiny-triage"):  # measured no-ops, deleted from the repo (extensions/README.md)
        p = os.path.join(EXT_DIR, old + ".ts")
        if os.path.isfile(p):
            os.remove(p)
            print(f"  removed {old}.ts (measured no-op)")
    for old, why in (("optional", "archive deleted from the repo"), ("plan-mode", "3 uses in 569 sessions")):
        p = os.path.join(EXT_DIR, old)
        if os.path.isdir(p):
            shutil.rmtree(p)
            print(f"  removed {old}/ ({why})")
    for d in subdirs(os.path.join(REPO, "extensions")):
        b = os.path.basename(os.path.normpath(d))
        if not same_tree(d, os.path.join(EXT_DIR, b)):
            copy_tree(d, os.path.join(EXT_DIR, b))
            print(f"  installed {b}/")
            n += 1
    if n:
        ok(f"installed/updated {n} extensions")
    else:
        ok(f"all extensions already current ({len(glob.glob(os.path.join(EXT_DIR, '*.ts')))} files + {len(subdirs(EXT_DIR))} dirs)")


def provider(url, eng):
    """models.json + settings.json for a running engine; asks its /v1/models for id, context and input types."""
    path = os.path.join(AGENT, "models.json")
    m = read_json(path, {})
    mid, ctx, inp = "qwen3.8-flash-next", 131072, ["text"]  # text-only guess when the server is down
    try:
        d = json.load(urllib.request.urlopen(url + "/v1/models", timeout=3))["data"][0]
        mid, ctx = d["id"], d.get("meta", {}).get("n_ctx") or d.get("context_length", ctx)  # gufo reports context_length
        inp = d.get("architecture", {}).get("input_modalities", inp)
    except Exception:
        print(f"  {eng} not answering - using defaults; re-run once it is up to pick up the real values")
    P = m.setdefault("providers", {})
    if eng in P:
        print(f"  {eng} provider exists - left untouched")
    else:
        P[eng] = {"baseUrl": url + "/v1", "api": "openai-completions", "apiKey": "dummy",  # set the key here if Strata runs with API_KEY
                  "models": [{"id": mid, "name": f"{eng} (local)", "reasoning": True, "input": inp,
                              "contextWindow": ctx, "maxTokens": 8192}]}
        print(f"  added provider: {eng}")
    print(f"  model id: {mid}")
    write_json(path, m, 1)
    sp = os.path.join(AGENT, "settings.json")
    st = read_json(sp, {})
    for k, v in (("defaultProvider", eng), ("defaultModel", mid)):
        st.setdefault(k, v)
    write_json(sp, st, 2)


def templates():
    prompts = os.path.join(AGENT, "prompts")
    os.makedirs(prompts, exist_ok=True)
    srcs = sorted(glob.glob(os.path.join(REPO, "templates", "*.md")))
    for f in srcs:  # pi reads agent/prompts/, never ~/.pi/templates: drop stale copies from older installs
        try:
            os.remove(os.path.join(PI_HOME, "templates", os.path.basename(f)))
        except OSError:
            pass
    try:
        os.rmdir(os.path.join(PI_HOME, "templates"))
    except OSError:
        pass
    n = 0
    for f in srcs:
        dst = os.path.join(prompts, os.path.basename(f))
        if not (os.path.isfile(dst) and filecmp.cmp(f, dst, shallow=False)):
            shutil.copy(f, dst)
            n += 1
    ok(f"prompt templates current ({n} updated, {len(os.listdir(prompts))} total)")
    # codebase search is the semble MCP server (mcp.json): verify its runtime is available
    if shutil.which("uvx"):
        ok("uvx found (semble + ddg MCP runnable)")
    else:
        warn("uvx not found - semantic search and web search (semble/ddg) unavailable; install uv (https://docs.astral.sh/uv)")


def skills():
    skill_dir = os.path.join(AGENT, "skills")
    os.makedirs(skill_dir, exist_ok=True)
    os.makedirs(os.path.join(AGENT, "themes"), exist_ok=True)
    theme, theme_dst = os.path.join(REPO, "config", "themes", "opencode.json"), os.path.join(AGENT, "themes", "opencode.json")
    if os.path.isfile(theme) and not os.path.exists(theme_dst):
        shutil.copy(theme, theme_dst)
        print('  installed theme: opencode (select via /settings, or set "theme": "opencode")')
    dup = os.path.join(skill_dir, "micro-interactions")  # ships in ~/.agents/skills (pi scans it natively): a pi-side copy collides
    if os.path.isdir(dup):
        shutil.rmtree(dup)
        print("  removed duplicate micro-interactions (native ~/.agents/skills copy is used)")
    n = 0
    for d in subdirs(os.path.join(REPO, "skills")):
        dst = os.path.join(skill_dir, os.path.basename(os.path.normpath(d)))
        if not same_tree(d, dst):
            copy_tree(d, dst)
            n += 1
    ok(f"installed/updated {n} skills" if n else f"skills current ({len(os.listdir(skill_dir))} dirs)")


PACKAGES = (("ponytail", "git:github.com/DietrichGebert/ponytail", "ponytail"),
            ("ask-user-question", "npm:@juicesharp/rpiv-ask-user-question", "ask-user-question"),  # the structured question tool is a package, not an extension
            ("pi-rtk-optimizer", "npm:@estebanforge/pi-rtk-optimizer", "pi-rtk-optimizer"))  # rewrites bash commands through the rtk binary
RTK_VERSION = (1, 0, 1)
RTK_ASSETS = {  # pinned sha256: the binary lands on PATH, so a swapped release asset must not install
    ("linux", "x86_64"): ("x86_64-unknown-linux-musl.tar.gz", "5b919c3bccb799b67a61866d0e617410a4fcdf173e2614440e59de2fa2003436"),
    ("linux", "aarch64"): ("aarch64-unknown-linux-gnu.tar.gz", "cd627c4091ad90b5e5aca83ca5af66ee4b8aa8537a736533fa90544f461f1c2b"),
    ("windows", "x86_64"): ("x86_64-pc-windows-msvc.zip", "64b4807c50b61dddf2ac1e86751dbda9117ab94ed02bf266e6584da571c39896"),
}


def packages():
    pi = pi_cmd()
    if not pi:
        return
    have = " ".join(read_json(os.path.join(AGENT, "settings.json"), {}).get("packages", []))
    for key, spec, label in PACKAGES:
        if key in have:
            ok(f"{label} package present")
        elif subprocess.run([pi, "install", spec], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            ok(f"{label} installed (pi package)")
        else:
            warn(f"{label} install failed - run: pi install {spec}")
    os.makedirs(os.path.join(EXT_DIR, "pi-rtk-optimizer"), exist_ok=True)
    cfg = os.path.join(EXT_DIR, "pi-rtk-optimizer", "config.json")
    if not os.path.isfile(cfg):
        shutil.copy(os.path.join(REPO, "config", "rtk", "pi-rtk-optimizer.json"), cfg)
    # shortcut: rtk's own config.toml (git diff/cat/head/tail left alone) is only placed on Linux/macOS; its Windows location is unverified
    if platform.system() != "Windows":
        toml = os.path.join(os.path.expanduser("~"), ".config", "rtk", "config.toml")
        if not os.path.isfile(toml):
            os.makedirs(os.path.dirname(toml), exist_ok=True)
            shutil.copy(os.path.join(REPO, "config", "rtk", "config.toml"), toml)
    rtk_binary()


def rtk_version():
    try:
        out = subprocess.run(["rtk", "--version"], capture_output=True, text=True).stdout.split()
        return tuple(int(x) for x in out[1].split(".")), out[1]
    except Exception:
        return None, ""


def rtk_binary():
    """rtk < 1.0.1 mangles single-file grep output (reads the line number as a filename): install the pinned release."""
    have, shown = rtk_version()
    if have and have >= RTK_VERSION:
        ok(f"rtk {shown}")
    else:
        sysname, arch = platform.system().lower(), platform.machine().lower().replace("amd64", "x86_64").replace("arm64", "aarch64")
        asset = RTK_ASSETS.get((sysname, arch))
        bindir = os.path.join(os.path.expanduser("~"), ".local", "bin")
        exe = "rtk.exe" if sysname == "windows" else "rtk"
        try:
            if not asset:
                raise ValueError("no pinned build for this platform")
            with tempfile.TemporaryDirectory() as tmp:
                arc = os.path.join(tmp, asset[0])
                url = "https://github.com/rtk-ai/rtk/releases/download/v%d.%d.%d/rtk-%s" % (*RTK_VERSION, asset[0])
                with urllib.request.urlopen(url, timeout=60) as r, open(arc, "wb") as f:
                    shutil.copyfileobj(r, f)
                if hashlib.sha256(open(arc, "rb").read()).hexdigest() != asset[1]:
                    raise ValueError("checksum mismatch")
                data = zipfile.ZipFile(arc).read(exe) if arc.endswith(".zip") else tarfile.open(arc).extractfile(exe).read()
                os.makedirs(bindir, exist_ok=True)
                with open(os.path.join(bindir, exe), "wb") as f:
                    f.write(data)
                os.chmod(os.path.join(bindir, exe), 0o755)
            ok("rtk %d.%d.%d installed to %s (was: %s); make sure it is on PATH" % (*RTK_VERSION, bindir, shown or "none"))
        except Exception:
            warn("rtk %d.%d.%d download failed - install it from https://github.com/rtk-ai/rtk/releases" % RTK_VERSION)
    if not shutil.which("rtk"):
        warn("rtk not on PATH - add ~/.local/bin to PATH in your shell profile, or pi-rtk-optimizer warns 'rtk binary unavailable'")


def agents():
    """User-owned: write only when absent, never append to or overwrite an existing one."""
    dst = os.path.join(AGENT, "AGENTS.md")
    if os.path.isfile(dst):
        ok(f"AGENTS.md exists (left untouched; see {os.path.join(REPO, 'config', 'AGENTS.md')} for semble/serena guidance to merge)")
    elif os.path.isdir(AGENT):
        shutil.copy(os.path.join(REPO, "config", "AGENTS.md"), dst)
        ok("installed AGENTS.md (semble/serena guidance)")


# ---------- all: what setup.sh --strata does, for a box without bash ----------
def mcp():
    path = os.path.join(AGENT, "mcp.json")
    verb = "migrate" if os.path.isfile(path) else "write"
    if verb == "write" and not pi_cmd():
        return warn("pi not found - skipped mcp.json")
    if subprocess.run([sys.executable, os.path.join(REPO, "setup", "mcp.py"), verb, path]).returncode:
        warn(f"could not update {path} (left untouched)")
    else:
        ok("MCP servers configured (pi mcp list to inspect)" if verb == "write" else "mcp.json exists (serena/semble migrated, ddg added when missing)")


def git_bash():
    """pi's bash tool on native Windows needs a Git Bash (docs: windows.md); every guard in extensions/ reads bash commands."""
    if platform.system() != "Windows":
        return True
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)")]
    return any(os.path.isfile(os.path.join(p, "Git", "bin", "bash.exe")) for p in pf if p) or bool(shutil.which("bash"))


def run_all(eng, url):
    print("== 1. pi + extensions ==")
    if pi_cmd():
        ok("pi found")
    else:
        fail("pi coding agent not found on PATH - install it first (https://pi.dev), then re-run")
        return 1
    if not git_bash():
        warn("Git for Windows (bash) not found: pi's bash tool and every guard here need it - winget install Git.Git")
    extensions(no_halogen=True)
    pref = os.path.join(AGENT, "harness-sidecar")  # the ling-tiny sidecar is Linux/systemd: off unless the user chose otherwise
    if not os.path.exists(pref):
        with open(pref, "w") as f:
            f.write("0\n")
    print("== 2. pi providers (models.json) ==")
    provider(url, eng)
    try:
        urllib.request.urlopen(url + "/v1/models", timeout=3)
        ok(f"{eng} up ({url})")
    except Exception:
        warn(f"{eng} not answering at {url} - start it before using pi")
    if subprocess.run([sys.executable, os.path.join(REPO, "scripts", "set-image-limits.py")]).returncode:
        warn("image cap not applied (PI_IMAGE_MAX=off skips it)")
    print("== 2a-2. prompt templates ==")
    templates()
    print("== 2a. skills + pi packages ==")
    skills()
    packages()
    print("== 2b. MCP servers (mcp.json) ==")
    mcp()
    print("== 2c. global AGENTS.md ==")
    agents()
    if eng == "strata":
        print("== 3. strata checks ==")
        sys.path.insert(0, os.path.join(REPO, "setup"))
        import strata_doctor
        for lvl, txt in strata_doctor.check(url):
            (ok if lvl == "ok" else warn)(txt)
    print(f"\ndone.\nuse it:  pi --provider {eng} --model <model id above>   (no NPU tools on {eng}, see docs/ENGINES.md)")
    return 0


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "extensions":
        extensions("--no-halogen" in a)
    elif cmd == "provider" and len(a) == 3:
        provider(a[1], a[2])
    elif cmd in ("templates", "skills", "packages", "agents"):
        globals()[cmd]()
    elif cmd == "all":
        opt = lambda k, d: a[a.index(k) + 1] if k in a else d
        return run_all(opt("--engine", "strata"), opt("--url", "http://127.0.0.1:8080").rstrip("/"))
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
