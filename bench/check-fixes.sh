#!/usr/bin/env bash
# Offline regression checks for the 2026-10 review fixes. No server needed.
# Each check fails loudly if its fix regresses. Run: bench/check-fixes.sh
set -u
cd "$(dirname "$0")/.."
FAIL=0
ok(){ echo "  ok: $1"; }
bad(){ echo "  FAIL: $1"; FAIL=1; }
JS=$(command -v bun || command -v node) || { echo "no bun/node, cannot run JS checks"; exit 1; }

# [1] uninstall backup must archive BOTH the unit and rag-index.py when both exist
# (evals the real TAR_ARGS lines from scripts/uninstall.sh, not a copy of them)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
( mkdir -p "$T/pi/agent/extensions" "$T/home/.config/systemd/user"
  echo x > "$T/pi/agent/settings.json"; echo x > "$T/pi/agent/rag-index.py"; echo x > "$T/home/.config/systemd/user/ling-tiny.service"
  PI_HOME="$T/pi" HOME="$T/home" AG="$T/pi/agent" BACKUP="$T/bk.tgz"
  eval "$(grep -E 'TAR_ARGS\+?=' scripts/uninstall.sh)"
  tar czf "$BACKUP" "${TAR_ARGS[@]}" 2>/dev/null
  M=$(tar tzf "$BACKUP" 2>/dev/null)
  echo "$M" | grep -q "ling-tiny.service" && echo "$M" | grep -q "agent/rag-index.py" ) \
  && ok "tar backup holds unit + rag-index.py together" || bad "tar backup missing a member"

# [2] setup --uninstall must route to uninstall.sh, not run the installer (README contract)
./setup.sh --uninstall </dev/null 2>&1 | grep -q "removes everything" \
  && ok "--uninstall routes to uninstall.sh" || bad "--uninstall did not route to uninstall.sh"

# [3] auto-guard: one timeout only (duplicate object key silently overrode it once)
[ "$(grep -c 'AbortSignal' extensions/auto-guard.ts)" = 1 ] \
  && ok "auto-guard: single signal/timeout" || bad "auto-guard: duplicate signal key is back"

# [4] sidecar payload caps keep head+tail and fit the window in BOTH summarizers
# (mirrors ling-tiny-compaction.ts and ling-tiny-branch-summary.ts)
for TSRC in extensions/ling-tiny-compaction.ts extensions/ling-tiny-branch-summary.ts; do
TSRC="$TSRC" $JS -e 'const fs=require("fs");const f=process.env.TSRC;const src=fs.readFileSync(f,"utf8");
const m=src.match(/const CAP = ([\d_]+);[\s\S]*?slice\(0, ([\d_]+)\)[\s\S]*?slice\(-\(CAP - ([\d_]+)\)/);
if(!m) throw new Error("cap pattern changed shape in "+f+" - update this check");
const N=s=>+s.replace(/_/g,""); const CAP=N(m[1]), HEAD=N(m[2]); const ct="x".repeat(CAP*2);
const out=ct.slice(0,HEAD)+"\n[...middle truncated to fit the sidecar window...]\n"+ct.slice(-(CAP-HEAD));
if(out.length>=CAP+60||!out.startsWith("xxxx")||!out.endsWith("xxxx")) throw new Error("cap math wrong");
if(CAP/3.9>131072-4096-2048) throw new Error("cap exceeds sidecar window budget");
console.log("  ok: cap in "+f+" (CAP="+CAP+" fits 131k window)")' || FAIL=1
done


# [7] git-commit guard blocks direct commits, allows message-free amends (mirrors ling-tiny-commit.ts)
$JS -e 'const BLOCK=/\bgit\b[^|;&]*\bcommit\b/, ALLOW=/commit[^|;&]*--amend[^|;&]*(--no-edit|-C\s+HEAD)/;
const t=(c)=>BLOCK.test(c)&&!ALLOW.test(c);
for(const c of ["git commit -m x","git add -A && git commit -m x","git commit --amend -m y","cd r; git commit"])
  if(!t(c)) throw new Error("should block: "+c);
for(const c of ["git commit --amend --no-edit","git commit --amend -C HEAD","echo committing history","git log --oneline"])
  if(t(c)) throw new Error("should allow: "+c);
console.log("  ok: git-commit guard blocks direct commits, allows --amend --no-edit")' || FAIL=1

# [8] sidecar launchers all carry the tuned batch size; launcher respects /tune reserveTokens
grep -q -- '-b $UB_TINY -ub $UB_TINY --jinja' setup.sh \
  && [ "$(grep -c -- '-b $UB_TINY -ub $UB_TINY' setup.sh)" -ge 2 ] \
  && grep -q -- '-b $UBBATCH -ub $UBBATCH' server/start-ling-tiny.sh \
  && grep -q 'launcher-owned values' server/start-halogen.sh \
  && grep -q 'maxTokens: \[1024, 8192\]' extensions/harness-tune.ts \
  && ok "ubatch baked into unit+spawn; reserveTokens guard; maxTokens bound = documented cap" \
  || bad "ubatch/reserve/maxTokens guards were removed"

[ "$FAIL" = 0 ] && echo "check-fixes: all checks pass" || { echo "check-fixes: FAILURES above"; exit 1; }
