// tool-trim: keeps big tool output out of context without losing information.
//  - bash: strips ANSI/progress frames, collapses repeated lines, and for very long output keeps
//    the first 60 + last 140 lines; the full original is saved to a file named in the marker.
//  - read: an identical re-read of an unchanged file returns a short pointer instead of the content
//    (reset after compaction/tree moves, when the earlier copy may no longer be in context).
// Off switch: TOOL_TRIM_DISABLED=1. Savings are logged to harness-events.jsonl (c: "tool-trim").
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
import { BOUNDED, trim } from "./core.ts";

const NUDGE_CHARS = 6000;
const SOURCE = /\.(py|tsx?|jsx?|mjs|go|rs|java|kt|c|cc|cpp|h|hpp|cs|rb|php|swift)$/i;
const OUT_DIR = path.join(os.tmpdir(), "pi-out");
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };

export default function toolTrim(pi: ExtensionAPI) {
	let seq = 0;
	let reads = new Map<string, string>(); // path|offset|limit -> content hash
	let nudged = new Set<string>(); // source files already given the narrow-read hint this session
	for (const e of ["session_start", "session_compact", "session_tree", "session_switch", "session_fork"]) pi.on(e as any, async () => { reads = new Map(); nudged = new Set(); });

	pi.on("tool_result", async (event: any) => {
		if (process.env.TOOL_TRIM_DISABLED || event.isError && event.toolName === "read") return;
		const blocks: any[] = event.content ?? [];
		if (!blocks.length || blocks.some((b) => b.type !== "text")) return; // images etc: untouched
		const before = blocks.map((b) => b.text).join("\n");

		if (event.toolName === "bash") {
			if (BOUNDED.test(String(event.input?.command ?? ""))) return;
			const file = path.join(OUT_DIR, `${Date.now()}-${++seq}.txt`);
			const r = trim(before, file);
			if (r.cut) { try { fs.mkdirSync(OUT_DIR, { recursive: true }); fs.writeFileSync(file, before); } catch { return; } } // can't save the original: leave output alone
			if (r.text === before) return;
			ev({ c: "tool-trim", k: "bash", saved: before.length - r.text.length, cut: r.cut });
			return { content: [{ type: "text" as const, text: r.text }] };
		}

		if (event.toolName === "read" && before.length > 2000) {
			const key = `${event.input?.path}|${event.input?.offset ?? ""}|${event.input?.limit ?? ""}`;
			const h = crypto.createHash("sha1").update(before).digest("hex");
			const seen = reads.get(key) === h;
			reads.set(key, h);
			if (!seen) {
				// logged sessions: median read 2.6k chars, a serena symbol lookup ~0.2k; nudge once per big whole-file source read
				const p = String(event.input?.path ?? "");
				if (before.length > NUDGE_CHARS && event.input?.offset == null && event.input?.limit == null && SOURCE.test(p) && !nudged.has(p)) {
					nudged.add(p);
					ev({ c: "tool-trim", k: "nudge", chars: before.length });
					return { content: [{ type: "text" as const, text: `${before}\n\n[tool-trim: whole-file read of ${before.length} chars. For one function or class next time use mcp__serena__find_symbol / get_symbols_overview, or read with offset/limit.]` }] };
				}
				return;
			}
			const text = `[tool-trim: ${event.input?.path} is unchanged since your earlier read (${before.split("\n").length} lines); that content is still in your context above. Re-read with a different offset/limit if you need other lines.]`;
			ev({ c: "tool-trim", k: "reread", saved: before.length - text.length });
			return { content: [{ type: "text" as const, text }] };
		}
	});
}
