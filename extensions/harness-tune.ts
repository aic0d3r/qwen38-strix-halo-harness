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
 *   temperature   0 = deterministic benches, 0.8 default sampling (models.json samplingParams)
 *   triageChars   bash-result size that triggers ling-tiny compression (ling-tiny-triage.ts)
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HOME = os.homedir();
const SETTINGS = path.join(HOME, ".pi/agent/settings.json");
const MODELS = path.join(HOME, ".pi/agent/models.json");
const TRIAGE = path.join(HOME, ".pi/agent/extensions/ling-tiny-triage.ts");
const MAIN_MODEL = "qwen3.8-27b";
const CONTEXT_WINDOW = 262144;

const DEFAULTS: Record<string, number> = { compactAt: 95, maxTokens: 32768, temperature: 0, triageChars: 6000, ubatch: 4096 };

const readJson = (p: string): any => (fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, "utf8")) : {});
const writeJson = (p: string, v: any) => fs.writeFileSync(p, JSON.stringify(v, null, 2) + "\n");

function current(): Record<string, number> {
	const s = readJson(SETTINGS);
	const m = readJson(MODELS);
	const entry = m?.providers?.llamacpp?.models?.find((x: any) => x?.id === MAIN_MODEL) || {};
	let triageChars = DEFAULTS.triageChars;
	if (fs.existsSync(TRIAGE)) {
		const mt = /TRIGGER_CHARS\s*=\s*(\d+)/.exec(fs.readFileSync(TRIAGE, "utf8"));
		if (mt) triageChars = Number(mt[1]);
	}
	let ubatch = DEFAULTS.ubatch;
	const ubFile = path.join(HOME, ".pi/agent/harness-ubatch");
	if (fs.existsSync(ubFile)) {
		const v = Number(fs.readFileSync(ubFile, "utf8").trim());
		if (Number.isFinite(v)) ubatch = v;
	}
	return {
		compactAt: Math.round((CONTEXT_WINDOW - (s?.compaction?.reserveTokens ?? 16384)) / 1000),
		maxTokens: entry?.maxTokens ?? DEFAULTS.maxTokens,
		temperature: entry?.samplingParams?.temperature ?? 0.8,
		triageChars,
		ubatch,
	};
}

export default function (pi: ExtensionAPI) {
	pi.registerCommand("tune", {
		description: "Show or set harness tuning knobs (compactAt, maxTokens, temperature, triageChars)",
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
			const bounds: Record<string, [number, number]> = { compactAt: [10, 245], maxTokens: [1024, 65536], temperature: [0, 2], triageChars: [500, 100000], ubatch: [128, 8192] };
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
			s.compaction = Object.assign({}, s.compaction, { enabled: true, reserveTokens: CONTEXT_WINDOW - Math.round(num * 1000) });
			writeJson(SETTINGS, s);
		} else if (key === "maxTokens" || key === "temperature") {
			const m = readJson(MODELS);
			const entry = m?.providers?.llamacpp?.models?.find((x: any) => x?.id === MAIN_MODEL);
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
		} else if (key === "triageChars" && fs.existsSync(TRIAGE)) {
			const src = fs.readFileSync(TRIAGE, "utf8");
			fs.writeFileSync(TRIAGE, src.replace(/TRIGGER_CHARS\s*=\s*\d+/, `TRIGGER_CHARS = ${Math.round(num)}`));
		}
	}
}
