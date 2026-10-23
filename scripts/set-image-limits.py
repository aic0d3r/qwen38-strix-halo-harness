#!/usr/bin/env python3
# Cap image resolution on every image-capable model pi lists, so screenshots cost fewer tokens
# (Claude ~1,500 -> ~600 per 1080x2400 phone screenshot at 450x1000; pi's image note keeps the
# coordinate multiplier, so taps still map back). pi has no global setting, only per-model
# inputLimits, so setup.sh re-runs this to cover models pi added since.
# Usage: scripts/set-image-limits.py [WxH]   (default PI_IMAGE_MAX or 450x1000; "off" skips)
import json, os, pathlib, shutil, subprocess, sys

spec = (sys.argv[1:2] or [os.environ.get("PI_IMAGE_MAX", "450x1000")])[0]
if spec == "off":
    sys.exit(0)
W, H = (int(v) for v in spec.lower().split("x"))
path = pathlib.Path(os.environ.get("PI_HOME", pathlib.Path.home() / ".pi")) / "agent/models.json"
limits = {"images": {"resize": {"maxWidth": W, "maxHeight": H}}}

rows = subprocess.run(["pi", "--list-models"], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
models = [(r.split()[0], r.split()[1]) for r in rows if len(r.split()) > 2 and r.split()[-1] == "yes"]
if not models:
    sys.exit("pi --list-models returned no image-capable models - left models.json untouched")

# a parse error must stop here, not overwrite the user's providers
data = json.loads(path.read_text()) if path.exists() else {}
backup = path.with_name("models.json.pre-image-limits")
if path.exists() and not backup.exists():
    shutil.copy(path, backup)
providers = data.setdefault("providers", {})
for prov, mid in models:
    p = providers.setdefault(prov, {})
    custom = next((m for m in p.get("models", []) if m.get("id") == mid), None)
    if custom is not None:  # models defined here take inputLimits directly
        custom["inputLimits"] = limits
    else:  # built-in / extension models take it through modelOverrides
        p.setdefault("modelOverrides", {}).setdefault(mid, {})["inputLimits"] = limits

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=1) + "\n")
print(f"  image cap {W}x{H} on {len(models)} image-capable models")
