#!/usr/bin/env bash
# uninstall.sh — remove everything setup.sh provisioned. Backs up first.
# Leaves alone: your own model entries, non-harness extensions, the halogen
# container/checkpoint, mcp.json if you've modified it, the sidecar GGUF
# (prints the rm command instead).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PI_HOME="${PI_HOME:-$HOME/.pi}"
AG="$PI_HOME/agent"
TS=$(date +%Y%m%d-%H%M%S)
BACKUP="$HOME/pi-harness-uninstall-backup-$TS.tar.gz"
ASSUME_YES=0
[ "${1:-}" = "--yes" ] && ASSUME_YES=1

echo "this removes everything the harness installer provisioned:"
echo "  - extensions, templates, skills, theme under $AG"
echo "  - halogen + llamacpp-tiny providers in models.json"
echo "  - pi defaults (halogen) if they still match what setup set"
echo "  - ling-tiny + hw-telemetry systemd units"
echo "  - NOT touched: mcp.json, the sidecar GGUF, the halogen container,"
echo "    your checkpoint files, the vm-compact.timer (sudo; command printed)"
if [ "$ASSUME_YES" -ne 1 ]; then
  read -r -p "type 'uninstall' to continue: " A
  [ "$A" = "uninstall" ] || { echo "aborted"; exit 1; }
fi

echo "[1/8] backup -> $BACKUP"
# one tar call: a second `czf` would truncate the first archive (backup lost)
TAR_ARGS=(-C "$PI_HOME" agent/extensions agent/templates agent/skills agent/themes agent/settings.json agent/models.json agent/mcp.json)
[ -f "$HOME/.config/systemd/user/ling-tiny.service" ] && TAR_ARGS+=(-C "$HOME/.config/systemd/user" ling-tiny.service)
[ -f "$AG/rag-index.py" ] && TAR_ARGS+=(-C "$PI_HOME" agent/rag-index.py) # own -C: tar applies earlier -C to later members
tar czf "$BACKUP" "${TAR_ARGS[@]}" 2>/dev/null
echo "      $(du -h "$BACKUP" | cut -f1)"

echo "[2/8] systemd units"
if [ -n "${PI_HOME:-}" ]; then
  echo "      skipped (PI_HOME sandbox - real systemd units untouched)"
else
  systemctl --user disable --now ling-tiny.service >/dev/null 2>&1 && echo "      ling-tiny stopped/disabled"
  rm -f "$HOME/.config/systemd/user/ling-tiny.service"
  if [ -f "$HOME/.config/systemd/user/lemonade-server.service" ]; then
    systemctl --user disable --now lemonade-server.service >/dev/null 2>&1 && echo "      lemonade-server stopped/disabled"
    rm -f "$HOME/.config/systemd/user/lemonade-server.service"
    echo "      note: the lemonade-server package itself is left installed (remove: pacman -R lemonade-server)"
  fi
  if [ -f "$AG/../speak.json" ]; then
    rm -f "$AG/../speak.json" && echo "      speak.json removed (voice config)"
    echo "      privateer-speak package left installed (remove: pi remove npm:privateer-speak)"
  fi
  systemctl --user disable --now hw-telemetry.timer >/dev/null 2>&1 && echo "      hw-telemetry timer stopped/disabled"
  rm -f "$HOME/.config/systemd/user/hw-telemetry.service" "$HOME/.config/systemd/user/hw-telemetry.timer"
  systemctl --user daemon-reload 2>/dev/null
fi

echo "[3/8] extensions provisioned by the harness"
for f in progress-tracker.ts npu-retrieval.ts ling-tiny-compaction.ts ling-tiny-commit.ts \
         ling-tiny-branch-summary.ts ling-tiny-repomap.ts harness-tune.ts auto-guard.ts turn-timer.ts; do
  [ -f "$AG/extensions/$f" ] && rm -f "$AG/extensions/$f"
done
[ -d "$AG/extensions/subagent" ] && rm -rf "$AG/extensions/subagent"
[ -d "$AG/extensions/optional" ] && rm -rf "$AG/extensions/optional"
[ -f "$AG/rag-index.py" ] && rm -f "$AG/rag-index.py" && echo "      rag-index.py removed (installed by setup)"
echo "      done (extensions you added yourself are left in place)"

echo "[4/8] templates + skills + theme"
for f in gauntlet.md speedtest.md; do rm -f "$PI_HOME/templates/$f"; done
for d in "$REPO"/skills/*/; do
  b=$(basename "$d"); [ -d "$AG/skills/$b" ] && rm -rf "$AG/skills/$b"
done
if [ -f "$AG/themes/opencode.json" ] && cmp -s "$AG/themes/opencode.json" "$REPO/config/themes/opencode.json" 2>/dev/null; then
  rm -f "$AG/themes/opencode.json"; echo "      theme removed (we installed it)"
fi

echo "[5/8] pi defaults + providers"
python3 - <<'PY'
import json, os
home = os.environ.get("PI_HOME", os.path.expanduser("~"))
home = os.path.join(home, "agent") if os.environ.get("PI_HOME") else os.path.join(home, ".pi/agent")
sp = os.path.join(home, "settings.json")
if os.path.exists(sp):
    s = json.load(open(sp))
    # defaults: remove only if they still match what setup set
    for k, ours in (("defaultProvider","halogen"), ("defaultModel","halogen-qwen3.8-flash-next"), ("defaultThinkingLevel","medium")):
        if s.get(k) == ours: s.pop(k, None)
    pkgs = s.get("packages", [])
    s["packages"] = [p for p in pkgs if "ponytail" not in p]
    json.dump(s, open(sp, "w"), indent=2); open(sp, "a").write("\n")
mp = os.path.join(home, "models.json")
if os.path.exists(mp):
    m = json.load(open(mp))
    for prov in ("halogen", "llamacpp-tiny"):
        m.get("providers", {}).pop(prov, None)
    json.dump(m, open(mp, "w"), indent=2); open(mp, "a").write("\n")
print("      defaults removed where they matched ours; providers halogen/llamacpp-tiny removed")
PY

echo "[6/8] mcp.json"
if [ -f "$AG/mcp.json" ]; then
  echo "      left in place (contains serena/jina/playwright choices you may want elsewhere)"
fi

echo "[7/8] sidecar GGUF"
GG=$(find "$HOME/.models" "$HOME/Downloads" "$HOME/LLMBench/models" -maxdepth 3 -name "Ling-3.0-tiny-Q4_K_M.gguf" 2>/dev/null | head -1)
if [ -n "$GG" ]; then
  echo "      kept: $GG ($(du -h "$GG" | cut -f1)) - rm it manually to reclaim the space"
else
  echo "      not found (nothing to reclaim)"
fi

echo "[8/8] notes"
echo "      halogen container/checkpoint: untouched (docker stop <id> + rm the model dir if desired)"
echo "      vm-compact.timer (sudo): sudo rm /etc/systemd/system/vm-compact.{service,timer} && sudo systemctl daemon-reload"
echo "      backup: $BACKUP (restore by untarring into ~)"
echo "DONE - pi is back to stock (restart any running pi session)."
