/**
 * Ling-3.0-tiny compaction extension
 *
 * Routes /compact (manual and auto) summarization to Ling-3.0-tiny on the local
 * llama-server at :8090 instead of the session's main model. On this box the
 * 27B main model would prefill the session at ~250 t/s while tiny runs ~2100 t/s,
 * so compaction drops from minutes to seconds. Falls back to default compaction
 * if tiny's provider is missing or the call fails.
 *
 * Install: copy to ~/.pi/agent/extensions/ling-tiny-compaction.ts
 * (or load once with pi --extension)
 */

import { uuidv7 } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { convertToLlm, serializeConversation } from "@earendil-works/pi-coding-agent";

const TINY_PROVIDER = "llamacpp-tiny";
const TINY_MODEL = "ling3.0-tiny";

export default function (pi: ExtensionAPI) {
	pi.on("session_before_compact", async (event, ctx) => {
		const { preparation, signal } = event;
		const { messagesToSummarize, turnPrefixMessages, tokensBefore, firstKeptEntryId, previousSummary } = preparation;

		const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
		if (!model) {
			ctx.ui.notify(`[${TINY_MODEL}] provider not found, using default compaction`, "warning");
			return;
		}

		const allMessages = [...messagesToSummarize, ...turnPrefixMessages];
		ctx.ui.notify(
			`Compacting with ${TINY_MODEL}: ${allMessages.length} messages (~${tokensBefore.toLocaleString()} tokens)...`,
			"info",
		);

		const conversationText = serializeConversation(convertToLlm(allMessages));
		const previousContext = previousSummary ? `\n\nPrevious session summary for context:\n${previousSummary}` : "";

		const summaryMessages = [
			{
				role: "user" as const,
				content: [
					{
						type: "text" as const,
						text: `You are the context-compaction service for a coding agent. Summarize this session into a handoff document for the next turn. Capture:${previousContext}

1. Project goal and current state (what exists, what works)
2. Every file created/modified with its purpose and key exports/functions
3. Bugs encountered and their resolutions
4. Verification status: which checks passed, which failed
5. Exact next steps for continuation
6. Critical constraints and API details future turns must know

Be precise with names and paths; do not invent anything not present in the session.

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
					maxTokens: 4096,
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
				if (!signal.aborted) ctx.ui.notify("Compaction summary was empty, using default compaction", "warning");
				return;
			}

			return {
				compaction: {
					summary,
					firstKeptEntryId,
					tokensBefore,
					usage: response.usage,
				},
			};
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			ctx.ui.notify(`Tiny compaction failed: ${message}, using default`, "error");
			return;
		}
	});
}
