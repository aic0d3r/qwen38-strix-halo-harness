/**
 * /tune: adjust this harness's performance knobs from inside pi.
 *
 *   /tune                      show current values (read from the real files)
 *   /tune <key> <value>        set one key (validated, written immediately)
 *   /tune reset                restore defaults documented in the harness post
 *
 * Keys (friendly names map to config locations):
 *   compactAt     k tokens; auto-compaction fires past this (settings.json compaction.reserveTokens)
 *   maxTokens     per-turn output budget (models.json model entry)
 *   temperature   1.0 = Qwen3.8 card sampling; set 0 for deterministic benches (models.json samplingParams)
 *   ubatch        llama.cpp-side batch size (harness-ubatch file; ignored by the halogen server)
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HOME = os.homedir();
const SETTINGS = path.join(HOME, ".pi/agent/settings.json");
const MODELS = path.join(HOME, ".pi/agent/models.json");
const MAIN_PROVIDER = "halogen";
const MAIN_MODEL = "halogen-qwen3.8-flash-next";

// canonical post-setup values = what setup.sh provisions (keep in sync):
// maxTokens 8192 because halogen hard-rejects bigger requests past ~32.7k ctx;
// compactAt 202 = reserveTokens 60000 on the 262k window (compaction summarize + answer room)
const DEFAULTS: Record<string, number> = { compactAt: 202, maxTokens: 8192, temperature: 1.0, ubatch: 4096 };

const readJson = (p: string): any => (fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, "utf8")) : {});
const writeJson = (p: string, v: any) => fs.writeFileSync(p, JSON.stringify(v, null, 2) + "\n");
// the launcher syncs contextWindow on every start (262k normally, 65k on the
// allocator fallback); computing compactAt from the hardcoded 262k on a 65k
// day produced a reserve larger than the context - compaction never fired
const ctxWindow = (): number =>
	readJson(MODELS)?.providers?.[MAIN_PROVIDER]?.models?.find((x: any) => x?.id === MAIN_MODEL)?.contextWindow || 262144;

function current(): Record<string, number> {
	const s = readJson(SETTINGS);
	const m = readJson(MODELS);
	const entry = m?.providers?.[MAIN_PROVIDER]?.models?.find((x: any) => x?.id === MAIN_MODEL) || {};
	let ubatch = DEFAULTS.ubatch;
	const ubFile = path.join(HOME, ".pi/agent/harness-ubatch");
	if (fs.existsSync(ubFile)) {
		const v = Number(fs.readFileSync(ubFile, "utf8").trim());
		if (Number.isFinite(v)) ubatch = v;
	}
	return {
		compactAt: Math.round((ctxWindow() - (s?.compaction?.reserveTokens ?? 16384)) / 1000),
		maxTokens: entry?.maxTokens ?? DEFAULTS.maxTokens,
		temperature: entry?.samplingParams?.temperature ?? DEFAULTS.temperature,
		ubatch,
	};
}

export default function (pi: ExtensionAPI) {
	pi.registerCommand("tune", {
		description: "Show or set harness tuning knobs (compactAt, maxTokens, temperature, ubatch)",
		handler: async (args, ctx) => {
			const argv = String(args ?? "").trim().split(/\s+/).filter(Boolean);

			if (argv.length === 0) {
				const c = current();
				ctx.ui.notify(
					`harness tuning (defaults: ${Object.entries(DEFAULTS).map(([k, v]) => `${k}=${v}`).join(", ")}):\n` +
						Object.entries(c).map(([k, v]) => `  ${k.padEnd(12)} ${v}`).join("\n") +
						`\nusage: /tune <key> <value>   (compactAt in k tokens; temperature 0 for reproducible benches; ubatch needs a server restart and 2048 is the deep-fill setting)`,
					"info",
				);
				return;
			}

			if (argv[0] === "reset") {
				for (const [k, v] of Object.entries(DEFAULTS)) await apply(k, v);
				ctx.ui.notify(`reset to defaults: ${JSON.stringify(DEFAULTS)}`, "info");
				return;
			}

			const [key, val] = argv;
			const num = Number(val);
			if (!(key in DEFAULTS)) {
				ctx.ui.notify(`unknown key '${key}'. keys: ${Object.keys(DEFAULTS).join(", ")}`, "error");
				return;
			}
			if (!Number.isFinite(num) || num < 0) {
				ctx.ui.notify(`value must be a non-negative number, got '${val}'`, "error");
				return;
			}
			const bounds: Record<string, [number, number]> = { compactAt: [10, Math.round((ctxWindow() - 10240) / 1000)], maxTokens: [1024, 8192], temperature: [0, 2], ubatch: [128, 8192] };
			const [lo, hi] = bounds[key];
			if (num < lo || num > hi) {
				ctx.ui.notify(`${key} must be between ${lo} and ${hi}`, "error");
				return;
			}
			await apply(key, num);
			ctx.ui.notify(`${key} = ${num} (restart pi if a session is mid-flight; server-side values apply next run)`, "info");
		},
	});

	async function apply(key: string, num: number) {
		if (key === "compactAt") {
			const s = readJson(SETTINGS);
			s.compaction = Object.assign({}, s.compaction, { enabled: true, reserveTokens: ctxWindow() - Math.round(num * 1000) });
			writeJson(SETTINGS, s);
		} else if (key === "maxTokens" || key === "temperature") {
			const m = readJson(MODELS);
			const entry = m?.providers?.[MAIN_PROVIDER]?.models?.find((x: any) => x?.id === MAIN_MODEL);
			if (!entry) throw new Error(`${MAIN_MODEL} not found in models.json`);
			if (key === "maxTokens") entry.maxTokens = Math.round(num);
			else {
				entry.samplingParams = Object.assign({}, entry.samplingParams, { temperature: num });
				if (num === 0) {
					entry.samplingParams.top_p = Math.max(entry.samplingParams.top_p ?? 0.95, 0.95);
					entry.samplingParams.min_p = entry.samplingParams.min_p ?? 0.05;
				}
			}
			writeJson(MODELS, m);
		} else if (key === "ubatch") {
			fs.writeFileSync(path.join(HOME, ".pi/agent/harness-ubatch"), String(Math.round(num)));
		}
	}
}
