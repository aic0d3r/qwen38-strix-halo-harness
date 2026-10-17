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

Don't read a whole source file to look at one symbol: a function body via `find_symbol` costs
~0.8k chars against ~14k for the file, and an outline via `get_symbols_overview` ~0.4k. Read a
file whole only when you need most of it; otherwise use `offset`/`limit`. Serena needs a
language server for the file's language (`.serena/project.yml` `language_servers`); if a tool
reports none, fall back to semble + `read` with `offset`/`limit`.

## Web search — jina

If an answer depends on knowledge likely newer than your training (recent packages/tools,
releases, maintenance status, current docs, "best X" questions) and Jina is enabled, search with
it first (`tools.mcp__jina__*` via codemode), then answer. Check `~/.pi/agent/mcp.json`
(`enabled: false` = off) or just try the call before telling the user a tool is unavailable.
Jina ships disabled in the example config: enable it with `/mcp`.
