#!/usr/bin/env bash
# pi harness for Strix Halo — automated setup.
# Idempotent: safe to re-run. Applies what it safely can (extensions, pi
# providers, sidecar), checks servers, prints exact next steps for what it can't fix.
#
#   ./setup.sh                  in a terminal: guided wizard for any engine (= ./harness setup);
#                               otherwise (scripts, CI): install + verify everything
#                               (saved answers: ./harness config)
#   ./setup.sh --halogen [auto|path.hgn]  also provision + start the halogen server
#   ./setup.sh --doctor         read-only status check
#   ./setup.sh --refresh-mcp    reset serena/semble MCP exposure and ensure ddg (backs up mcp.json first)
#   ./setup.sh --no-halogen     pi-only install: skip the halogen provider, the NPU
#                               extensions and all container management (for boxes that
#                               run pi without a local halogen, e.g. a laptop against a
#                               remote Strix Halo appliance)
#   ./setup.sh --strata [url]   pi-only install + a provider for Strata (github.com/Niko1221/Strata,
#                               NVIDIA/AMD desktop GPUs; default http://127.0.0.1:8080). Install
#                               and start Strata itself first, per its README.
#   ./setup.sh --gufo [url]     same pi-only install for gufo (default http://127.0.0.1:8080, what
#                               server/start-gufo-flashnext.sh serves). Stop halogen first.
#   ./setup.sh --voice          also set up the voice stack (Lemonade STT/TTS)
#   ./setup.sh --no-sidecar     do not run the ling-tiny sidecar (saves ~8 GiB GPU memory; the
#                               main model writes commits/summaries). --sidecar turns it back on.
#   ./setup.sh --halogen-upgrade [tag]  upgrade the halogen image, roll back on failure
#   ./setup.sh --uninstall      remove what setup provisioned (backs up first)
set -u
# bare invocation in a terminal = the guided wizard (flags below stay for scripts/CI)
if [ $# -eq 0 ] && [ -t 0 ] && [ -t 1 ]; then exec python3 -u "$(dirname "$(readlink -f "$0")")/setup/harness.py" setup; fi
HALOGEN_CKPT=""; SIDECAR_SET=""; DOCTOR=0; REFRESH_MCP=0; VOICE=0; NO_HALOGEN=0; STRATA=0; GUFO=0; ENG=strata; ENG_URL=http://127.0.0.1:8080
while [ $# -gt 0 ]; do
  case "$1" in
    --no-halogen)  NO_HALOGEN=1; shift ;;
    --halogen) HALOGEN_CKPT="${2:-auto}"; shift 2 2>/dev/null || shift ;;
    --strata|--gufo) [ "$1" = --gufo ] && { GUFO=1; ENG=gufo; } || STRATA=1; NO_HALOGEN=1
               case "${2:-}" in http*) ENG_URL="${2%/}"; shift 2 ;; *) shift ;; esac ;;
    --doctor)  DOCTOR=1; shift ;;
    --refresh-mcp) REFRESH_MCP=1; shift ;;
    --voice)   VOICE=1; shift ;;
    --sidecar|--no-sidecar) SIDECAR_SET=$([ "$1" = --sidecar ] && echo 1 || echo 0); shift ;;
    --halogen-upgrade) case "${2:-}" in ""|-*) HALOGEN_UPGRADE=latest; shift ;; *) HALOGEN_UPGRADE="$2"; shift 2 ;; esac ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see ./setup.sh --help)" >&2; exit 2 ;;
  esac
done
REPO="$(cd "$(dirname "$0")" && pwd)"
PI_HOME="${PI_HOME:-$HOME/.pi}"
EXT_DIR="$PI_HOME/agent/extensions"
MODELS_JSON="$PI_HOME/agent/models.json"
TINY_PORT=8090; HAL_PORT=8731
# ling-tiny sidecar is a saved choice (1/0), asked by the wizard; absent = on (old installs keep working)
SC_PREF="$PI_HOME/agent/harness-sidecar"
[ -n "$SIDECAR_SET" ] && { mkdir -p "$(dirname "$SC_PREF")"; echo "$SIDECAR_SET" > "$SC_PREF"; }
SIDECAR_ON=1; [ "$(cat "$SC_PREF" 2>/dev/null)" = 0 ] && SIDECAR_ON=0
# sidecar batch size shared with /tune and start-ling-tiny.sh; without this the
# unit and first-boot spawn ran llama.cpp's 512 default, 8x below the measured 4096
UB_TINY=$(tr -dc '0-9' 2>/dev/null < "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/harness-ubatch"); UB_TINY=${UB_TINY:-4096}
ok(){ printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }
fail(){ printf '  \033[31m✗\033[0m %s\n' "$1"; }
tiny_up(){ curl -sf -m 3 "localhost:$TINY_PORT/health" >/dev/null 2>&1; }
hal_up(){  curl -sf -m 3 "localhost:$HAL_PORT/health"  >/dev/null 2>&1; }
eng_up(){ curl -sf -m 3 "$ENG_URL/v1/models" >/dev/null 2>&1; }
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
# find the main halogen checkpoint: one search, owned by the launcher
find_ckpt(){ bash "$REPO/server/start-halogen.sh" --print-ckpt; }
# sudo one-time fixes: full command on the first install run (always in --doctor), then only in the log until fixed
HINTS="$PI_HOME/agent/harness-hints-shown"
hint(){ local k=$1; shift
  if [ "$DOCTOR" = 1 ] || ! grep -qx "$k" "$HINTS" 2>/dev/null; then
    for l in "$@"; do warn "$l"; done; [ "$DOCTOR" = 1 ] || echo "$k" >> "$HINTS"
  else echo "  - $1 (fix shown once; ./setup.sh --doctor shows it again)"; fi; }
hint_fixed(){ [ -f "$HINTS" ] && sed -i "/^$1\$/d" "$HINTS"; return 0; }
# host hygiene shared by --doctor and install: allocator, fabric clock, vm-compact timer
host_checks(){
  local o9
  o9=$(awk '$4=="Normal"{u=0; for(i=14;i<=NF;i++) u+=$(i)*2**(i-14); print u}' /proc/buddyinfo 2>/dev/null | head -1)
  # a running engine (halogen :8731, gufo/Strata :8080) holds the GPU heap, so the count only means something before a launch
  if hal_up || curl -sf -m 3 localhost:8080/v1/models >/dev/null 2>&1; then ok "order-9 pages: n/a while an engine holds the GPU memory"
  else [ -n "$o9" ] && [ "$o9" -lt 200 ] \
    && warn "order-9 pages = $o9 (<200): full-ctx halogen (262k) will wedge; 65k/2-slot is fine. reboot before full-ctx." \
    || ok "order-9 pages: ${o9:-n/a}"; fi
  # GPU+NPU together are only safe with the fabric clock held; the container holds it while it runs
  if fclk_held; then ok "gpu fabric clock held (GPU+NPU concurrency safe)"; hint_fixed fclk
  else hint fclk "gpu fabric clock NOT held - NPU beside GPU can hang or corrupt (halogen NPU docs). one-time install (sudo):" \
    "  sudo install -m 755 $REPO/config/systemd/halogen-fabric-clock /usr/local/sbin/ && sudo install -m 644 $REPO/config/systemd/halogen-fabric-clock.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now halogen-fabric-clock.service"; fi
  # list-timers exits 0 even with "0 timers listed"; is-enabled actually checks
  if systemctl is-enabled --quiet vm-compact.timer 2>/dev/null; then
    ok "vm-compact.timer enabled (15-min allocator hygiene)"; hint_fixed vmcompact
  else
    hint vmcompact "vm-compact.timer not installed - big-model days fragment the allocator (order-9 decay). one-time install:" \
      "  sudo cp $REPO/config/systemd/vm-compact.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vm-compact.timer"
  fi
}

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
  [ -f "$EXT_DIR/progress-tracker.ts" ] \
                                    && ok "core extensions in place" || fail "extensions missing - run ./setup.sh"
  # halogen provider is only ours on NPU installs (--no-halogen skips it and the NPU extensions)
  python3 -c "import json,os;p=json.load(open('$MODELS_JSON'))['providers'];p['llamacpp-tiny'];os.path.exists('$EXT_DIR/npu-retrieval.ts') and p['halogen']" 2>/dev/null \
                                    && ok "providers configured"   || fail "providers missing - run ./setup.sh"
  [ -f "$PI_HOME/agent/mcp.json" ]  && ok "mcp.json present (serena+ddg+jina+semble)" || warn "no mcp.json - serena/ddg/jina/semble unavailable (run ./setup.sh)"
  if [ -f "$PI_HOME/agent/mcp.json" ]; then
    MCP_DRIFT=$(python3 "$REPO/setup/mcp.py" check "$PI_HOME/agent/mcp.json" 2>/dev/null)
    [ -n "$MCP_DRIFT" ] && warn "$MCP_DRIFT"
  fi
  if [ "$SIDECAR_ON" = 0 ]; then ok "sidecar off (opted out; ./setup.sh --sidecar turns it on)"
  elif tiny_up;                     then ok "sidecar up (:8090)"
  elif [ -f "$HOME/.config/systemd/user/ling-tiny.service" ]; then ok "sidecar idle (starts on first use)"
  else fail "sidecar not installed - run ./setup.sh"; fi
  # the active engine (what the wizard set pi up for) decides what "down" means; detect tells gufo/Strata apart on :8080
  read -r ACT ACT_PROV ACT_MID < <(python3 -c 'import json,sys;p=json.load(open(sys.argv[1]));print(p["engine"],p["provider"],p["model"])' \
    "${PI_CODING_AGENT_DIR:-$PI_HOME/agent}/harness-profile.json" 2>/dev/null)
  [ -z "${ACT:-}" ] && [ -f "$EXT_DIR/npu-retrieval.ts" ] && { ACT=halogen; ACT_PROV=halogen; ACT_MID=halogen-qwen3.8-flash-next; }
  RUNNING=" $(python3 "$REPO/setup/harness.py" detect 2>/dev/null | python3 -c 'import json,sys;print(" ".join(e["engine"] for e in json.load(sys.stdin)["engines"]))' 2>/dev/null) "
  if [ -f "$EXT_DIR/npu-retrieval.ts" ] && [ "${ACT:-}" = halogen ]; then
    hal_up                          && ok "halogen up (:8731)"     || fail "halogen down - run: ./setup.sh --halogen auto   (or existing start-halogen.sh)"
  elif [ -f "$EXT_DIR/npu-retrieval.ts" ]; then
    hal_up                          && ok "halogen up (:8731)"     || ok "halogen down (active engine is $ACT; NPU tools idle until you switch back with ./setup.sh)"
  else
    # pi-only install (--no-halogen or extensions never installed): halogen is not ours to manage
    hal_up                          && ok "halogen up (:8731, not managed by this install)" || warn "halogen down (pi-only install - fine unless you want the NPU tools)"
  fi
  # voice stack (opt-in --voice): Lemonade on :13305 serves Whisper STT + Kokoro TTS
  if [ "$VOICE" = 1 ] || lem_up; then
    lem_up                          && ok "lemonade up (:13305, voice)" || fail "lemonade down - sudo pacman -S lemonade-server, then: systemctl --user start lemond"
    command -v arecord >/dev/null   && ok "mic capture (arecord)"       || fail "arecord missing (alsa-utils)"
  fi
  # non-halogen engine detection: the harness contract (NPU guard/triage/dedup,
  # vision tower, /health cache+vision fields) exists only on halogen. Other
  # engines degrade gracefully (extensions fail open) but the user should know.
  if curl -sf --max-time 3 http://127.0.0.1:8731/health 2>/dev/null | grep -qv 'prompt_cache'; then
    warn "something that is not halogen is answering on :8731 (no prompt_cache in /health)."
    warn "  auto-guard, the NPU tools (triage, dedup_scan) and image input will not work; /tune knobs partly inert."
    warn "  supported main engine: halogen only (see docs/ENGINES.md)."
  fi
  [ -f "$REPO/start-halogen.sh" ]   && ok "start-halogen.sh ready" || warn "no start-halogen.sh yet (written on first --halogen launch)"
  for e in strata gufo; do
    if python3 -c "import json;json.load(open('$MODELS_JSON'))['providers']['$e']" 2>/dev/null; then
      if [[ "$RUNNING" == *" $e "* ]]; then ok "$e up (:8080)"
      elif [ "$e" = "${ACT:-}" ]; then fail "$e down (the active engine) - ./start.sh starts it"
      else ok "$e configured, not running (not the active engine)"; fi
    fi
  done
  host_checks
  if [ -z "${ACT:-}" ]; then :
  elif [[ "$RUNNING" != *" $ACT "* ]]; then fail "ready check skipped: $ACT is down"
  else
    RC=$(mktemp); T0=$SECONDS
    timeout 120 pi -p --no-skills --no-context-files --provider "$ACT_PROV" --model "$ACT_MID" --thinking low "Reply with exactly: OK" > "$RC" 2>/dev/null &
    while kill -0 $! 2>/dev/null; do [ -t 1 ] && printf '\r  … ready check: asking %s for a reply (%ss, gives up at 120s)' "$ACT" $((SECONDS-T0)); sleep 1; done
    [ -t 1 ] && printf '\r\033[K'
    R=$(grep -c 'OK' "$RC"); rm -f "$RC"
    [ "$R" -ge 1 ] && ok "ready check: $ACT answered" || fail "ready check: no reply from $ACT"
  fi
  exit 0
fi

# first run: ask once whether start.sh should check for a newer halogen image (answer saved; env overrides)
UPD_PREF="${PI_CODING_AGENT_DIR:-$PI_HOME/agent}/harness-update-check"
# -s: an empty file is not an answer (an older harness-update extension left empty files here)
if [ "$NO_HALOGEN" = 0 ] && [ ! -s "$UPD_PREF" ] && [ -t 0 ]; then
  read -r -p "Check for engine updates each time start.sh runs? (one short network call; never installs without asking) [y/N] " A
  mkdir -p "$(dirname "$UPD_PREF")"; case "$A" in [yY]*) echo 1 ;; *) echo 0 ;; esac > "$UPD_PREF"
fi

echo "== 1. pi + extensions =="
if command -v pi >/dev/null; then ok "pi found"; else fail "pi coding agent not found on PATH - install it first, then re-run"; fi
mkdir -p "$EXT_DIR"
printf '%s\n' "$REPO" > "$PI_HOME/agent/harness-repo"  # lets harness-status find scripts/usage-stats.py
N=0
for f in "$REPO"/extensions/*.ts; do
  b=$(basename "$f")
  # NPU extensions are opt-out (--no-halogen): they need a local halogen on :8731
  { [ "$b" = "npu-retrieval.ts" ] || [ "$b" = "auto-guard.ts" ]; } && [ "$NO_HALOGEN" = 1 ] && continue
  if ! cmp -s "$f" "$EXT_DIR/$b"; then cp "$f" "$EXT_DIR/$b"; echo "  installed $b"; N=$((N+1)); fi
done
# drop measured no-op extensions from older installs (deleted from the repo; see extensions/README.md)
for f in context-prune npu-triage ling-tiny-triage; do
  [ -f "$EXT_DIR/$f.ts" ] && rm -f "$EXT_DIR/$f.ts" && echo "  removed $f.ts (measured no-op)"
done
[ -d "$EXT_DIR/optional" ] && rm -rf "$EXT_DIR/optional" && echo "  removed optional/ (archive deleted from the repo)"
[ -d "$EXT_DIR/plan-mode" ] && rm -rf "$EXT_DIR/plan-mode" && echo "  removed plan-mode/ (3 uses in 569 sessions)"
for d in "$REPO"/extensions/*/; do
  [ -d "$d" ] || continue
  b=$(basename "$d")
  if ! diff -rq "$d" "$EXT_DIR/$b" >/dev/null 2>&1; then rm -rf "$EXT_DIR/$b"; cp -r "$d" "$EXT_DIR/$b"; echo "  installed $b/"; N=$((N+1)); fi
done
[ "$N" = 0 ] && ok "all extensions already current ($(ls "$EXT_DIR"/*.ts 2>/dev/null | wc -l) files + $(find "$EXT_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) dirs)"
[ "$N" != 0 ] && ok "installed/updated $N extensions"

echo "== 2. pi providers (models.json) =="
if [ "$NO_HALOGEN" = 1 ]; then
  ok "pi-only install: halogen provider, NPU extensions and container management skipped"
  ok "point pi at your own provider, or at a remote halogen appliance by setting its baseUrl in models.json"
  if [ "$STRATA" = 1 ] || [ "$GUFO" = 1 ]; then
    python3 - "$MODELS_JSON" "$ENG_URL" "$ENG" <<'PY' && ok "$ENG provider configured in $MODELS_JSON" || fail "could not update $MODELS_JSON (left untouched)"
import json, sys, os, urllib.request
path, url, eng = sys.argv[1:4]
os.makedirs(os.path.dirname(path), exist_ok=True)
try: m = json.load(open(path))
except FileNotFoundError: m = {}
# ask the running server for its model id, context and input types; fall back to a text-only guess
mid, ctx, inp = "qwen3.8-flash-next", 131072, ["text"]
try:
    d = json.load(urllib.request.urlopen(url + "/v1/models", timeout=3))["data"][0]
    mid, ctx = d["id"], d.get("meta", {}).get("n_ctx", ctx)
    inp = d.get("architecture", {}).get("input_modalities", inp)
except Exception: print(f"  {eng} not answering - using defaults; re-run once it is up to pick up the real values")
P = m.setdefault("providers", {})
if eng in P: print(f"  {eng} provider exists - left untouched")
else:
    P[eng] = {"baseUrl": url + "/v1", "api": "openai-completions", "apiKey": "dummy",  # set the key here if Strata runs with API_KEY
        "models": [{"id": mid, "name": f"{eng} (local)", "reasoning": True, "input": inp,
                    "contextWindow": ctx, "maxTokens": 8192}]}
    print(f"  added provider: {eng}")
print(f"  model id: {mid}")
json.dump(m, open(path, "w"), indent=1)
sp = os.path.join(os.path.dirname(path), "settings.json")
try: st = json.load(open(sp))
except FileNotFoundError: st = {}
for k, v in (("defaultProvider", eng), ("defaultModel", mid)):
    st.setdefault(k, v)
json.dump(st, open(sp, "w"), indent=2)
PY
    eng_up && ok "$ENG up ($ENG_URL)" || warn "$ENG not answering at $ENG_URL - start it before using pi"
  fi
else
if ! command -v python3 >/dev/null; then fail "python3 required for models.json merge — install python3 and re-run"; else
if python3 - "$MODELS_JSON" <<'PY'
import json, sys, os
path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
# only a missing file starts empty: a parse error must stop here, not overwrite the user's providers
try: m = json.load(open(path))
except FileNotFoundError: m = {}
m.setdefault("providers", {})
def model(id_, name, ctx):
    return {"id": id_, "name": name, "reasoning": True, "contextWindow": ctx,
            "maxTokens": 8192,
            "compat": {"thinkingFormat": "chat-template",
                       "chatTemplateKwargs": {"reasoning_effort": {"$var": "thinking.effort"},
                                              "enable_thinking": {"$var": "thinking.enabled"}}},
            "thinkingLevelMap": {"minimal": None, "low": "low", "medium": "medium", "high": "xhigh", "xhigh": None, "max": None},
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
except FileNotFoundError: s = {}
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
then ok "providers configured in $MODELS_JSON"
else fail "could not parse $MODELS_JSON or settings.json - fix the JSON and re-run (left untouched)"; fi
fi
fi

# screenshots cost tokens by pixel size: cap every image-capable model (re-run covers new models)
if command -v pi >/dev/null && command -v python3 >/dev/null; then
  python3 "$REPO/scripts/set-image-limits.py" || warn "image cap not applied (PI_IMAGE_MAX=off skips it)"
fi

echo "== 2a-2. prompt templates =="
# pi loads personal prompt templates from agent/prompts/; older installs copied to
# ~/.pi/templates, which pi never reads - drop those stale copies
PROMPTS="$PI_HOME/agent/prompts"; mkdir -p "$PROMPTS"
for f in "$REPO"/templates/*.md; do rm -f "$PI_HOME/templates/$(basename "$f")"; done
rmdir "$PI_HOME/templates" 2>/dev/null
T=0
for f in "$REPO"/templates/*.md; do
  [ -f "$f" ] || continue
  b=$(basename "$f")
  if ! diff -q "$f" "$PROMPTS/$b" >/dev/null 2>&1; then cp "$f" "$PROMPTS/$b"; T=$((T+1)); fi
done
ok "prompt templates current ($T updated, $(ls "$PROMPTS" 2>/dev/null | wc -l) total)"
# codebase search is the semble MCP server (mcp.json): verify its runtime is available
if command -v uvx >/dev/null; then ok "uvx found (semble + ddg MCP runnable)"; else warn "uvx not found - semantic search and web search (semble/ddg) unavailable; install uv (https://docs.astral.sh/uv)"; fi

echo "== 2a. skills + pi packages =="
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
  # the structured ask_user_question tool is a package, not a local extension: without it the
  # agent can only ask prose questions, so ambiguous runs silently guess
  if ! python3 -c "import json,sys; sys.exit(0 if any('ask-user-question' in p for p in json.load(open('$PI_HOME/agent/settings.json')).get('packages',[])) else 1)" 2>/dev/null; then
    pi install npm:@juicesharp/rpiv-ask-user-question >/dev/null 2>&1 && ok "ask-user-question installed (pi package)" || warn "ask-user-question install failed - run: pi install npm:@juicesharp/rpiv-ask-user-question"
  else ok "ask-user-question package present"; fi
  # rtk: the pi package rewrites bash commands through the rtk binary (installed separately)
  if ! python3 -c "import json,sys; sys.exit(0 if any('pi-rtk-optimizer' in p for p in json.load(open('$PI_HOME/agent/settings.json')).get('packages',[])) else 1)" 2>/dev/null; then
    pi install npm:@estebanforge/pi-rtk-optimizer >/dev/null 2>&1 && ok "pi-rtk-optimizer installed (pi package)" || warn "pi-rtk-optimizer install failed - run: pi install npm:@estebanforge/pi-rtk-optimizer"
  else ok "pi-rtk-optimizer package present"; fi
  mkdir -p "$HOME/.config/rtk" "$EXT_DIR/pi-rtk-optimizer"
  [ -f "$HOME/.config/rtk/config.toml" ] || cp "$REPO/config/rtk/config.toml" "$HOME/.config/rtk/config.toml"
  [ -f "$EXT_DIR/pi-rtk-optimizer/config.json" ] || cp "$REPO/config/rtk/pi-rtk-optimizer.json" "$EXT_DIR/pi-rtk-optimizer/config.json"
  # rtk < 1.0.1 mangles single-file grep output (reads the line number as a filename)
  RTKV=$(rtk --version 2>/dev/null | awk '{print $2}')
  if [ -n "$RTKV" ] && [ "$(printf '%s\n1.0.1\n' "$RTKV" | sort -V | head -1)" = 1.0.1 ]; then ok "rtk $RTKV"
  else
    case "$(uname -m)" in x86_64) RTKT=x86_64-unknown-linux-musl;; aarch64) RTKT=aarch64-unknown-linux-gnu;; *) RTKT=;; esac
    RTKTMP=$(mktemp -d)
    if [ -n "$RTKT" ] && curl -fsSL "https://github.com/rtk-ai/rtk/releases/download/v1.0.1/rtk-$RTKT.tar.gz" | tar xz -C "$RTKTMP" 2>/dev/null \
       && mkdir -p "$HOME/.local/bin" && install -m755 "$RTKTMP/rtk" "$HOME/.local/bin/rtk"; then
      ok "rtk 1.0.1 installed to ~/.local/bin (was: ${RTKV:-none}); make sure ~/.local/bin is on PATH"
    else warn "rtk 1.0.1 download failed - install it from https://github.com/rtk-ai/rtk/releases"; fi
    rm -rf "$RTKTMP"
  fi
  command -v rtk >/dev/null || warn "rtk not on PATH - add ~/.local/bin to PATH in your shell profile, or pi-rtk-optimizer warns 'rtk binary unavailable'"
fi
[ "$S" = 0 ] && ok "skills current ($(ls "$SKILL_DIR" | wc -l) dirs)" || ok "installed/updated $S skills"

echo "== 2b. MCP servers (mcp.json) =="
MCP_JSON="$PI_HOME/agent/mcp.json"
if [ -f "$MCP_JSON" ]; then
  if [ "$REFRESH_MCP" = 1 ]; then
    if python3 "$REPO/setup/mcp.py" refresh "$MCP_JSON"; then
      ok "mcp.json refreshed (serena/semble exposure; ddg ensured; other servers untouched)"
    else
      warn "could not refresh $MCP_JSON (left untouched)"
    fi
  else
    if python3 "$REPO/setup/mcp.py" migrate "$MCP_JSON"; then
      ok "mcp.json exists (serena/semble migrated, ddg added when missing)"
    else
      warn "could not update $MCP_JSON (left untouched)"
    fi
  fi
elif command -v pi >/dev/null; then
  if python3 "$REPO/setup/mcp.py" write "$MCP_JSON"; then
    ok "MCP servers configured (pi mcp list to inspect)"
  else
    warn "could not write $MCP_JSON"
  fi
else
  warn "pi not found - skipped mcp.json"
fi

echo "== 2c. global AGENTS.md =="
# user-owned: write only when absent, never append to or overwrite an existing one
if [ -f "$PI_HOME/agent/AGENTS.md" ]; then
  ok "AGENTS.md exists (left untouched; see $REPO/config/AGENTS.md for semble/serena guidance to merge)"
elif [ -d "$PI_HOME/agent" ]; then
  cp "$REPO/config/AGENTS.md" "$PI_HOME/agent/AGENTS.md" && ok "installed AGENTS.md (semble/serena guidance)"
fi

echo "== 3. ling-tiny sidecar (:8090) =="
GGUF=""
if [ "$SIDECAR_ON" = 0 ]; then
  ok "sidecar off (opted out: the main model writes commits and summaries; ./setup.sh --sidecar turns it on)"
  tiny_up && systemctl --user stop ling-tiny.service 2>/dev/null && ok "stopped the running sidecar (frees ~8 GiB GPU memory)"
elif tiny_up; then
  ok "sidecar is up"
  [ -f "$SC_PREF" ] || warn "sidecar is on by default (~8 GiB GPU memory); opt out with ./setup.sh --no-sidecar"
elif [ -f "$HOME/.config/systemd/user/ling-tiny.service" ]; then
  # unit exists from a previous setup: leave it down; the commit tool starts it on first use (cloud-only sessions never load it)
  systemctl --user daemon-reload 2>/dev/null
  ok "sidecar idle: starts on first use, unloads after 10 min idle"
else
  fail "sidecar not running"
  GGUF="$(ls "$HOME"/.models/ling3.0-tiny/*.gguf "$REPO"/models/ling3.0-tiny/*.gguf 2>/dev/null | head -1)"
  # a terminal gets asked; no terminal (scripts, the wizard - which already asked) downloads
  if [ -z "${GGUF:-}" ] && [ -t 0 ]; then
    read -r -p "  download Ling-3.0-tiny (~4.5 GB) for the sidecar? (n = skip; ./setup.sh --no-sidecar turns it off) [Y/n] " A
    case "$A" in [nN]*) GGUF=skip ;; esac
  fi
  if [ "${GGUF:-}" = skip ]; then
    GGUF=""; warn "sidecar download skipped - commits/summaries fall back to the main model until it runs"
  elif [ -z "${GGUF:-}" ]; then
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
      if [ -z "$WANT" ]; then
        warn "checksum unavailable (Hugging Face API unreachable) - download NOT verified"
      elif [ "$WANT" != "$GOT" ]; then
        fail "sha256 MISMATCH (want $WANT, got $GOT) - deleting bad file, fetch manually"
        rm -f "$GGUF"; GGUF=""
      else ok "sha256 verified"; fi
    fi
  fi
  if [ -n "${LLS:-}" ] && [ -n "${GGUF:-}" ]; then
    setsid nohup "$LLS" -a ling3.0-tiny -m "$GGUF" -ngl 99 -c 131072 -b $UB_TINY -ub $UB_TINY --jinja \
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
if [ "$SIDECAR_ON" = 1 ] && command -v systemctl >/dev/null && systemctl --user status >/dev/null 2>&1; then
  UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
  UNIT_GGUF="${GGUF:-$(ls "$HOME"/.models/ling3.0-tiny/*.gguf 2>/dev/null | head -1)}"
  if [ -n "$UNIT_GGUF" ] && [ -n "${LLS:-}" ]; then
    cat > "$UNIT_DIR/ling-tiny.service" <<UNIT
[Unit]
Description=Ling-3.0-tiny sidecar (llama.cpp)
After=network.target

[Service]
ExecStart=$LLS -a ling3.0-tiny -m $UNIT_GGUF -ngl 99 -c 131072 -b $UB_TINY -ub $UB_TINY --jinja --host 127.0.0.1 --port $TINY_PORT --metrics --sleep-idle-seconds 600
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    if ! tiny_up; then
      ok "sidecar unit installed (starts on first use, unloads after 10 min idle, NOT enabled at boot)"
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
  elif [ -n "$(docker ps -q --filter publish=8080 2>/dev/null)" ] || pgrep -f 'serve/server.py --engine strata' >/dev/null; then
    # one big engine at a time: halogen beside gufo/Strata fights over the same GPU heap (device-lost)
    fail "gufo or Strata holds the GPU memory halogen needs - not launching. stop it first (scripts/stop-all.sh) or switch with ./setup.sh"
  else
    [ "$HALOGEN_CKPT" = "auto" ] && HALOGEN_CKPT=$(find_ckpt)
    if [ -z "$HALOGEN_CKPT" ] || [ ! -f "$HALOGEN_CKPT" ]; then
      # no checkpoint found: offer to fetch exactly the required file set (~124 GB, resumable)
      echo "  no .hgn checkpoint found (looked in ., /models, ~/models, ~/Downloads, LLMBench/models)."
      bash "$REPO/scripts/download-halogen.sh" || { fail "no checkpoint - re-run ./setup.sh --halogen auto to resume"; exit 1; }
      HALOGEN_CKPT="$HOME/models/halogen-qwen3.8-flash-next/qwen38-flash-next-v2.hgn"
    fi
    if [ -z "$HALOGEN_CKPT" ] || [ ! -f "$HALOGEN_CKPT" ]; then
      fail "no .hgn checkpoint found (looked in ., /models, ~/models, ~/Downloads, LLMBench/models) - pass it: ./setup.sh --halogen /path/to/checkpoint.hgn"
    else
      IMG=$(bash "$REPO/server/start-halogen.sh" --print-image)
      docker image inspect "$IMG" >/dev/null 2>&1 \
        || { echo "  pulling halogen image (first run, ~GB) ..."; docker pull "$IMG"; }
      W=$(dirname "$HALOGEN_CKPT")
      RENDER_GID=$(getent group render | cut -d: -f3); VIDEO_GID=$(getent group video | cut -d: -f3)
      echo "  launching halogen ($HALOGEN_CKPT, cache ON) ..."
      # also drop a one-command restart script (halogen is NOT auto-started at boot on purpose: full-ctx
      # launches need a fresh allocator - see README. 65k/2-slot is the safe default below.)
      # a link, not a copy: a copy kept the old image pin after upgrade-halogen.sh moved it
      ln -sfn server/start-halogen.sh "$REPO/start-halogen.sh"; ok "linked $REPO/start-halogen.sh (restart any time)"
      setsid nohup "$REPO/start-halogen.sh" > "$REPO/halogen.log" 2>&1 < /dev/null &
      for i in $(seq 1 30); do sleep 10; hal_up && { ok "halogen up (~${i}0s)"; break; }; done
      hal_up || fail "not healthy after 5 min - check $REPO/halogen.log"
    fi
  fi
elif [ -n "${HARNESS_STARTS_ENGINE:-}" ]; then
  ok "halogen starts after the install (the wizard's last step)"
elif [ -n "$(find_ckpt)" ]; then
  warn "halogen down but a checkpoint exists - start it: ./setup.sh --halogen auto"
else
  warn "halogen down (optional: main model). start with: ./setup.sh --halogen auto"
fi

echo "== 5. host checks =="
host_checks

echo
echo "done. doctor check any time:  ./setup.sh --doctor"
if [ "$STRATA" = 1 ] || [ "$GUFO" = 1 ]; then
  echo "use it:  pi --provider $ENG --model <model id above>   (no NPU tools on $ENG, see docs/ENGINES.md)"; exit 0
fi
[ "$NO_HALOGEN" = 1 ] && exit 0  # the smoke test below is for halogen
echo "smoke test:"
echo "  pi -p --no-skills --no-context-files --provider halogen \\"
echo "    --model halogen-qwen3.8-flash-next --thinking medium \\"
echo "    --tools commit,bash,read,write \\"
echo '    "List the 5 largest source files and write them to SMOKE.md"'
