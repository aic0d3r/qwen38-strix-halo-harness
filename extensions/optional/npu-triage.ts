/**
 * npu-triage.ts v2 — passive tool-result summarization on the NPU.
 *
 * Available hooks: session_before_compact, after_provider_response
 * NOT available: after_tool_execution (was used in v1 — didn't work)
 *
 * v2 approach: since we can't intercept individual tool results in-flight,
 * we do two things:
 * 1. At compaction time (session_before_compact): find oversized tool results
 *    in the conversation and replace them with NPU-generated summaries.
 * 2. Track context stats for observability.
 *
 * This means the summarization happens at compaction rather than immediately,
 * but the context saving is the same — the summarized version is what gets
 * prefilled on all subsequent turns after compaction.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BASE = process.env.PI_NPU_BASE || "http://127.0.0.1:8731";
const THRESHOLD = parseInt(process.env.PI_NPU_TRIAGE_CHARS || "6000", 10);

async function npuSummarize(text: string, context: string): Promise<string | null> {
	try {
		const prompt = `Summarize this ${context} concisely. Keep: errors, findings, file names, line numbers, test results. Drop: repeated lines, blank lines, verbose progress.\n\n${text.slice(0, 12000)}`;
		const res = await fetch(BASE + "/v1/chat/completions", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ model: "qwen3.5-2b", messages: [{ role: "user", content: prompt }], max_tokens: 400 }),
			signal: AbortSignal.timeout(30000),
		});
		if (!res.ok) return null;
		const r = await res.json();
		const s = r.choices?.[0]?.message?.content;
		return (s && s.trim().length > 20) ? s.trim() : null;
	} catch { return null; }
}

export default function (pi: ExtensionAPI) {
	let triagedCount = 0;
	let charsSaved = 0;

	pi.on("session_before_compact", async (event: any, ctx: any) => {
		const messages = event?.messages || [];
		if (!messages.length) return;

		let summarized = 0;
		const cleaned = [...messages];

		for (let i = 0; i < cleaned.length; i++) {
			const msg = cleaned[i];
			if (msg.role !== "tool" && msg.role !== "function") continue;
			const content = typeof msg.content === "string" ? msg.content : "";
			if (content.length < THRESHOLD) continue;
			if (content.startsWith("[npu-triage:")) continue; // already summarized

			const summary = await npuSummarize(content, "tool output");
			if (!summary) continue;

			const saved = content.length - summary.length;
			if (saved < 100) continue;

			summarized++;
			charsSaved += saved;
			cleaned[i] = { ...msg, content: `[npu-triage: ${content.length} -> ${summary.length} chars]\n\n${summary}` };
		}

		if (summarized > 0) {
			event.messages = cleaned;
			console.log(`[npu-triage] compaction: summarized ${summarized} oversized results, ${(charsSaved / 1000).toFixed(1)}k chars saved total`);
		}
	});

	pi.registerCommand("triage-stats", {
		description: "show npu-triage stats",
		handler: async () => `triaged: ${triagedCount} | chars saved: ${(charsSaved / 1000).toFixed(1)}k | threshold: ${THRESHOLD}`,
	});
}
