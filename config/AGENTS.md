# Agent notes (installed by the harness setup.sh; edit freely)

## Code search — semble

Semble (MCP `search` / `find_related`, or CLI `semble search "<query>" <path>`) is the default for
"where/how is X done" questions and unfamiliar code: describe behavior or name an identifier, then
jump to the returned file:line instead of grep+read. Both tools require `repo`: always pass the
absolute project root (the cwd), never a file path. Use `--content docs|config|all` for non-code.

## Serena — symbol-level work

Call `mcp__serena__activate_project` once per project, then prefer Serena for symbols:
`get_symbols_overview` (file structure), `find_symbol` (definitions), `find_referencing_symbols`
(callers/impact), `replace_symbol_body` / `insert_after_symbol` (edits), `rename_symbol`
(project-wide rename), `get_diagnostics_for_file` (type/syntax errors). Use grep only for
exact-string sweeps, and native read/edit for docs and config files.
