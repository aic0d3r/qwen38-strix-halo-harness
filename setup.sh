#!/usr/bin/env bash
# pi harness for Strix Halo — automated setup.
# Idempotent: safe to re-run. Applies what it safely can (extensions, pi
# providers, sidecar), checks servers, prints exact next steps for what it can't fix.
#
#   ./setup.sh                  install + verify everything
#   ./setup.sh --doctor         read-only status check
#   ./setup.sh --no-halogen     pi-only install: skip the halogen provider, the NPU
#                               extensions and all container management (for boxes that
#                               run pi without a local halogen, e.g. a laptop against a
#                               remote Strix Halo appliance)
#   ./setup.sh --index <dir>    build NPU search index for a repo
set -u
HALOGEN_CKPT=""; INDEX_DIR=""; DOCTOR=0; VOICE=0; NO_HALOGEN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-halogen)  NO_HALOGEN=1; shift ;;
    --halogen) HALOGEN_CKPT="${2:-auto}"; shift 2 ;;
    --index)   INDEX_DIR="${2:-}"; shift 2 ;;
    --doctor)  DOCTOR=1; shift ;;
    --voice)   VOICE=1; shift ;;
    --halogen-upgrade) HALOGEN_UPGRADE="${2:-latest}"; shift 2>/dev/null || shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) shift ;;
  esac
done
REPO="$(cd "$(dirname "$0")" && pwd)"
PI_HOME="${PI_HOME:-$HOME/.pi}"
EXT_DIR="$PI_HOME/agent/extensions"
MODELS_JSON="$PI_HOME/agent/models.json"
TINY_PORT=8090; HAL_PORT=8731
ok(){ printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }
fail(){ printf '  \033[31m✗\033[0m %s\n' "$1"; }
tiny_up(){ curl -sf -m 3 "localhost:$TINY_PORT/health" >/dev/null 2>&1; }
hal_up(){  curl -sf -m 3 "localhost:$HAL_PORT/health"  >/dev/null 2>&1; }
lem_up(){  curl -sf -m 3 "localhost:13305/api/v1/models" >/dev/null 2>&1 || curl -sf -m 3 "localhost:13305/health" >/dev/null 2>&1; }
# fabric clock held? GPU+NPU together on Strix Halo are only safe with it held
# (halogen NPU docs). The container holds it while it runs via /sys, but any NPU
# program outside the container (voice STT, fine-tune jobs) needs the host unit.
fclk_held(){
  local d perf
  for d in /sys/class/drm/card*/device; do
    [ -e "$d/pp_dpm_fclk" ] || continue
    perf=$(cat "$d/power_dpm_force_performance_level" 2>/dev/null)
    if [ "$perf" = high ] || { [ "$perf" = manual ] && grep -q '\*' "$d/pp_dpm_fclk"; }; then return 0; fi
  done
  return 1
}
# resolve a usable llama-server for the sidecar (PATH, then known build dirs)
LLS="$(command -v llama-server 2>/dev/null || true)"
[ -z "$LLS" ] && for c in "$HOME/.local/bin/llama-server" \
    "$HOME/LLMBench/engines/strix-v0761/vulkan/bin/llama-server" \
    "$HOME/LLMBench/engines/strix-v075/vulkan/bin/llama-server" \
    "$HOME/LLMBench/engines/strix-v0741/vulkan/bin/llama-server"; do
  [ -x "$c" ] && LLS="$c" && break
done
# find the main halogen checkpoint (exclude ngram/vision sidecars)
find_ckpt(){ ls "$PWD"/*.hgn /models/*.hgn "$HOME"/models/*.hgn "$HOME"/Downloads/*.hgn \
    "${LLMBENCH:-$HOME/LLMBench}"/models/*/*.hgn 2>/dev/null \
    | grep -v 'ngram\|vision\|mtp\|w4b' | grep 'v2\.hgn' | head -1; }

# README-documented standalone commands: must run WITHOUT --doctor
if [ -n "${UNINSTALL:-}" ]; then
  exec bash "$REPO/scripts/uninstall.sh" ${UNINSTALL_YES:+--yes}
fi
if [ -n "${HALOGEN_UPGRADE:-}" ]; then
  exec bash "$REPO/scripts/upgrade-halogen.sh" "$HALOGEN_UPGRADE"
fi
if [ "$DOCTOR" = 1 ]; then
  echo "== harness doctor =="
  command -v pi >/dev/null          && ok "pi installed"           || fail "pi not found - install the pi coding agent first"
  [ -d "$EXT_DIR" ] && [ "$(ls "$EXT_DIR"/progress-tracker.ts "$EXT_DIR"/npu-retrieval.ts 2>/dev/null | wc -l)" = 2 ] \
                                    && ok "core extensions in place" || fail "extensions missing - run ./setup.sh"
  python3 -c "import json;m=json.load(open('$MODELS_JSON'));m['providers']['halogen'];m['providers']['llamacpp-tiny']" 2>/dev/null \
                                    && ok "providers configured"   || fail "providers missing - run ./setup.sh"
  [ -f "$PI_HOME/agent/mcp.json" ]  && ok "mcp.json present (serena+jina)" || warn "no mcp.json - serena/jina unavailable (run ./setup.sh)"
  tiny_up                           && ok "sidecar up (:8090)"     || fail "sidecar down - run ./setup.sh (or: systemctl --user enable --now ling-tiny)"
  if [ -f "$EXT_DIR/npu-retrieval.ts" ]; then
    hal_up                          && ok "halogen up (:8731)"     || fail "halogen down - run: ./setup.sh --halogen auto   (or existing start-halogen.sh)"
  else
    # pi-only install (--no-halogen or extensions never installed): halogen is not ours to manage
    hal_up                          && ok "halogen up (:8731, not managed by this install)" || warn "halogen down (pi-only install - fine unless you want the NPU tools)"
  fi
  # voice stack (opt-in --voice): Lemonade on :13305 serves Whisper STT + Kokoro TTS
  if [ "$VOICE" = 1 ] || lem_up; then
    lem_up                          && ok "lemonade up (:13305, voice)" || fail "lemonade down - sudo pacman -S lemonade-server, then: systemctl --user start lemond"
    command -v arecord >/dev/null   && ok "mic capture (arecord)"       || fail "arecord missing (alsa-utils)"
  fi
  # non-halogen engine detection: the harness contract (NPU guard/search/decide,
  # vision tower, /health cache+vision fields) exists only on halogen. Other
  # engines degrade gracefully (extensions fail open) but the user should know.
  if curl -sf --max-time 3 http://127.0.0.1:8731/health 2>/dev/null | grep -qv 'prompt_cache'; then
    warn "something that is not halogen is answering on :8731 (no prompt_cache in /health)."
    warn "  auto-guard, codebase_search, decide and image input will not work; /tune knobs partly inert."
    warn "  supported main engine: halogen only (see README 'Using another engine')."
  fi
  [ -f "$REPO/start-halogen.sh" ]   && ok "start-halogen.sh ready" || warn "no start-halogen.sh yet (written on first --halogen launch)"
  O9=$(awk '$4=="Normal"{u=0; for(i=14;i<=NF;i++) u+=$(i)*2**(i-14); print u}' /proc/buddyinfo 2>/dev/null | head -1)
  [ -n "$O9" ] && [ "$O9" -ge 200 ] && ok "order-9 pages: $O9"     || warn "order-9 pages: ${O9:-n/a} (<200): reboot before full-ctx (262k) halogen; 65k/2-slot fine"
  fclk_held && ok "gpu fabric clock held (GPU+NPU concurrency safe)" || { warn "gpu fabric clock NOT held - NPU beside GPU can hang or corrupt (halogen NPU docs). one-time install (sudo):"; warn "  sudo install -m 755 $REPO/config/systemd/halogen-fabric-clock /usr/local/sbin/ && sudo install -m 644 $REPO/config/systemd/halogen-fabric-clock.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now halogen-fabric-clock.service"; }
  if ! systemctl list-timers vm-compact.timer >/dev/null 2>&1; then
    warn "vm-compact.timer not installed - install it to stretch time between reboots:"
    warn "  sudo cp $REPO/config/systemd/vm-compact.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vm-compact.timer"
  fi
  R=$(pi -p --no-skills --no-context-files --provider halogen --model halogen-qwen3.8-flash-next --thinking low "Reply with exactly: OK" 2>/dev/null | grep -c 'OK')
[ "$R" -ge 1 ] && ok "ready check: model answered" || fail "ready check: no reply from halogen"
  exit 0
fi

echo "== 1. pi + extensions =="
if command -v pi >/dev/null; then ok "pi found"; else fail "pi coding agent not found on PATH - install it first, then re-run"; fi
mkdir -p "$EXT_DIR"
N=0
for f in "$REPO"/extensions/*.ts; do
  b=$(basename "$f")
  # NPU extensions are opt-out (--no-halogen): they need a local halogen on :8731
  { [ "$b" = "npu-retrieval.ts" ] || [ "$b" = "auto-guard.ts" ]; } && [ "$NO_HALOGEN" = 1 ] && continue
  if ! cmp -s "$f" "$EXT_DIR/$b"; then cp "$f" "$EXT_DIR/$b"; echo "  installed $b"; N=$((N+1)); fi
done
# drop measured no-op extensions from older installs (kept in extensions/optional/)
for f in context-prune npu-triage ling-tiny-triage; do
  [ -f "$EXT_DIR/$f.ts" ] && rm -f "$EXT_DIR/$f.ts" && echo "  removed $f.ts (measured no-op — see extensions/optional/)"
done
[ -d "$EXT_DIR/plan-mode" ] && rm -rf "$EXT_DIR/plan-mode" && echo "  removed plan-mode/ (3 uses in 569 sessions — see extensions/optional/)"
for d in "$REPO"/extensions/*/; do
  [ -d "$d" ] || continue
  b=$(basename "$d")
  [ "$b" = "optional" ] && continue # reference archive of measured no-ops; pi's loader never reads it
  if ! diff -rq "$d" "$EXT_DIR/$b" >/dev/null 2>&1; then rm -rf "$EXT_DIR/$b"; cp -r "$d" "$EXT_DIR/$b"; echo "  installed $b/"; N=$((N+1)); fi
done
[ "$N" = 0 ] && ok "all extensions already current ($(ls "$EXT_DIR"/*.ts 2>/dev/null | wc -l) files + $(find "$EXT_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) dirs)"
[ "$N" != 0 ] && ok "installed/updated $N extensions"

echo "== 2. pi providers (models.json) =="
if [ "$NO_HALOGEN" = 1 ]; then
  ok "pi-only install: halogen provider, NPU extensions and container management skipped"
  ok "point pi at your own provider, or at a remote halogen appliance by setting its baseUrl in models.json"
else
if ! command -v python3 >/dev/null; then fail "python3 required for models.json merge — install python3 and re-run"; else
python3 - "$MODELS_JSON" <<'PY'
import json, sys, os
path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
try: m = json.load(open(path))
except Exception: m = {}
m.setdefault("providers", {})
def model(id_, name, ctx):
    return {"id": id_, "name": name, "reasoning": True, "contextWindow": ctx,
            "maxTokens": 8192,
            "compat": {"thinkingFormat": "chat-template",
                       "chatTemplateKwargs": {"reasoning_effort": {"$var": "thinking.effort"},
                                              "enable_thinking": {"$var": "thinking.enabled"}}},
            "thinkingLevelMap": {"minimal": None, "low": "low", "medium": "medium", "high": "high", "xhigh": None, "max": None},
            "samplingParams": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0},
            "input": ["text", "image"]}  # vision tower enabled in start-halogen.sh (HALOGEN_VISION_TOWER=1)
HAL = m["providers"].get("halogen", {})
if "baseUrl" not in HAL:
    m["providers"]["halogen"] = {"baseUrl": "http://127.0.0.1:8731/v1", "api": "openai-completions",
        "apiKey": "dummy", "models": [model("halogen-qwen3.8-flash-next", "halogen Qwen3.8-Flash-Next", 262144)]}
    print("  added provider: halogen")
else:
    print("  halogen provider exists — left untouched")
TINY = m["providers"].get("llamacpp-tiny", {})
if "baseUrl" not in TINY:
    m["providers"]["llamacpp-tiny"] = {"baseUrl": "http://127.0.0.1:8090/v1", "api": "openai-completions",
        "apiKey": "dummy", "models": [model("ling3.0-tiny", "Ling-3.0-tiny sidecar", 131072)]}
    print("  added provider: llamacpp-tiny")
else:
    print("  llamacpp-tiny provider exists — left untouched")
# ling3.0-tiny thinks by default: the think pass ate budget and 8.6x'd latency
# (measured 10-06). Disable at the template level for all registry-based callers.
for _e in m.get("providers", {}).get("llamacpp-tiny", {}).get("models", []):
    if _e.get("id") == "ling3.0-tiny":
        _e.setdefault("compat", {})["chatTemplateKwargs"] = {"enable_thinking": False}
# cap maxTokens ONLY on our model ids (32768 requests get hard-rejected past ~32.7k ctx)
fixed = 0
def cap(o):
    global fixed
    if isinstance(o, dict):
        if o.get("id") in ("halogen-qwen3.8-flash-next", "ling3.0-tiny") and o.get("maxTokens", 0) > 8192:
            o["maxTokens"] = 8192; fixed += 1
        for v in o.values(): cap(v)
    elif isinstance(o, list):
        for v in o: cap(v)
cap(m)
if fixed: print(f"  capped maxTokens->8192 on {fixed} model entries (prevents 400 'does not fit' past ~32.7k ctx)")
json.dump(m, open(path, "w"), indent=1)
# startup defaults so plain `pi` opens the TUI on halogen (only if the user hasn't chosen)
sp = os.path.join(os.path.dirname(path), "settings.json")
try: s = json.load(open(sp))
except Exception: s = {}
changed = False
for k, v in (("defaultProvider", "halogen"), ("defaultModel", "halogen-qwen3.8-flash-next"), ("defaultThinkingLevel", "medium")):
    if k not in s: s[k] = v; changed = True
# silence the builtin llama.cpp router autodiscovery: it probes :8080/:8090 and lists
# stray bench models / duplicates the sidecar entry as "llama-server=http://..."
ext = s.setdefault("extensions", [])
if "-builtin:llama.cpp" not in ext: ext.append("-builtin:llama.cpp"); changed = True
# pi-llama-cpp package (if pre-installed) autodetects local llama-servers and lists
# stray bench models / duplicates our sidecar entry - remove it; we use models.json providers.
pkgs = s.get("packages", [])
if any("pi-llama-cpp" in p for p in pkgs):
    s["packages"] = [p for p in pkgs if "pi-llama-cpp" not in p]; changed = True
    print("  removed npm:pi-llama-cpp package (duplicates harness model list)")
if changed:
    json.dump(s, open(sp, "w"), indent=2); print("  pi startup defaults -> halogen (existing user overrides untouched)")
PY
ok "providers configured in $MODELS_JSON"
fi
fi

echo "== 2a-2. prompt templates =="
mkdir -p "$PI_HOME/templates"
T=0
for f in "$REPO"/templates/*.md; do
  [ -f "$f" ] || continue
  b=$(basename "$f")
  if ! diff -q "$f" "$PI_HOME/templates/$b" >/dev/null 2>&1; then cp "$f" "$PI_HOME/templates/$b"; T=$((T+1)); fi
done
ok "prompt templates current ($T updated, $(ls "$PI_HOME/templates" 2>/dev/null | wc -l) total)"
# generic home for rag-index.py so npu-retrieval's auto-index finds it without machine-specific paths
cmp -s "$REPO/rag-index.py" "$PI_HOME/agent/rag-index.py" || cp "$REPO/rag-index.py" "$PI_HOME/agent/rag-index.py"

echo "== 2a. skills + ponytail =="
SKILL_DIR="$PI_HOME/agent/skills"; mkdir -p "$SKILL_DIR"
mkdir -p "$PI_HOME/agent/themes"
if [ -f "$REPO/config/themes/opencode.json" ] && [ ! -f "$PI_HOME/agent/themes/opencode.json" ]; then
  cp "$REPO/config/themes/opencode.json" "$PI_HOME/agent/themes/opencode.json"
  echo "  installed theme: opencode (select via /settings, or set \"theme\": \"opencode\")"
fi
# micro-interactions ships in ~/.agents/skills (pi scans it natively) - a pi-side copy collides
[ -d "$SKILL_DIR/micro-interactions" ] && rm -rf "$SKILL_DIR/micro-interactions" && echo "  removed duplicate micro-interactions (native ~/.agents/skills copy is used)"
S=0
for d in "$REPO"/skills/*/; do
  [ -d "$d" ] || continue
  b=$(basename "$d")
  if ! diff -rq "$d" "$SKILL_DIR/$b" >/dev/null 2>&1; then rm -rf "$SKILL_DIR/$b"; cp -r "$d" "$SKILL_DIR/$b"; S=$((S+1)); fi
done
if command -v pi >/dev/null; then
  if ! python3 -c "import json,sys; sys.exit(0 if any('ponytail' in p for p in json.load(open('$PI_HOME/agent/settings.json')).get('packages',[])) else 1)" 2>/dev/null; then
    pi install git:github.com/DietrichGebert/ponytail >/dev/null 2>&1 && ok "ponytail installed (pi package)" || warn "ponytail install failed - run: pi install git:github.com/DietrichGebert/ponytail"
  else ok "ponytail package present"; fi
fi
[ "$S" = 0 ] && ok "skills current ($(ls "$SKILL_DIR" | wc -l) dirs)" || ok "installed/updated $S skills"

echo "== 2b. MCP servers (mcp.json) =="
MCP_JSON="$PI_HOME/agent/mcp.json"
if [ -f "$MCP_JSON" ]; then
  ok "mcp.json exists (left untouched)"
elif command -v pi >/dev/null; then
  python3 - "$MCP_JSON" <<'PYM'
import json, os, sys
path = sys.argv[1]
servers = {
    "serena": {
        "command": "~/.local/bin/serena", "args": ["start-mcp-server"],
        "exposure": "hidden",
        "toolExposure": {"activate_project": "direct", "find_symbol": "direct",
            "get_symbols_overview": "direct", "find_referencing_symbols": "direct",
            "search_for_pattern": "direct", "get_diagnostics_for_file": "direct",
            "replace_symbol_body": "direct", "insert_after_symbol": "direct",
            "rename_symbol": "direct", "*": "hidden"},
        "description": "Serena LSP toolbox: symbol-precise code navigation, diagnostics, and edits (activate_project first)"},
    "jina": {
        "url": "https://mcp.jina.ai/v1",
        "headers": {"Authorization": "!echo Bearer $(cat ~/.config/opencode/jina.key)"},
        "enabled": False,
        "description": "Jina web search and page reading (enable via /mcp; needs the key file)"},
}
key = os.path.expanduser("~/.config/opencode/jina.key")
if not os.path.exists(key):
    servers["jina"]["headers"] = {"Authorization": "${JINA_API_KEY}"}
json.dump({"mcpServers": servers}, open(path, "w"), indent=2)
print("  wrote mcp.json (serena direct x9, jina disabled)")
PYM
  ok "MCP servers configured (pi mcp list to inspect)"
else
  warn "pi not found - skipped mcp.json"
fi

echo "== 3. ling-tiny sidecar (:8090) =="
GGUF=""
if tiny_up; then
  ok "sidecar is up"
elif [ -f "$HOME/.config/systemd/user/ling-tiny.service" ]; then
  # unit exists from a previous setup: start it (supervised + keep-alive) instead of raw-spawning
  systemctl --user daemon-reload 2>/dev/null; systemctl --user start ling-tiny.service 2>/dev/null
  for _ in $(seq 1 12); do sleep 5; tiny_up && break; done
  if tiny_up; then ok "sidecar started via systemd unit (supervised, auto-restart)"; else fail "unit failed to start - check: journalctl --user -u ling-tiny"; fi
else
  fail "sidecar not running"
  GGUF="$(ls "$HOME"/.models/ling3.0-tiny/*.gguf "$REPO"/models/ling3.0-tiny/*.gguf 2>/dev/null | head -1)"
  if [ -z "${GGUF:-}" ]; then
    warn "model not found locally - downloading Ling-3.0-tiny-Q4_K_M.gguf (~4.5 GB, resumable) to ~/.models/ling3.0-tiny/"
    mkdir -p "$HOME/.models/ling3.0-tiny"
    curl -fL -C - -o "$HOME/.models/ling3.0-tiny/Ling-3.0-tiny-Q4_K_M.gguf" \
      "https://huggingface.co/inclusionAI/Ling-3.0-tiny-GGUF/resolve/main/Ling-3.0-tiny-Q4_K_M.gguf" \
      && GGUF="$HOME/.models/ling3.0-tiny/Ling-3.0-tiny-Q4_K_M.gguf" \
      || fail "download failed - fetch it manually from huggingface.co/inclusionAI/Ling-3.0-tiny-GGUF"
    if [ -n "${GGUF:-}" ]; then
      WANT=$(curl -sf -m 15 "https://huggingface.co/api/models/inclusionAI/Ling-3.0-tiny-GGUF/tree/main" \
        | python3 -c "import json,sys; print(next((s['lfs']['oid'] for s in json.load(sys.stdin) if s['path']=='Ling-3.0-tiny-Q4_K_M.gguf'),''))" 2>/dev/null)
      GOT=$(sha256sum "$GGUF" | cut -d' ' -f1)
      if [ -n "$WANT" ] && [ "$WANT" != "$GOT" ]; then
        fail "sha256 MISMATCH (want $WANT, got $GOT) - deleting bad file, fetch manually"
        rm -f "$GGUF"; GGUF=""
      else ok "sha256 verified"; fi
    fi
  fi
  if [ -n "${LLS:-}" ] && [ -n "${GGUF:-}" ]; then
    setsid nohup "$LLS" -a ling3.0-tiny -m "$GGUF" -ngl 99 -c 131072 --jinja \
      --host 127.0.0.1 --port $TINY_PORT > "$REPO/ling-tiny.log" 2>&1 < /dev/null &
    for i in $(seq 1 12); do sleep 5; tiny_up && break; done
    tiny_up && ok "sidecar started (log: $REPO/ling-tiny.log)" || fail "started but not healthy yet — check $REPO/ling-tiny.log"
  else
    warn "fix: get Ling-3.0-tiny-Q4_K_M.gguf (Hugging Face) and a Vulkan llama-server, then:"
    warn "  llama-server -a ling3.0-tiny -m <gguf> -ngl 99 -c 131072 --jinja --port 8090"
    [ -z "${GGUF:-}" ] && warn "  (looked in ~/.models/ling3.0-tiny/ and ./models/ling3.0-tiny/)"
    [ -z "${LLS:-}" ] && warn "  (no llama-server binary found - set one up on PATH)"
  fi
fi

# systemd user unit: auto-restart beats watchdog polling (sidecar died silently once)
if command -v systemctl >/dev/null && systemctl --user status >/dev/null 2>&1; then
  UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
  UNIT_GGUF="${GGUF:-$(ls "$HOME"/.models/ling3.0-tiny/*.gguf 2>/dev/null | head -1)}"
  if [ -n "$UNIT_GGUF" ] && [ -n "${LLS:-}" ]; then
    cat > "$UNIT_DIR/ling-tiny.service" <<UNIT
[Unit]
Description=Ling-3.0-tiny sidecar (llama.cpp)
After=network.target

[Service]
ExecStart=$LLS -a ling3.0-tiny -m $UNIT_GGUF -ngl 99 -c 131072 --jinja --host 127.0.0.1 --port $TINY_PORT --metrics
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    if ! tiny_up; then
      systemctl --user start ling-tiny.service 2>/dev/null \
        && ok "sidecar started as systemd user unit (auto-restart on death, NOT enabled at boot)" \
        || warn "unit written but not started - run: systemctl --user start ling-tiny"
    else
      ok "sidecar running; keep-alive unit installed (dies -> systemd restarts it; nothing starts at boot)"
    fi
  fi
fi

# voice stack (opt-in: ./setup.sh --voice) - privateer-speak pi package + Lemonade
if [ "$VOICE" = 1 ]; then
  echo "== 3b. voice (privateer-speak + lemonade :13305) =="
  if command -v lemond >/dev/null; then
    # Nothing-at-boot rule: start it now if down, never enable (same policy as ling-tiny).
    lem_up || systemctl --user start lemond.service 2>/dev/null
    sleep 2
    lem_up && ok "lemond up (:13305, whisper STT + kokoro TTS, single voice server)" || warn "lemond not answering yet (check: journalctl --user -u lemond)"
    lemonade list --downloaded 2>/dev/null | grep -q "Whisper-Large-v3-Turbo" \
      && ok "audio models present (Whisper-Large-v3-Turbo + kokoro-v1)" \
      || warn "load the audio models once: lemonade load Whisper-Large-v3-Turbo && lemonade load kokoro-v1"
  else
    warn "lemonade not found - install it (Arch/CachyOS: sudo pacman -S lemonade-server;"
    warn "  Ubuntu: snap install lemonade-server; or docker: see lemonade-server.ai docs), then re-run ./setup.sh --voice"
  fi
  if command -v pi >/dev/null; then
    if ! python3 -c "import json,sys; sys.exit(0 if any('privateer-speak' in p for p in json.load(open('$PI_HOME/agent/settings.json')).get('packages',[])) else 1)" 2>/dev/null; then
      pi install npm:privateer-speak >/dev/null 2>&1 && ok "installed privateer-speak (pi package)" || warn "privateer-speak install failed - run: pi install npm:privateer-speak"
    else ok "privateer-speak present"; fi
    if [ ! -f "$PI_HOME/speak.json" ]; then
      cat > "$PI_HOME/speak.json" <<'SPEAKCFG'
{
  "enabled": true,
  "provider": "openai-compatible",
  "stream": false,
  "providers": {
    "openai-compatible": {
      "baseUrl": "http://127.0.0.1:13305/v1",
      "apiKey": "none",
      "model": "kokoro-v1",
      "sttModel": "Whisper-Large-v3-Turbo",
      "voice": "af_heart"
    }
  },
  "input": {
    "enabled": true,
    "provider": "openai-compatible",
    "language": "en",
    "autoSend": false,
    "silenceMs": 1200,
    "shortcut": "alt+t"
  }
}
SPEAKCFG
      ok "wrote $PI_HOME/speak.json (openai-compatible -> lemonade)"
    else
      ok "$PI_HOME/speak.json exists (left untouched)"
    fi
  fi
  ok "in pi: /talk records + transcribes into the composer (alt+t), /speak reads answers aloud"
fi

echo "== 4. halogen server (:8731) =="
if [ "$NO_HALOGEN" = 1 ]; then
  ok "skipped (--no-halogen: pi-only install)"
elif hal_up; then
  ok "halogen is up"
  cache=$(curl -sf --max-time 5 http://127.0.0.1:8731/health | python3 -c "import json,sys; h=json.load(sys.stdin); pc=h.get('prompt_cache'); v=(pc.get('enabled') if isinstance(pc,dict) else pc) if pc is not None else h.get('cache'); print('on' if v else 'off')" 2>/dev/null || echo unknown)
  if [ "$cache" = off ]; then
    fail "prompt cache is OFF — restart WITHOUT CACHE=0 (sessions run ~2.5x slower without it)"
  elif [ "$cache" = on ]; then
    ok "prompt cache: ON (verified via /health)"
  else
    docker logs "$(docker ps -q --filter ancestor=ghcr.io/peonist-ai/halogen-flash-server 2>/dev/null | head -1)" 2>/dev/null | grep -q 'prompt cache OFF' \
      && fail "prompt cache is OFF — restart WITHOUT CACHE=0 (sessions run ~2.5x slower without it)" \
      || warn "prompt cache: unknown (/health unreadable, no OFF line in log)"
  fi
elif [ -n "$HALOGEN_CKPT" ]; then
  # launch it ourselves (new-user path). prompt cache stays ON: never set HALOGEN_PROMPT_CACHE=0.
  if ! command -v docker >/dev/null; then
    fail "docker not found — install docker, re-login, then re-run: ./setup.sh --halogen $HALOGEN_CKPT"
  else
    [ "$HALOGEN_CKPT" = "auto" ] && HALOGEN_CKPT=$(find_ckpt)
    if [ -z "$HALOGEN_CKPT" ] || [ ! -f "$HALOGEN_CKPT" ]; then
      # no checkpoint found: offer to fetch exactly the required file set (~124 GB, resumable)
      DL_DIR="$HOME/models/halogen-qwen3.8-flash-next"
      echo "  no .hgn checkpoint found (looked in ., /models, ~/models, ~/Downloads, LLMBench/models)."
      AVAIL=$(df -BG --output=avail "$HOME" 2>/dev/null | tail -1 | tr -dc '0-9')
      echo "  required download: ~124 GB into $DL_DIR${AVAIL:+ (free: ${AVAIL} GB)}"
      read -r -p "  download now? [y/N] " A
      [ "$A" = "y" ] || fail "aborted - download the files (see README 'Minimum download') or pass a checkpoint: ./setup.sh --halogen /path/to/checkpoint.hgn"
      if [ "${AVAIL:-999}" -lt 135 ]; then
        warn "only ${AVAIL} GB free - the download will likely run out of space. Free up at least 135 GB first."
        fail "not enough free space"
      fi
      HF_BIN="$(command -v hf || command -v huggingface-cli || true)"
      if [ -z "$HF_BIN" ]; then
        echo "  no hf cli found - bootstrapping a venv (~40 MB) ..."
        python3 -m venv "$HOME/.hf-venv" 2>/dev/null && "$HOME/.hf-venv/bin/pip" install -q -U huggingface_hub 2>/dev/null \
          && HF_BIN="$HOME/.hf-venv/bin/hf" || fail "could not bootstrap the hf downloader - install huggingface_hub (pipx or a venv) and re-run"
      fi
      mkdir -p "$DL_DIR"
      echo "  downloading (resumable - re-run this command if interrupted) ..."
      "$HF_BIN" download peonist-ai/halogen-qwen3.8-flash-next \
        qwen38-flash-next-v2.hgn qwen38-flash-next-ngram.hgn qwen38-flash-next-vision.hgn tokenizer/ \
        --local-dir "$DL_DIR" || fail "download failed/incomplete - re-run ./setup.sh --halogen auto to resume"
      # the NPU models live in four separate repos (they are NOT in the main weights repo)
      for n in decider-0.8b qwen3-embedding-0.6b qwen3-reranker-0.6b qwen3guard-gen-0.6b; do
        [ -d "$DL_DIR/npu/$n" ] && continue
        echo "  downloading NPU model: $n ..."
        "$HF_BIN" download "peonist-ai/halogen-npu-$n" --local-dir "$DL_DIR/npu/$n" \
          || fail "NPU model download failed: $n - re-run ./setup.sh --halogen auto to resume"
      done
      [ -f "$DL_DIR/qwen38-flash-next-v2.hgn" ] || fail "download finished but v2.hgn is missing from $DL_DIR"
      HALOGEN_CKPT="$DL_DIR/qwen38-flash-next-v2.hgn"
      echo "  download complete"
    fi
    if [ -z "$HALOGEN_CKPT" ] || [ ! -f "$HALOGEN_CKPT" ]; then
      fail "no .hgn checkpoint found (looked in ., /models, ~/models, ~/Downloads, LLMBench/models) - pass it: ./setup.sh --halogen /path/to/checkpoint.hgn"
    else
      PIN=$(grep -oE 'HALOGEN_IMAGE_TAG:-[0-9.a-z]+' "$REPO/server/start-halogen.sh" | head -1 | cut -d- -f2)
      docker image inspect "ghcr.io/peonist-ai/halogen-flash-server:$PIN" >/dev/null 2>&1 \
        || { echo "  pulling halogen image (first run, ~GB) ..."; docker pull "ghcr.io/peonist-ai/halogen-flash-server:$PIN"; }
      W=$(dirname "$HALOGEN_CKPT")
      RENDER_GID=$(getent group render | cut -d: -f3); VIDEO_GID=$(getent group video | cut -d: -f3)
      echo "  launching halogen ($HALOGEN_CKPT, cache ON) ..."
      # also drop a one-command restart script (halogen is NOT auto-started at boot on purpose: full-ctx
      # launches need a fresh allocator - see README. 65k/2-slot is the safe default below.)
      install -m 755 "$REPO/server/start-halogen.sh" "$REPO/start-halogen.sh"
      chmod +x "$REPO/start-halogen.sh"; ok "wrote $REPO/start-halogen.sh (restart any time)"
      setsid nohup "$REPO/start-halogen.sh" > "$REPO/halogen.log" 2>&1 < /dev/null &
      for i in $(seq 1 30); do sleep 10; hal_up && { ok "halogen up (~${i}0s)"; break; }; done
      hal_up || fail "not healthy after 5 min - check $REPO/halogen.log"
    fi
  fi
elif [ -n "$(find_ckpt)" ]; then
  warn "halogen down but a checkpoint exists - start it: ./setup.sh --halogen auto"
else
  warn "halogen down (optional: main model). start with: ./setup.sh --halogen auto"
fi

echo "== 5. NPU index (optional) =="
if [ -n "$INDEX_DIR" ]; then
  [ -d "$INDEX_DIR" ] || fail "not a directory: $INDEX_DIR"
  [ -d "$INDEX_DIR" ] && { [ -f "$REPO/rag-index.py" ] && python3 "$REPO/rag-index.py" --dir "$INDEX_DIR" --incremental --ext '.cpp,.h,.c,.hpp,.go,.py,.ts,.js,.html,.css,.sh' --exclude '(\.git|build|vendor|node_modules)' || fail "rag-index.py not found"; }
else
  warn "run when needed:  ./setup.sh --index <repo-dir>"
fi

O9=$(awk '$4=="Normal"{u=0; for(i=14;i<=NF;i++) u+=$(i)*2**(i-14); print u}' /proc/buddyinfo 2>/dev/null | head -1)
if [ -n "$O9" ] && [ "$O9" -lt 200 ]; then
  warn "order-9 contiguous pages = $O9 (<200): full-ctx halogen (262k) will wedge. 65k/2-slot is fine. reboot before full-ctx."
else ok "order-9 pages: ${O9:-n/a}"; fi
fclk_held && ok "gpu fabric clock held (GPU+NPU concurrency safe)" || { warn "gpu fabric clock NOT held - NPU beside GPU can hang or corrupt (halogen NPU docs). one-time install (sudo):"; warn "  sudo install -m 755 $REPO/config/systemd/halogen-fabric-clock /usr/local/sbin/ && sudo install -m 644 $REPO/config/systemd/halogen-fabric-clock.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now halogen-fabric-clock.service"; }
# fabric clock: GPU+NPU together are only safe with it held (halogen NPU docs);
# fclk_held() above checks it, container holds it while it runs via /sys.
if systemctl list-timers vm-compact.timer >/dev/null 2>&1; then
  ok "vm-compact.timer active (15-min allocator hygiene)"
else
  warn "vm-compact.timer not installed - big-model days fragment the allocator (order-9 decay). one-time install:"
  warn "  sudo cp $REPO/config/systemd/vm-compact.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vm-compact.timer"
fi

echo
echo "done. doctor check any time:  ./setup.sh --doctor"
echo "smoke test:"
echo "  pi -p --no-skills --no-context-files --provider halogen \\"
echo "    --model halogen-qwen3.8-flash-next --thinking medium \\"
echo "    --tools commit,codebase_search,bash,read,write \\"
echo '    "List the 5 largest source files and write them to SMOKE.md"'
