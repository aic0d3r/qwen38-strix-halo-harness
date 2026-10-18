#!/usr/bin/env bash
# stop the engines the harness starts (halogen, gufo, Strata) + the ling-tiny sidecar; leaves pi and
# servers it does not start (Ollama, vLLM, LM Studio, your own llama-server) alone.
#   scripts/stop-all.sh            stop both, desktop notification when done
#   scripts/stop-all.sh --install  add "Stop/Start LLM servers" to the app menu + ~/Desktop
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "${1:-}" = --install ]; then
  mk() { # file name comment script icon
    f="$HOME/.local/share/applications/$1.desktop"
    printf '[Desktop Entry]\nType=Application\nName=%s\nComment=%s\nExec=%s\nIcon=%s\nTerminal=false\nCategories=Utility;\n' "$2" "$3" "$4" "$5" > "$f"
    chmod +x "$f"; [ -d "$HOME/Desktop" ] && cp "$f" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/$1.desktop"
    echo "installed: $f"
  }
  mk stop-llm-servers "Stop LLM servers" "Stop halogen, gufo, Strata and the ling-tiny sidecar" "$HERE/stop-all.sh" process-stop
  mk start-llm-servers "Start LLM servers" "Start the engine you picked in setup and the ling-tiny sidecar (no pi)" "$HERE/start-servers.sh" media-playback-start
  exit 0
fi
out=""
# by published port, not image name: digest-pinned containers show as a bare image id in docker ps
for e in halogen:8731 gufo:8080; do
  ids=$(docker ps -q --filter "publish=${e#*:}" 2>/dev/null)
  [ -n "$ids" ] && docker stop -t 30 $ids >/dev/null 2>&1 && out+="${e%%:*} stopped. "
done
# Strata runs as a plain process (serve/server.py), not a container
if pkill -f 'serve/server.py --engine strata'; then
  for _ in $(seq 1 15); do pgrep -f 'serve/server.py --engine strata' >/dev/null || break; sleep 2; done
  out+="Strata stopped. "
fi
[ -z "$out" ] && out="no engine was running. "
if systemctl --user is-active --quiet ling-tiny.service 2>/dev/null; then systemctl --user stop ling-tiny.service && out+="ling-tiny stopped. "
elif pkill -f 'llama-server -a ling3.0-tiny'; then out+="ling-tiny stopped. "; else out+="ling-tiny was not running. "; fi
sleep 3
gtt=$(cat /sys/class/drm/card*/device/mem_info_gtt_used 2>/dev/null | sort -n | tail -1)
# >2 GiB still held with nothing running = a previous engine leaked it (halogen issue #79)
[ "${gtt:-0}" -gt 2147483648 ] && out+="GPU memory still held ($((gtt/1073741824)) GiB): reboot before the next engine start."
echo "$out"; notify-send -i process-stop "LLM servers" "$out" 2>/dev/null
