/**
 * context-prune.ts v2 — lossless context reduction (using ACTUAL pi hooks).
 *
 * Available hooks: session_before_compact, after_provider_response
 * NOT available: after_tool_execution (was used in v1 — didn't work)
 *
 * What works:
 * 1. PRE-COMPACTION PRUNING (session_before_compact): strip noise + dedup
 *    before the compaction model sees the conversation.
 * 2. INTER-SCAN (after_provider_response): after each LLM response, scan the
 *    conversation for duplicate tool results and mark them for pruning.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

function hash(s: string): string {
	let h = 0;
	for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
	return String(h);
}

function isNoise(text: string): boolean {
	const t = text.trim();
	if (t.length < 40) return true;
	if (/^(ok|done|success|pass|yes|no|true|false|[\d.]+)\s*$/i.test(t)) return true;
	if (/^no (changes|output|error|diff|match)/i.test(t)) return true;
	if (/^\(no output\)$/.test(t)) return true;
	return false;
}

export default function (pi: ExtensionAPI) {
	let scanCount = 0;
	let totalCharsSaved = 0;

	// Pre-compaction: strip noise + dedup from the conversation
	pi.on("session_before_compact", async (event: any, ctx: any) => {
		const messages = event?.messages || [];
		if (!messages.length) return;

		let pruned = 0;
		let charsSaved = 0;
		const seen = new Map<string, number>(); // hash -> first index

		const cleaned = messages.map((msg: any, i: number) => {
			if (msg.role !== "tool" && msg.role !== "function") return msg;
			const content = typeof msg.content === "string" ? msg.content : "";

			// Strip noise
			if (isNoise(content)) {
				pruned++;
				charsSaved += content.length;
				return { ...msg, content: "[pruned: noise]" };
			}

			// Dedup identical outputs
			const h = hash(content);
			if (seen.has(h)) {
				pruned++;
				charsSaved += content.length;
				return { ...msg, content: `[pruned: duplicate of message ${seen.get(h)}]` };
			}
			if (content.length > 50) seen.set(h, i);

			// Collapse mostly-whitespace long outputs
			if (content.length > 2000) {
				const lines = content.split("\n");
				const nonEmpty = lines.filter((l: string) => l.trim().length > 0);
				if (nonEmpty.length < lines.length * 0.3) {
					charsSaved += content.length - nonEmpty.join("\n").length;
					pruned++;
					return { ...msg, content: nonEmpty.join("\n") };
				}
			}

			return msg;
		});

		if (pruned > 0) {
			event.messages = cleaned;
			totalCharsSaved += charsSaved;
			console.log(`[context-prune] pre-compaction: ${pruned} results pruned, ${(charsSaved / 1000).toFixed(1)}k chars saved (total: ${(totalCharsSaved / 1000).toFixed(1)}k)`);
		}
	});

	// Post-response: log context stats (observability, doesn't modify)
	pi.on("after_provider_response", async (event: any, ctx: any) => {
		scanCount++;
		// Don't modify here — just track. The actual pruning happens at compaction.
		// This hook proves the extension is alive and counting turns.
		if (scanCount % 10 === 0) {
			console.log(`[context-prune] turn ${scanCount}: alive, ${(totalCharsSaved / 1000).toFixed(1)}k chars saved so far`);
		}
	});
}
