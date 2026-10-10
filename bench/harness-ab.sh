#!/usr/bin/env bash
# One-command harness A/B: baseline vs (interrupt + resume) on the standard
# survey task. Re-run after engine/extension updates; ~25 min.
#   bench/harness-ab.sh <repo-with-.rag> [baseline-only|resume-only]
set -u
REPO="${1:?usage: harness-ab.sh <repo-with-.rag> [phase]}"
PHASE="${2:-all}"
TASK_FILE="$(dirname "$0")/harness-ab-task.txt"
OUT=/tmp/harness-ab-$$; mkdir -p $OUT
RESERVE=50000   # force compaction so the sidecar path fires
set_repo(){ cd "$REPO" && git checkout -- . 2>/dev/null; git clean -qfd -e .rag -e .pi -e .serena; rm -f PROGRESS.md BIG_TEST_REPORT.md SURVEY.md; }
set_reserve(){ python3 -c "
import json
s = json.load(open('$HOME/.pi/agent/settings.json'))
s.setdefault('compaction', {})['reserveTokens'] = $RESERVE
json.dump(s, open('$HOME/.pi/agent/settings.json', 'w'), indent=2)"; }
restore_reserve(){ python3 -c "
import json
s = json.load(open('$HOME/.pi/agent/settings.json'))
s.setdefault('compaction', {})['reserveTokens'] = 10240
json.dump(s, open('$HOME/.pi/agent/settings.json', 'w'), indent=2)"; }

baseline(){
  set_repo
  mkdir -p ~/.pi/agent/extensions.bak && mv ~/.pi/agent/extensions/*.ts ~/.pi/agent/extensions.bak/ 2>/dev/null
  # dirs are not *.ts: subagent/ must go too or the "baseline" arm still runs it
  mv ~/.pi/agent/extensions/subagent ~/.pi/agent/extensions.bak/ 2>/dev/null
  local t0=$(date +%s); timeout 2400 pi -p --no-skills --no-context-files \
    --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
    "$(cat $TASK_FILE)" > $OUT/baseline.txt 2>&1; local rc=$?; local t1=$(date +%s)
  mv ~/.pi/agent/extensions.bak/*.ts ~/.pi/agent/extensions/ 2>/dev/null
  mv ~/.pi/agent/extensions.bak/subagent ~/.pi/agent/extensions/ 2>/dev/null
  echo "baseline: $((t1-t0))s rc=$rc report=$(wc -c < "$REPO"/BIG_TEST_REPORT.md 2>/dev/null || echo 0)B"
}
resume(){
  set_repo
  local t0=$(date +%s); timeout 600 pi -p --no-skills --no-context-files \
    --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
    --tools commit,codebase_search,triage,bash,read,write \
    "$(cat $TASK_FILE)" > $OUT/a1.txt 2>&1; local t1=$(date +%s)
  pkill -9 -f 'p[i] -p' 2>/dev/null; sleep 2
  local r0=$(date +%s); timeout 1800 pi -p --no-skills --no-context-files \
    --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
    --tools commit,codebase_search,triage,bash,read,write \
    "Continue." > $OUT/a2.txt 2>&1; local r1=$(date +%s)
  echo "resume: kill@600s? a1=$((t1-t0))s + a2=$((r1-r0))s = total $(( (t1-t0)+(r1-r0) ))s report=$(wc -c < "$REPO"/BIG_TEST_REPORT.md 2>/dev/null || echo 0)B"
}
case "$PHASE" in
  baseline-only) set_reserve; baseline; restore_reserve ;;
  resume-only)   set_reserve; resume; restore_reserve ;;
  *) set_reserve; baseline; resume; restore_reserve ;;
esac
restore_reserve
echo "outputs in $OUT"
