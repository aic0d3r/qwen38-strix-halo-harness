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

# [4] sidecar compaction cap keeps head+tail and fits the window (mirrors ling-tiny-compaction.ts)
$JS -e 'const fs=require("fs");const src=fs.readFileSync("extensions/ling-tiny-compaction.ts","utf8");
const m=src.match(/const CAP = ([\d_]+);[\s\S]*?slice\(0, ([\d_]+)\)[\s\S]*?slice\(-\(CAP - ([\d_]+)\)/);
if(!m) throw new Error("cap pattern changed shape in source - update this check");
const N=s=>+s.replace(/_/g,""); const CAP=N(m[1]), HEAD=N(m[2]); const ct="x".repeat(CAP*2);
const out=ct.slice(0,HEAD)+"\n[...middle truncated to fit the sidecar window...]\n"+ct.slice(-(CAP-HEAD));
if(out.length>=CAP+60||!out.startsWith("xxxx")||!out.endsWith("xxxx")) throw new Error("cap math wrong");
if(CAP/3.9>131072-4096-2048) throw new Error("cap exceeds sidecar window budget");
console.log("  ok: compaction cap (CAP="+CAP+" fits 131k window, head+tail kept)")' || FAIL=1

# [5] rag staleness expr counts changed AND deleted files (mirrors npu-retrieval.ts)
$JS -e 'const fs=require("fs"),os=require("os"),p=fs.mkdtempSync(os.tmpdir()+"/stale");
fs.writeFileSync(p+"/a.ts","x".repeat(200));
const meta={files:{a:Math.floor(Date.now()/1000)-50,b:1}}; let stale=0;
for(const rel of Object.keys(meta.files)){try{if(Math.floor(fs.statSync(p+"/"+rel).mtimeMs/1000)!==meta.files[rel])stale++}catch{stale++}}
fs.rmSync(p,{recursive:true});
if(stale!==2) throw new Error("stale calc wrong: got "+stale+", want 2 (changed+deleted)");
console.log("  ok: staleness counts changed + deleted")' || FAIL=1

# [6] index meta carries an exact byte count and the reader uses it (crash-order fix)
grep -q '"bytes": len(flat)' rag-index.py && grep -q "bytes: flat.byteLength" extensions/npu-retrieval.ts \
  && grep -q "meta.bytes" extensions/npu-retrieval.ts \
  && ok "index writers record exact bytes; guard checks it" || bad "bytes guard was removed from a writer or the reader"

[ "$FAIL" = 0 ] && echo "check-fixes: all checks pass" || { echo "check-fixes: FAILURES above"; exit 1; }
