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


# [7] git-commit guard blocks direct commits, allows message-free amends (regexes read from the real file)
$JS -e 'const src=require("fs").readFileSync("extensions/ling-tiny-commit.ts","utf8");
const re=[...src.matchAll(/if \((!?)\/(.+?)\/\.test\(cmd\)\) return;/g)];
if(re.length!==2) throw new Error("commit guard changed shape - update this check");
const BLOCK=new RegExp(re[0][2]), ALLOW=new RegExp(re[1][2]);
const t=(c)=>BLOCK.test(c)&&!ALLOW.test(c);
for(const c of ["git commit -m x","git add -A && git commit -m x","git commit --amend -m y","cd r; git commit"])
  if(!t(c)) throw new Error("should block: "+c);
for(const c of ["git commit --amend --no-edit","git commit --amend -C HEAD","echo committing history","git log --oneline","git log -- src/commit.ts","git log --grep commit"])
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

# [9] the NPU retrieval pipeline stays dead: no shipped runner asks pi for the removed
# codebase_search tool, and the upgrade battery must not gate on the idle reranker
! grep -rq --include='*.sh' --include='*.ts' codebase_search setup.sh server scripts extensions bench/harness-ab.sh \
  && ! grep -q 't("/v1/rerank"' scripts/upgrade-halogen.sh \
  && ok "no codebase_search in shipped runners; upgrade battery free of the idle reranker" \
  || bad "retired NPU search crept back into a runner or the upgrade gate"

# [10] crash-recovery resume gate (the real exported function; bun imports .ts directly)
if command -v bun >/dev/null; then bun -e 'import { isContinuation as c } from "./extensions/progress-tracker.ts";
const cases = [["continue", true], ["Continue where we left off. It crashed mid-way through the migration; pick up from the last file and keep the same style.", true],
  ["finish the login page", true], ["Build a REST API for invoices with pagination, auth middleware, and integration tests; we will continue the frontend later once that is done.", false], ["hi", false]];
for (const [t, want] of cases) if (c(t) !== want) throw new Error(`isContinuation(${JSON.stringify(t.slice(0, 40))}) != ${want}`);
console.log("  ok: resume gate (long continue keeps the note, long new task drops it)")' || FAIL=1
else echo "  skip: resume gate check needs bun"; fi

# [11] launcher reserveTokens sync (the real heredoc): a 262k-day /tune value must not survive a 65k day
R=$(T2=$(mktemp -d); echo '{"compaction":{"reserveTokens":60144}}' > "$T2/settings.json"; echo '{}' > "$T2/models.json"
  awk '/python3 - "\$CTX" <<.PY./{f=1;next} f&&/^PY$/{exit} f' server/start-halogen.sh > "$T2/s.py"
  PI_CODING_AGENT_DIR="$T2" python3 "$T2/s.py" 65536 >/dev/null; python3 -c "import json;print(json.load(open('$T2/settings.json'))['compaction']['reserveTokens'])"; rm -rf "$T2")
[ "$R" = 10240 ] && ok "launcher resets a reserve that no longer fits the window (60144 -> 10240 at 65k)" \
  || bad "launcher kept reserve $R on a 65k window"

[ "$FAIL" = 0 ] && echo "check-fixes: all checks pass" || { echo "check-fixes: FAILURES above"; exit 1; }
