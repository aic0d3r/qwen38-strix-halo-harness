#!/usr/bin/env python3
"""Acceptance matrix: every engine x weights combination this box can run, one after another.

For each: stop the running big engine, start the combination with its own launcher, then run
scripts/engine-acceptance.py (the model answers, decodes fast enough) and bench/refusal-canary.py
(refusals out of 19). Combinations whose weights are not on disk are skipped with the reason, never
downloaded. The engine that was running at the start is restarted at the end.

  scripts/engine-matrix.py [--engines halogen,gufo,strata] [--weights stock,abliterated]
                           [--dry-run] [--no-restore] [--min-tps 15]
--dry-run prints which combinations are runnable and why the others are not. Takes minutes per engine
(model load dominates); prints a markdown table to paste into docs/ENGINES.md. Exit 1 if a run failed.
"""
import os, re, shutil, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "setup"))
import harness as h  # noqa: E402

ENV = {"halogen": "HALOGEN_WEIGHTS", "gufo": "GUFO_WEIGHTS", "strata": "STRATA_WEIGHTS"}
os.environ.update(GUFO_FAMILY="flash-next", STRATA_MODEL="flash-next")  # compares Flash-Next weights, whatever model the wizard saved


def arg(name, default):
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else default


def sh(*cmd, **env):
    r = subprocess.run(["bash", *cmd], capture_output=True, text=True, env=dict(os.environ, **env))
    return r.stdout.strip()


def runnable(engine, w):
    """-> '' when this combination can start from what is on disk, else why not."""
    var = {ENV[engine]: w}
    if engine == "halogen":
        f = sh(os.path.join(REPO, "server/start-halogen.sh"), "--print-ckpt", **var)
        return "" if os.path.isfile(f) else f"checkpoint missing: {f}"
    if engine == "gufo":
        f = sh(os.path.join(REPO, "server/start-gufo-flashnext.sh"), "--print-model", **var)
        return "" if os.path.isfile(f) else f"weights missing: {os.path.basename(f)}"
    return "" if sh(os.path.join(REPO, "server/start-strata.sh"), "--print-run", **var) else "Strata not installed for these weights"


def one(engine, w, min_tps):
    """Start engine on weights w, run the checks. -> result dict."""
    running = {x["engine"]: x for x in h.detect()["engines"]}
    for e in h.BIG:  # one big engine at a time, including a restart of the same one onto other weights
        if e in running and not h.stop_engine(e, running):
            return {"note": f"could not stop {e}"}
    if engine in ENV:
        os.environ[ENV[engine]] = w
    t0 = time.time()
    ok = h.start_engine(engine, {}, {"stop": None, "download": False, "weights": None}, wait_s=1500)
    if engine in ENV:
        os.environ.pop(ENV[engine], None)
    if not ok:
        return {"note": f"did not start (see /tmp/harness-{engine}.log)"}
    res = {"load_s": int(time.time() - t0)}
    port = h.PROFILES["engines"][engine]["port"]
    url, model = f"http://127.0.0.1:{port}", h.served_models(port, engine)[0]
    a = subprocess.run([sys.executable, os.path.join(REPO, "scripts/engine-acceptance.py"), url, str(min_tps)], capture_output=True, text=True)
    m = re.search(r"= ([\d.]+) t/s", a.stdout)
    res["accept"] = ("pass" if a.returncode == 0 else "FAIL") + (f" {m.group(1)} t/s" if m else "")
    c = subprocess.run([sys.executable, os.path.join(REPO, "bench/refusal-canary.py"), "--base", url, "--model", model, "--weights", w], capture_output=True, text=True)
    m = re.search(r"refusals: (\d+)/(\d+)", c.stdout)
    res["refusals"] = f"{m.group(1)}/19" if m and not re.search(r"unanswered.*: [1-9]", c.stdout) else (f"{m.group(1)}/{m.group(2)} (+unanswered)" if m else "n/a")
    res["ok"] = a.returncode == 0 and c.returncode in (0, 1)  # refusals are a measurement, not a failure
    return res


def main():
    engines = arg("--engines", "halogen,gufo,strata").split(",")
    weights = arg("--weights", "stock,abliterated").split(",")
    min_tps = float(arg("--min-tps", "15"))
    plan = [(e, w, runnable(e, w)) for e in engines for w in weights]
    for e, w, why in plan:
        print(f"  {e:9} {w:12} {'runnable' if not why else 'skip: ' + why}")
    if "--dry-run" in sys.argv:
        return 0
    start = [x["engine"] for x in h.detect()["engines"] if x["engine"] in h.BIG]
    rows, failed = [], False
    for e, w, why in plan:
        if why:
            rows.append((e, w, {"note": "skipped: " + why}))
            continue
        print(f"\n== {e} / {w} ==", flush=True)
        r = one(e, w, min_tps)
        failed |= not r.get("ok", False)
        rows.append((e, w, r))
        print(f"   {r}", flush=True)
    if start and "--no-restore" not in sys.argv:
        print(f"\nrestoring {start[0]} on the saved weights ({h.weights_pref()})", flush=True)
        for x in h.detect()["engines"]:
            if x["engine"] in h.BIG:
                h.stop_engine(x["engine"], {x["engine"]: x})
        h.start_engine(start[0], {}, {"stop": None, "download": False, "weights": None}, wait_s=1500)
    print(f"\n| engine | weights | load | acceptance (decode) | refusals |\n|---|---|---|---|---|")
    for e, w, r in rows:
        print(f"| {e} | {w} | {r['load_s']} s | {r['accept']} | {r['refusals']} |" if "accept" in r else f"| {e} | {w} | - | {r.get('note', '?')} | - |")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
