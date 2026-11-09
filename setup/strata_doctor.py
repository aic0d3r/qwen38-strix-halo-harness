"""Strata diagnosis, stdlib only so Linux (setup.sh --doctor, start_engine) and Windows (setup.ps1) share it.
  strata_doctor.py check [--url http://127.0.0.1:8080]   prints "ok|warn|fail <text>" lines
  strata_doctor.py explain [FILE]                        known Strata failure text (stdin or FILE) -> what it means
Advice only states what the Strata issue tracker confirmed; where no workaround is known it says so."""
import json, os, re, sys, urllib.error, urllib.request

FEED = "https://github.com/Niko1221/Strata/releases/latest/download/install.sh"
AGENT = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(os.environ.get("PI_HOME", os.path.expanduser("~/.pi")), "agent")
LOGS = ("/tmp/harness-strata.log", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Strata", "components", ".updates", "state.json"))

RULES = [
    (r"3221225785|0xC0000139|STATUS_ENTRYPOINT_NOT_FOUND",
     "the engine loaded Windows' own amdhip64_7.dll (older AMD driver copy) instead of the one bundled with it - Strata issue #5542, "
     "seen on Strix Halo with engines 0.1.57/0.1.58. No fix is confirmed yet. Strata keeps your previous engine when a new one fails "
     "its test start, so it keeps working on that; update the AMD driver and retry after the next Strata release"),
    (r"WinError 4551|WinError 1260|Smart App Control|application control policy",
     "Windows blocked the engine program: Smart App Control (or another application-control policy) refuses unsigned files (Strata issue #5619). "
     "no Strata workaround is listed; turning Smart App Control off (Windows Security > App & browser control) is your call - check Microsoft's note that it may not turn back on"),
    (r"(\d+) MiB needed, (\d+) MiB free after the weights",
     None),  # filled by vram()
    (r"ignoring --vram-reserve-mib .*not an option of this engine",
     "this engine ignores 'GPU memory for other apps' (--vram-reserve-mib), so changing it cannot help; "
     "lower the context, the slots or the layer split instead (Strata issue #5533)"),
    (r"0xc0000409",
     "the Strata app host crashed at startup; a skill folder with non-ASCII characters in its name is a known cause (Strata issue #5547)"),
]


def vram(m):
    need, free = int(m.group(1)), int(m.group(2))
    return (f"short by {need - free} MiB of GPU memory ({need} needed, {free} free after weights): "
            "use fewer slots, a shorter context or Int8/Q4 context memory")


def explain(text):
    """-> one line per known failure found in text."""
    out = []
    for pat, msg in RULES:
        m = re.search(pat, text or "", re.I)
        if m:
            out.append(msg or vram(m))
    return out


def get(url, timeout=3):
    return urllib.request.urlopen(url, timeout=timeout)


def feed_status(url=FEED):
    """-> an error line when the install feed answers with an HTTP error; None when fine or offline (offline is not Strata's fault)."""
    try:
        get(urllib.request.Request(url, method="HEAD"), 8)
    except urllib.error.HTTPError as e:
        return (f"Strata's install feed answers HTTP {e.code} ({url}); the install script then waits silently (Strata issue #5611). "
                "Point it at a release instead: STRATA_FEED=https://github.com/Niko1221/Strata/releases/download/<tag> (e.g. v0.2.12)")
    except Exception:
        return None


def served(url):
    return json.load(get(url.rstrip("/") + "/v1/models"))["data"]


def provider_ids():
    try:
        return [m["id"] for m in json.load(open(os.path.join(AGENT, "models.json")))["providers"]["strata"]["models"]]
    except Exception:
        return []


def check(url):
    """-> [(level, text)]"""
    out, ids = [], []
    try:
        data = served(url)
        ids = [m["id"] for m in data]
        other = next((m["owned_by"] for m in data if m.get("owned_by") in ("gufo", "llamacpp")), None)
        out.append(("warn", f"{other} answers on {url}, not Strata (they share :8080; one at a time)") if other else ("ok", f"Strata answers on {url}: {', '.join(ids) or 'no model'}"))
        if other:
            ids = []  # the model-id comparison below is only about Strata
    except Exception:
        out.append(("warn", f"nothing answers on {url}"))
        for f in LOGS:  # why it is down: the start log (Linux) or the engine-update state (Windows)
            try:
                for line in explain(open(f, errors="replace").read()[-20000:]):
                    out.append(("warn", line))
            except OSError:
                pass
    for pid in provider_ids():
        if ids and pid not in ids:
            out.append(("warn", f"pi's strata provider uses model id '{pid}' but Strata serves {ids}: pi gets a 404 until they match (rerun ./setup.sh --strata, or edit models.json)"))
    f = feed_status()
    if f:
        out.append(("warn", f))
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["check"]:
        for lvl, txt in check(a[a.index("--url") + 1] if "--url" in a else "http://127.0.0.1:8080"):
            print(lvl, txt)
    elif a[:1] == ["explain"]:
        print("\n".join(explain(open(a[1]).read() if a[1:] else sys.stdin.read())))
    else:
        sys.exit(__doc__)
