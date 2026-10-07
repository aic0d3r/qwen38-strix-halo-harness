/**
 * Ling-3.0-tiny tool-result triage extension
 *
 * Compresses oversized bash tool results with Ling-3.0-tiny BEFORE they enter
 * session history, so the main model's context stays small (smaller resends,
 * later/no compaction). Conservative by design:
 *   - only bash results; only successes; only above TRIGGER_CHARS
 *   - keeps the first KEEP_HEAD and last KEEP_TAIL lines verbatim
 *   - tiny is instructed to preserve errors, warnings, diffs, numbers, paths
 *   - any failure -> original result passes through unchanged
 */

import { uuidv7 } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TINY_PROVIDER = "llamacpp-tiny";
const TINY_MODEL = "ling3.0-tiny";
const TRIGGER_CHARS = 6000;   // only compress results larger than this
const KEEP_HEAD = 10;         // verbatim head lines
const KEEP_TAIL = 20;         // verbatim tail lines

export default function (pi: ExtensionAPI) {
	pi.on("tool_result", async (event, ctx) => {
		if (event.toolName !== "bash") return;
		if (event.isError) return;

		// event.content may be a string or an array of content blocks
		let content: string | null = null;
		if (typeof event.content === "string") content = event.content;
		else if (Array.isArray(event.content)) {
			const text = event.content
				.map((c: any) => (c && typeof c === "object" && typeof c.text === "string" ? c.text : ""))
				.join("\n");
			if (text.trim()) content = text;
		}
		if (!content || content.length < TRIGGER_CHARS) return;

		const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
		if (!model) return; // silent: no provider, no triage

		const lines = content.split("\n");
		const head = lines.slice(0, KEEP_HEAD).join("\n");
		const tail = lines.slice(-KEEP_TAIL).join("\n");
		const middle = lines.slice(KEEP_HEAD, -KEEP_TAIL).join("\n");

		ctx.ui.notify(
			`Triage: compressing bash result (${content.length.toLocaleString()} chars) with ${TINY_MODEL}...`,
			"info",
		);

		const summaryMessages = [
			{
				role: "user" as const,
				content: [
					{
						type: "text" as const,
						text: `You compress terminal output for a coding agent. The output below will replace the original in the agent's context. Extract EVERYTHING load-bearing, drop only repetition and boilerplate. Preserve verbatim: error messages, warnings, stack traces, test counts, exit-relevant lines, file paths, and any diff lines. Keep the original ordering. Output only the compressed output, no commentary.

<terminal-output>
${middle}
</terminal-output>`,
					},
				],
				timestamp: Date.now(),
			},
		];

		try {
			const response = await ctx.modelRegistry.complete(
				model,
				{ messages: summaryMessages },
				{
					maxTokens: 2048,
					signal: ctx.signal,
					cacheRetention: "none",
					sessionId: uuidv7(),
				},
			);

			const compressed = response.content
				.filter((c): c is { type: "text"; text: string } => c.type === "text")
				.map((c) => c.text)
				.join("\n")
				.trim();

			if (!compressed) return; // empty -> keep original

			const newContent = [
				head,
				"[triage: middle compressed by ling3.0-tiny; original length " + content.length + " chars]",
				compressed,
				tail,
			].join("\n");

			return {
				content: [{ type: "text", text: newContent }],
				usage: response.usage,
			};
		} catch {
			return; // any failure -> original passes through
		}
	});
}
