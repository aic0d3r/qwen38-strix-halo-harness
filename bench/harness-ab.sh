#!/usr/bin/env bash
# One-command harness A/B: baseline vs (interrupt + resume) on the standard
# survey task. Re-run after engine/extension updates; ~25 min.
#   bench/harness-ab.sh <repo-dir> [baseline-only|resume-only]
set -u
REPO="${1:?usage: harness-ab.sh <repo-dir> [phase]}"
PHASE="${2:-all}"
TASK_FILE="$(dirname "$0")/harness-ab-task.txt"
OUT=/tmp/harness-ab-$$; mkdir -p $OUT
RESERVE=50000   # force compaction so the sidecar path fires
AG="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
# the bench resets the repo (checkout + clean): never run it where it can eat work
cd "$REPO" || exit 1
DIRTY=$(git status --porcelain | grep -vE '^\?\? (PROGRESS\.md|BIG_TEST_REPORT\.md|SURVEY\.md)$')
[ -z "$DIRTY" ] || { echo "refusing: $REPO has uncommitted or untracked files (the bench resets it):" >&2; echo "$DIRTY" >&2; exit 1; }
set_repo(){ cd "$REPO" || exit 1; git checkout -- . 2>/dev/null; git clean -qfd -e .rag -e .pi -e .serena; rm -f PROGRESS.md BIG_TEST_REPORT.md SURVEY.md; }
# setting $1 = reserve value, or "" to drop the key (restores a setting that was never there)
set_reserve(){ python3 -c "
import json, sys
p = '$AG/settings.json'; s = json.load(open(p)); c = s.setdefault('compaction', {})
if sys.argv[1]: c['reserveTokens'] = int(sys.argv[1])
else: c.pop('reserveTokens', None)
json.dump(s, open(p, 'w'), indent=2)" "$1"; }
OLD_RESERVE=$(python3 -c "import json; print(json.load(open('$AG/settings.json')).get('compaction', {}).get('reserveTokens', ''))" 2>/dev/null)
# Ctrl-C mid-run must not strand the extensions in extensions.bak or leave the bench reserve
restore(){
  mv "$AG"/extensions.bak/*.ts "$AG/extensions/" 2>/dev/null
  mv "$AG/extensions.bak/subagent" "$AG/extensions/" 2>/dev/null
  set_reserve "$OLD_RESERVE"
}
trap restore EXIT

baseline(){
  set_repo
  mkdir -p "$AG/extensions.bak" && mv "$AG"/extensions/*.ts "$AG/extensions.bak/" 2>/dev/null
  # dirs are not *.ts: subagent/ must go too or the "baseline" arm still runs it
  mv "$AG/extensions/subagent" "$AG/extensions.bak/" 2>/dev/null
  local t0=$(date +%s); timeout 2400 pi -p --no-skills --no-context-files \
    --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
    "$(cat $TASK_FILE)" > $OUT/baseline.txt 2>&1; local rc=$?; local t1=$(date +%s)
  mv "$AG"/extensions.bak/*.ts "$AG/extensions/" 2>/dev/null
  mv "$AG/extensions.bak/subagent" "$AG/extensions/" 2>/dev/null
  echo "baseline: $((t1-t0))s rc=$rc report=$(wc -c < "$REPO"/BIG_TEST_REPORT.md 2>/dev/null || echo 0)B"
}
resume(){
  set_repo
  # kill only our own run (the old pkill -f 'pi -p' also hit the user's other sessions)
  local t0=$(date +%s); timeout -k 2 600 pi -p --no-skills --no-context-files \
    --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
    --tools commit,mcp__semble__search,triage,bash,read,write \
    "$(cat $TASK_FILE)" > $OUT/a1.txt 2>&1; local t1=$(date +%s)
  sleep 2
  local r0=$(date +%s); timeout 1800 pi -p --no-skills --no-context-files \
    --provider halogen --model halogen-qwen3.8-flash-next --thinking medium \
    --tools commit,mcp__semble__search,triage,bash,read,write \
    "Continue." > $OUT/a2.txt 2>&1; local r1=$(date +%s)
  echo "resume: kill@600s? a1=$((t1-t0))s + a2=$((r1-r0))s = total $(( (t1-t0)+(r1-r0) ))s report=$(wc -c < "$REPO"/BIG_TEST_REPORT.md 2>/dev/null || echo 0)B"
}
case "$PHASE" in
  baseline-only) set_reserve $RESERVE; baseline ;;
  resume-only)   set_reserve $RESERVE; resume ;;
  *) set_reserve $RESERVE; baseline; resume ;;
esac
echo "outputs in $OUT"
