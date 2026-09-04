/**
 * Ling-3.0-tiny branch-summary extension
 *
 * Hooks session_before_tree: when navigating branches with /tree, summarize
 * the entries being abandoned (entriesToSummarize) with Ling-3.0-tiny instead
 * of the main model. Same economics as compaction: prefill-shaped job.
 * Falls back to pi's default branch summarization on any failure.
 */

import { uuidv7 } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { convertToLlm, serializeConversation } from "@earendil-works/pi-coding-agent";

const TINY_PROVIDER = "llamacpp-tiny";
const TINY_MODEL = "ling3.0-tiny";

export default function (pi: ExtensionAPI) {
	pi.on("session_before_tree", async (event, ctx) => {
		const { preparation, signal } = event;
		const { entriesToSummarize, oldLeafId, targetId } = preparation;

		if (!entriesToSummarize || entriesToSummarize.length === 0) return;

		const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
		if (!model) {
			ctx.ui.notify(`[${TINY_MODEL}] provider not found, using default summary`, "warning");
			return;
		}

		ctx.ui.notify(
			`Branch summary with ${TINY_MODEL}: ${entriesToSummarize.length} entries...`,
			"info",
		);

		const fullById = new Map((ctx.sessionManager.getEntries() as any[]).map((x: any) => [x.id, x]));
		const serializeEntry = (stub: any): string => {
			const e = (stub?.id && fullById.get(stub.id)) || stub;  // hydrate: stubs come content-stripped
			const out: string[] = [];
			const visit = (node: any, depth = 0) => {
				if (!node || depth > 3) return;
				if (typeof node === "string") { if (node.trim()) out.push(node); return; }
				if (!Array.isArray(node) && typeof node !== "object") return;
				const role = node.role;
				if (Array.isArray(node.content)) {
					for (const c of node.content) {
						if (!c || typeof c !== "object") continue;
						if (c.type === "text" && c.text) out.push(`${String(role ?? "MSG").toUpperCase()}: ${c.text}`);
						else if (c.type === "toolCall") out.push(`TOOL CALL ${c.name}: ${JSON.stringify(c.arguments ?? c.input ?? {}).slice(0, 400)}`);
						else if (c.type === "toolResult" || c.type === "tool_result") out.push(`TOOL RESULT: ${String(c.result ?? c.text ?? c.content ?? "").slice(0, 600)}`);
					}
				}
				if (node.message) visit(node.message, depth + 1);
			};
			visit(e?.message ?? e);
			return out.join("\n");
		};
		const conversationText = entriesToSummarize
			.map(serializeEntry)
			.filter((s: string) => s.trim().length > 0)
			.join("\n\n");
		const summaryMessages = [
			{
				role: "user" as const,
				content: [
					{
						type: "text" as const,
						text: `You are the branch-summarization service for a coding agent. The user is navigating back to an earlier point in the session (${targetId}), abandoning the branch that ended at ${oldLeafId}. Summarize what was attempted in the abandoned branch so future turns keep the context without re-reading it. Capture:

1. What the branch was trying to do
2. What was actually changed (files, commands, results)
3. Why it is being abandoned or left (failed, superseded, unfinished)
4. Anything a future session should NOT redo

Be precise with names and paths; do not invent anything not present.

<conversation>
${conversationText}
</conversation>`,
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
					signal,
					cacheRetention: "none",
					sessionId: uuidv7(),
				},
			);

			const summary = response.content
				.filter((c): c is { type: "text"; text: string } => c.type === "text")
				.map((c) => c.text)
				.join("\n");

			if (!summary.trim()) {
				if (!signal.aborted) ctx.ui.notify("Branch summary was empty, using default", "warning");
				return;
			}

			return {
				summary: {
					summary,
					usage: response.usage,
				},
			};
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			ctx.ui.notify(`Tiny branch summary failed: ${message}, using default`, "error");
			return;
		}
	});
}
