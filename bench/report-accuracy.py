#!/usr/bin/env python3
"""Grade a survey report's file:line claims against the actual repo.

Extracts `path:line` (and path:line-line) claims, verifies:
  A) the file exists
  B) the referenced line range mentions the symbol claimed nearby (if any)
Output: per-report accuracy % + list of misses. The objective quality metric
for harness A/B reports - replaces "the report looked thorough".
"""
import re, sys, os, json

report_path = sys.argv[1]
repo = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
text = open(report_path, errors="replace").read()

# claims: `path/file.ext:123` or path/file.ext:123-45 (optionally with a backticked symbol nearby)
claims = re.findall(r'([\w./-]+\.(?:cpp|h|c|hpp|cu|py|ts|js|sh)):(\d+)(?:-(\d+))?', text)
symbols = {}
# associate backticked identifiers with the nearest claim on the same line of the report
for line in text.splitlines():
    cs = re.findall(r'([\w./-]+\.(?:cpp|h|c|hpp|cu|py|ts|js|sh)):(\d+)', line)
    syms = re.findall(r'`([A-Za-z_][A-Za-z0-9_:~]*)`', line)
    if cs and syms:
        for c in cs:
            symbols.setdefault(c, []).extend(syms[:3])

total = len(claims)
ok_file = ok_line = 0
misses = []
seen = set()
for f, ln, hi in claims:
    key = (f, ln)
    if key in seen: continue
    seen.add(key)
    fp = os.path.join(repo, f)
    if not os.path.exists(fp):
        misses.append(f"MISSING FILE {f}:{ln}"); continue
    ok_file += 1
    syms = symbols.get((f, ln), [])
    if not syms:
        ok_line += 1  # no symbol claim to falsify
        continue
    try:
        lines = open(fp, errors="replace").read().splitlines()
    except OSError:
        continue
    lo_n, hi_n = int(ln), int(hi or ln) + 3
    window = "\n".join(lines[max(0, lo_n - 4):hi_n])
    if any(s in window for s in syms):
        ok_line += 1
    else:
        misses.append(f"DRIFT {f}:{ln} wanted {syms} got: {window.strip()[:90]!r}")

graded = len(seen)
print(f"report: {report_path}")
print(f"unique claims: {graded} | file-exists: {ok_file}/{graded} ({100*ok_file//max(graded,1)}%) | symbol-at-line: {ok_line}/{graded} ({100*ok_line//max(graded,1)}%)")
for m in misses[:12]: print(" ", m)
json.dump({"report": report_path, "claims": graded, "file_ok": ok_file, "line_ok": ok_line}, open("/tmp/last-grade.json", "w"))
