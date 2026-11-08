"""python3 tests/test_image_limits.py - set-image-limits.py against a fake `pi --list-models`."""
import json, os, pathlib, subprocess, tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "bin").mkdir()
fake = tmp / "bin/pi"
fake.write_text("#!/bin/sh\nprintf 'provider model context max-out thinking images\\n"
                "halogen halogen-qwen3.8-flash-next 262K 8K yes yes\\n"
                "anthropic claude-x 1M 128K yes yes\\n"
                "zai glm-text 200K 8K yes no\\n'\n")
fake.chmod(0o755)
mj = tmp / "agent/models.json"
mj.parent.mkdir()
mj.write_text(json.dumps({"providers": {"halogen": {"models": [{"id": "halogen-qwen3.8-flash-next"}]}}}))
script = pathlib.Path(__file__).parent.parent / "scripts/set-image-limits.py"
env = {**os.environ, "PATH": f"{tmp}/bin:{os.environ['PATH']}", "PI_HOME": str(tmp), "PI_IMAGE_MAX": "540x1200"}

subprocess.run(["python3", script], env=env, check=True)
p = json.loads(mj.read_text())["providers"]
lim = {"images": {"resize": {"maxWidth": 540, "maxHeight": 1200}}}
assert p["halogen"]["models"][0]["inputLimits"] == lim, p  # own model: set directly
assert p["anthropic"]["modelOverrides"]["claude-x"]["inputLimits"] == lim, p  # built-in: override
assert "zai" not in p, p  # text-only model untouched
assert (tmp / "agent/models.json.pre-image-limits").exists()

before = mj.read_text()
subprocess.run(["python3", script], env={**env, "PI_IMAGE_MAX": "off"}, check=True)
assert mj.read_text() == before
print("ok")
