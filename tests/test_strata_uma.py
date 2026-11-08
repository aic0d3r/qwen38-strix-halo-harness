"""scripts/strata-uma.py: budget-mode config -> all-in-pool config on a Strix Halo where the experts fit; untouched otherwise."""
import importlib.util, json, os, sys, tempfile, types

spec = importlib.util.spec_from_file_location("uma", os.path.join(os.path.dirname(__file__), "..", "scripts", "strata-uma.py"))
uma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uma)
sys.modules["strata_control"] = types.SimpleNamespace(uma_pool=types.SimpleNamespace(FIXED_GIB=16.5, MARGIN_GIB=1.0))
uma.experts = lambda g: (71.7, 24576)
uma.ram_gib = lambda: 125.0

d = tempfile.mkdtemp()
p = os.path.join(d, "strata-x.json")
budget = {"backend": "hip", "env": {"STRATA_PF_FUSED": "0"},
          "args": ["--native", "x.gguf", "--expert-cache", "8703", "--prefill", "auto", "--ple-io", "ram", "--resident-budget-gib", "34"]}


def run(pool):
    json.dump(budget, open(p, "w"))
    uma.strix_halo_pool_gib = lambda: pool
    sys.argv = ["strata-uma.py", p]
    assert uma.main() == 0
    return json.load(open(p))


c = run(124.5)
a = c["args"]
assert "--resident-budget-gib" not in a and "--mmap-experts" in a and "--no-prefill-borrow" in a, a
assert a[a.index("--expert-cache") + 1] == "24576" and a[a.index("--ple-io") + 1] == "mmap" and a[a.index("--prefill") + 1] == "auto:16384", a
assert c["env"]["STRATA_PF_FUSED"] == "0" and c["env"]["STRATA_IGPU_PROMPT_SET"] == "1", c["env"]  # a hand-set value stays
assert json.load(open(p + ".budget-mode")) == budget  # setup's original kept
before = open(p).read()
sys.argv = ["strata-uma.py", p]
assert uma.main() == 0 and open(p).read() == before  # idempotent
os.remove(p + ".budget-mode")
assert run(64.0) == budget  # a smaller pool (64 GB box): setup's budget mode stays
assert run(0.0) == budget   # not a Strix Halo
print("ok")
