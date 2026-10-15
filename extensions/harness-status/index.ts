// harness-status: /usage runs scripts/usage-stats.py inside pi; a footer status shows halogen load.
//   /usage [--all] [--top N]     (HARNESS_REPO overrides the repo path)
// Footer: "halogen kv 14% · 1 run · 0 wait · 62 t/s" polled from :8731/metrics every 5s; hidden when unreachable.
import * as path from "node:path";
import { type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BASE = process.env.HALOGEN_URL || "http://127.0.0.1:8731";
const REPO = process.env.HARNESS_REPO || path.join(process.env.HOME ?? "", "coding/qwen38-strix-halo-harness");
const metric = (txt: string, k: string) => { const m = txt.match(new RegExp(`^${k.replace(":", ":")}\\s+([0-9.eE+-]+)`, "m")); return m ? Number(m[1]) : undefined; };

export default function harnessStatus(pi: ExtensionAPI) {
	let timer: ReturnType<typeof setInterval> | undefined;
	let prev: { tok: number; sec: number } | undefined;
	let rate: number | undefined;

	pi.registerCommand("usage", {
		description: "MCP / skill / extension usage report (scripts/usage-stats.py); pass --all for lifetime",
		handler: async (args: string, ctx: any) => {
			const r = await pi.exec("python3", [path.join(REPO, "scripts/usage-stats.py"), ...(args ?? "").split(/\s+/).filter(Boolean)], { cwd: REPO, timeout: 30_000 });
			ctx.ui.notify((r.stdout || r.stderr || "no output").trim(), r.code === 0 ? "info" : "error");
		},
	});

	const poll = async (ctx: any) => {
		try {
			const txt = await (await fetch(`${BASE}/metrics`, { signal: AbortSignal.timeout(2000) })).text();
			const kv = metric(txt, "llamacpp:kv_cache_usage_ratio"), run = metric(txt, "llamacpp:requests_processing"), wait = metric(txt, "halogen:kv_pool_waiting_for_room");
			const tok = metric(txt, "llamacpp:tokens_predicted_total"), sec = metric(txt, "llamacpp:tokens_predicted_seconds_total");
			if (tok !== undefined && sec !== undefined) {
				if (prev && sec > prev.sec) rate = (tok - prev.tok) / (sec - prev.sec); // last decode rate, kept while idle
				prev = { tok, sec };
			}
			const parts = [kv !== undefined ? `kv ${Math.round(kv * 100)}%` : "", run !== undefined ? `${run} run` : "", wait ? `${wait} wait` : "", rate ? `${Math.round(rate)} t/s` : ""].filter(Boolean);
			ctx.ui.setStatus("halogen", parts.length ? `halogen ${parts.join(" · ")}` : undefined);
		} catch {
			ctx.ui.setStatus("halogen", undefined); // server down or not halogen: show nothing rather than stale numbers
		}
	};

	pi.on("session_start", async (_e: any, ctx: any) => {
		clearInterval(timer);
		void poll(ctx);
		timer = setInterval(() => void poll(ctx), 5000);
		timer.unref?.();
	});
	pi.on("session_shutdown", async (_e: any, ctx: any) => { clearInterval(timer); ctx.ui.setStatus("halogen", undefined); });
}
