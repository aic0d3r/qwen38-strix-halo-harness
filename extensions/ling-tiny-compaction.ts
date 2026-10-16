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
import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
import { convertToLlm, serializeConversation } from "@earendil-works/pi-coding-agent";
import { isMain } from "./harness-profile/index.ts";

const TINY_PROVIDER = "llamacpp-tiny";
const TINY_MODEL = "ling3.0-tiny";
// one JSON line per sidecar/NPU call; summarized by scripts/session-audit.sh --harness
const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };

export default function (pi: ExtensionAPI) {
	pi.on("session_before_compact", async (event, ctx) => {
		// local halogen only: the sidecar offload is pointless for cloud models
		if (!isMain(ctx)) return;
		const { preparation, signal } = event;
		const { messagesToSummarize, turnPrefixMessages, tokensBefore, firstKeptEntryId, previousSummary } = preparation;

		const model = ctx.modelRegistry.find(TINY_PROVIDER, TINY_MODEL);
		if (!model) {
			ev({ c: "compaction", ok: false, why: "no-provider" });
			ctx.ui.notify(`[${TINY_MODEL}] provider not found, using default compaction`, "warning");
			return;
		}

		const allMessages = [...messagesToSummarize, ...turnPrefixMessages];
		ctx.ui.notify(
			`Compacting with ${TINY_MODEL}: ${allMessages.length} messages (~${tokensBefore.toLocaleString()} tokens)...`,
			"info",
		);

		// the sidecar window is 131k tokens (~3.9 chars/tok); an oversized prompt
		// 400s llama.cpp and silently falls back to slow main-model compaction,
		// which is exactly what big sessions must not pay - cap head+tail instead
		const CAP = 420_000;
		let conversationText = serializeConversation(convertToLlm(allMessages));
		if (conversationText.length > CAP) {
			conversationText = conversationText.slice(0, 40_000)
				+ "\n[...middle truncated to fit the sidecar window...]\n"
				+ conversationText.slice(-(CAP - 40_000));
		}
		const previousContext = previousSummary ? `\n\nPrevious session summary for context:\n${previousSummary}` : "";

		const summaryMessages = [
			{
				role: "user" as const,
				content: [
					{
						type: "text" as const,
						text: `You are the context-compaction service for a coding agent. Summarize this session into a handoff document for the next turn. Capture:

1. Project goal and current state (what exists, what works)
2. Every file created/modified with its purpose and key exports/functions
3. Bugs encountered and their resolutions
4. Verification status: which checks passed, which failed
5. Exact next steps for continuation
6. Critical constraints and API details future turns must know

Be precise with names and paths; do not invent anything not present in the session.${previousContext}

<conversation>
${conversationText}
</conversation>`,
					},
				],
				timestamp: Date.now(),
			},
		];

		const t0 = Date.now();
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
				ev({ c: "compaction", ok: false, ms: Date.now() - t0, why: "empty" });
				if (!signal.aborted) ctx.ui.notify("Compaction summary was empty, using default compaction", "warning");
				return;
			}

			ev({ c: "compaction", ok: true, ms: Date.now() - t0, tokIn: response.usage?.input, tokOut: response.usage?.output });
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
			ev({ c: "compaction", ok: false, ms: Date.now() - t0, why: message.slice(0, 120) });
			ctx.ui.notify(`Tiny compaction failed: ${message}, using default`, "error");
			return;
		}
	});
}
