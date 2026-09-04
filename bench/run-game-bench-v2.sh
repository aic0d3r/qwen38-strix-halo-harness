#!/usr/bin/env bash
# v2 (Aug 24): taxonomy watchdog kills off-contract rolls early; SUCCESS requires
# exact file set + node --check clean on all 8 js. Config otherwise = run-with-retry.sh.
L=~/LLMBench/results/qwen38-27b/game-ladder
DIR=$1 NAME=$2 LVL=$3; shift 3
mkdir -p $L/$DIR
for i in $(seq 1 30); do curl -sf -m2 localhost:${PORT:-8080}/health > /dev/null 2>&1 && break; sleep 5; done
curl -sf -m2 localhost:${PORT:-8080}/health > /dev/null || { echo "=== $NAME ABORT: server unhealthy ===" >> $L/ladder.log; exit 1; }
for TRY in 1 2 3 4 5; do
  cd $L/$DIR || exit 1
  P="$(printf '%s ' "$@") [attempt $TRY $(date +%s)]"
  T0=$(date +%s)
  PROVIDER_ARGS=""; [ -n "${PROVIDER:-}" ] && PROVIDER_ARGS="--provider $PROVIDER"
  timeout 5400 pi -p --no-skills --no-context-files $PROVIDER_ARGS --thinking $LVL --name $NAME "$P" > $L/run-$NAME.txt 2>&1 &
  PID=$!
  # watchdog: off-contract file => kill immediately. First 2min poll at 10s to reap
  # instant-EOS basin tries (~1-in-4 rolls: pi exits rc=0 having produced nothing).
  ELAPSED=0; TICK=10
  while kill -0 $PID 2>/dev/null; do
    sleep $TICK; ELAPSED=$((ELAPSED+TICK)); [ $ELAPSED -ge 120 ] && TICK=45
    # basin exit: pi already dead but loop caught it this tick => nothing to wait for
    BAD=$(find $L/$DIR -type f ! -name '*.txt' | sed "s|$L/$DIR/||" | grep -vE '^(index\.html|css/styles\.css|js/(config|particles|bricks|balls|powerups|states|shop|main)\.js)$' | head -1)
    [ -n "$BAD" ] && { echo "=== $NAME try=$TRY watchdog: off-contract '$BAD', killing ===" >> $L/ladder.log; kill -9 $PID 2>/dev/null; pkill -9 -f -- "--name $NAME" 2>/dev/null; }
    # early-exit on clean (v2.1): contract complete + syntax clean => artifact done, kill the polish loop
    DONE=1
    for F in config particles bricks balls powerups states shop main; do
      { [ -f js/$F.js ] && node --check js/$F.js 2>/dev/null; } || { DONE=0; break; }
    done
    { [ -f index.html ] && [ -f css/styles.css ]; } || DONE=0
    if [ "$DONE" = 1 ]; then
      echo "=== $NAME try=$TRY early-exit: contract clean ($(date +%H:%M)), ending polish ===" >> $L/ladder.log
      kill -9 $PID 2>/dev/null; pkill -9 -f -- "--name $NAME" 2>/dev/null
    fi
  done
  wait $PID; RC=$?
  pkill -9 -f -- "--name $NAME" 2>/dev/null; sleep 2   # reap any basin-orphaned pi from this try
  W=$(( ($(date +%s)-T0)/60 ))
  [ $RC -eq 137 ] && RC=99
  # honest gate: exact set + syntax clean
  MISS=""; SYN=""
  for F in config particles bricks balls powerups states shop main; do
    [ -f js/$F.js ] || MISS="$MISS js/$F.js"
    node --check js/$F.js 2>/dev/null || SYN="$SYN js/$F.js"
  done
  [ -f index.html ] && [ -f css/styles.css ] || MISS="$MISS (html/css)"
  if [ -z "$MISS" ] && [ -n "$SYN" ]; then
    # syn-only failure: all files present, some fail node --check. Fix in place
    # (model's own fix-loop on its own output; artifact stays single-lineage).
    echo "=== $NAME try=$TRY syn-fix: $SYN, running fix pass ===" >> $L/ladder.log
    FIXFILES="$SYN"
    timeout -k 10 1200 pi -p --no-skills --no-context-files --thinking $LVL --name $NAME-fix "These files fail node --check:$FIXFILES. Run node --check on each to get the exact error, view the offending region, then fix SURGICALLY with the edit tool (or bash sed/append): patch only the broken lines. If a file was truncated mid-write, reconstruct just the missing tail and append it - never rewrite the whole file with the write tool (that is what truncated it). Then run: node --check$FIXFILES. Repeat until every file passes, then reply FIXED." > $L/run-$NAME-fix.txt 2>&1
    pkill -9 -f -- "--name $NAME-fix" 2>/dev/null; sleep 2
    SYN=""
    for F in config particles bricks balls powerups states shop main; do
      node --check js/$F.js 2>/dev/null || SYN="$SYN js/$F.js"
    done
    if [ -n "$SYN" ]; then
      echo "=== $NAME try=$TRY syn-fix failed:[$SYN], full retry ===" >> $L/ladder.log
    else
      echo "=== $NAME try=$TRY syn-fix clean ===" >> $L/ladder.log
    fi
  fi
  if [ -z "$MISS" ] && [ -z "$SYN" ]; then
    echo "=== $NAME try=$TRY SUCCESS wall=${W}min (v2 gate) ===" >> $L/ladder.log; exit 0
  fi
  echo "=== $NAME try=$TRY fail rc=$RC miss=[$MISS] syn=[$SYN] wall=${W}min, retry ===" >> $L/ladder.log
  rm -rf $L/$DIR/*; sleep 3
done
echo "=== $NAME FAILED all tries ===" >> $L/ladder.log
