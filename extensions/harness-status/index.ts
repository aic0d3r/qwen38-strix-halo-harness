// harness-status: /usage runs scripts/usage-stats.py inside pi; a footer status shows halogen load.
//   /usage [--all] [--top N]     (HARNESS_REPO overrides the repo path)
// Footer: "halogen kv 14% · 1 run · 0 wait · 62 t/s" polled from :8731/metrics every 5s; hidden when unreachable.
import * as fs from "node:fs";
import * as path from "node:path";
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
import { profile, sidecarOn } from "../harness-profile/index.ts";

const BASE = process.env.HALOGEN_URL || "http://127.0.0.1:8731";
// setup.sh writes the repo path to <agent dir>/harness-repo
const recorded = (() => { try { return fs.readFileSync(path.join(getAgentDir(), "harness-repo"), "utf8").trim(); } catch { return ""; } })();
const REPO = process.env.HARNESS_REPO || recorded || path.join(process.env.HOME ?? "", "coding/qwen38-strix-halo-harness");
const metric = (txt: string, k: string) => { const m = txt.match(new RegExp(`^${k.replace(":", ":")}\\s+([0-9.eE+-]+)`, "m")); return m ? Number(m[1]) : undefined; };

export default function harnessStatus(pi: ExtensionAPI) {
	let timer: ReturnType<typeof setInterval> | undefined;
	let prev: { tok: number; sec: number } | undefined;
	let rate: number | undefined;

	pi.registerCommand("usage", {
		description: "MCP / skill / extension usage report (scripts/usage-stats.py); pass --all for lifetime",
		handler: async (args: string, ctx: any) => {
			try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), c: "harness-status", k: "usage" }) + "\n"); } catch {}
			const r = await pi.exec("python3", [path.join(REPO, "scripts/usage-stats.py"), ...(args ?? "").split(/\s+/).filter(Boolean)], { cwd: REPO, timeout: 30_000 });
			ctx.ui.notify((r.stdout || r.stderr || "no output").trim(), r.code === 0 ? "info" : "error");
		},
	});

	const poll = async (ctx: any) => {
		try {
			const txt = await (await fetch(`${BASE}/metrics`, { signal: AbortSignal.timeout(2000) })).text();
			const kv = metric(txt, "llamacpp:kv_cache_usage_ratio"), run = metric(txt, "llamacpp:requests_processing"), wait = metric(txt, "halogen:kv_pool_waiting_for_room");
			const ptok = metric(txt, "llamacpp:prompt_tokens_total"), psec = metric(txt, "llamacpp:prompt_seconds_total");
			const tok = metric(txt, "llamacpp:tokens_predicted_total"), sec = metric(txt, "llamacpp:tokens_predicted_seconds_total");
			if (tok !== undefined && sec !== undefined) {
				if (prev && sec > prev.sec) rate = (tok - prev.tok) / (sec - prev.sec); // last decode rate, kept while idle
				prev = { tok, sec };
			}
			// prefill is bursty, so a poll delta is useless: show the since-start average, the number ubatch/batch tuning moves
			const pf = ptok && psec ? ptok / psec : undefined;
			const k = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n)));
			const parts = [kv !== undefined ? `kv ${Math.round(kv * 100)}%` : "", run ? `${run} run` : "", wait ? `${wait} wait` : "", pf ? `pf ${k(pf)}/s` : "", rate ? `${k(rate)} t/s` : ""].filter(Boolean);
			ctx.ui.setStatus("halogen", parts.length ? `halogen ${parts.join(" · ")}` : undefined);
		} catch {
			ctx.ui.setStatus("halogen", undefined); // server down or not halogen: show nothing rather than stale numbers
		}
	};

	// footer + polling only while a halogen model is selected; other providers get nothing
	const stop = (ctx: any) => { clearInterval(timer); timer = undefined; prev = rate = undefined; ctx.ui.setStatus("halogen", undefined); };
	const sync = (provider: string | undefined, ctx: any) => {
		stop(ctx);
		if (provider !== "halogen") return;
		void poll(ctx);
		timer = setInterval(() => void poll(ctx), 5000);
		timer.unref?.();
	};
	// sidecar badge, any provider: llama-server sleeps (model unloaded) after 10 min idle, wakes on the next request
	let scTimer: ReturnType<typeof setInterval> | undefined;
	const scPoll = async (ctx: any) => {
		let s = "off";
		try {
			const p: any = await (await fetch(`${profile().sidecarUrl}/props`, { signal: AbortSignal.timeout(2000) })).json();
			s = p.is_sleeping ? "asleep" : "on";
		} catch {}
		ctx.ui.setStatus("sidecar", ctx.ui.theme.fg("muted", `sidecar ${s}`));
	};
	const scStop = (ctx: any) => { clearInterval(scTimer); scTimer = undefined; ctx.ui.setStatus("sidecar", undefined); };

	pi.on("session_start", async (_e: any, ctx: any) => {
		sync(ctx.model?.provider, ctx);
		scStop(ctx);
		if (!sidecarOn()) return;
		void scPoll(ctx);
		scTimer = setInterval(() => void scPoll(ctx), 10_000);
		scTimer.unref?.();
	});
	pi.on("model_select", async (e: any, ctx: any) => sync(e.model?.provider, ctx));
	pi.on("session_shutdown", async (_e: any, ctx: any) => { stop(ctx); scStop(ctx); });
}
