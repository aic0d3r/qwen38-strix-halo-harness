#!/usr/bin/env bash
# upgrade-strata.sh - update Strata with its own ./update.sh, restart it exactly the way it was running,
# run the acceptance battery, and roll back to the previous commit if anything fails.
#   scripts/upgrade-strata.sh      (Strata must be running: the restart reuses its folder and command line)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8080
URL=http://127.0.0.1:$PORT
LOG=/tmp/strata-upgrade.log

PID=$(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
if [ -z "$PID" ] || ! tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null | grep -q 'serve/server.py'; then
  echo "FAIL: Strata is not running on :$PORT - start it first (the upgrade restarts it the same way)"; exit 1
fi
DIR=$(readlink "/proc/$PID/cwd")
mapfile -d '' CMD < "/proc/$PID/cmdline"
[ -f "$DIR/update.sh" ] && [ -d "$DIR/.git" ] || { echo "FAIL: $DIR is not a git clone of Strata - use its own UPDATE instead"; exit 1; }
[ -z "$(git -C "$DIR" status --porcelain --untracked-files=no)" ] || { echo "FAIL: $DIR has local changes to tracked files - commit or stash them first"; exit 1; }
OLD=$(git -C "$DIR" rev-parse HEAD)
CUR=$(git -C "$DIR" describe --tags 2>/dev/null || echo "${OLD:0:9}")
BRANCH=$(git -C "$DIR" symbolic-ref --short -q HEAD || echo main)

stop(){
  local p; p=$(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
  [ -n "$p" ] && kill "$p" 2>/dev/null
  for _ in $(seq 1 30); do ss -ltnH "sport = :$PORT" | grep -q . || return 0; sleep 2; done
  [ -n "$p" ] && kill -9 "$p" 2>/dev/null; sleep 2
}
# BROWSER=true: Strata's --open would pop a browser tab on every restart
start(){ (cd "$DIR" && BROWSER=true setsid nohup "${CMD[@]}" > "$1" 2>&1 < /dev/null &); }
wait_up(){ for _ in $(seq 1 80); do sleep 15; curl -sf -m 3 "$URL/v1/models" >/dev/null && return 0; done; return 1; }
rollback(){
  echo "FAIL: $1 - rolling back to $CUR"
  stop
  git -C "$DIR" checkout -q -B "$BRANCH" "$OLD" \
    && (cd "$DIR" && sh ./setup.sh --update --yes < /dev/null > "$LOG.rollback" 2>&1) \
    || echo "      could not rebuild $CUR (log: $LOG.rollback)"
  start "$LOG.rollback-server"
  echo "      restarted on $CUR (log: $LOG.rollback-server)"
  exit 1
}

echo "[1/4] stopping Strata $CUR (pid $PID, $DIR)"
stop
echo "[2/4] ./update.sh: git pull + engine and packages (log: $LOG)"
(cd "$DIR" && sh ./update.sh --yes < /dev/null > "$LOG" 2>&1) || { tail -5 "$LOG"; rollback "update.sh failed (log: $LOG)"; }
NEW=$(git -C "$DIR" describe --tags 2>/dev/null || git -C "$DIR" rev-parse --short HEAD)
echo "[3/4] restarting on $NEW (loading the model takes a minute or two)"
start "$LOG.server"
wait_up || { tail -5 "$LOG.server"; rollback "not answering after 20 min (log: $LOG.server)"; }
echo "[4/4] answer + decode check"
python3 "$REPO/scripts/engine-acceptance.py" "$URL" 15 || rollback "acceptance"
echo "OK: Strata $CUR -> $NEW, running (previous commit kept as rollback point: git -C $DIR checkout $OLD)"
