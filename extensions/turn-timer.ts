// turn-timer: per-turn elapsed wall time in the footer. pi has no built-in
// turn duration display (opencode does) - this closes that gap.
// Shows a ticking clock while the agent works, then freezes the final duration.

export default function turnTimer(pi: import("@earendil-works/pi-coding-agent").ExtensionAPI) {
	let start: number | null = null;
	let last: string | null = null;

	const fmt = (s: number) => {
		const m = Math.floor(s / 60);
		const r = s % 60;
		return m > 0 ? `${m}m${String(r).padStart(2, "0")}s` : `${r}s`;
	};

	pi.on("session_start", async (_event, ctx) => {
		// unref: the 1s tick must not keep the process alive after the agent loop
		// ends (pi -p hung indefinitely at exit without this)
		const iv = setInterval(() => {
			if (start !== null) {
				last = fmt(Math.round((Date.now() - start) / 1000));
				ctx.ui.setStatus("turn-timer", ctx.ui.theme.fg("muted", `\u23f1 ${last}`));
			}
		}, 1000);
		(iv as any).unref?.();
	});

	pi.on("before_agent_start", async () => {
		start = Date.now();
	});

	pi.on("agent_end", async () => {
		if (start !== null) last = fmt(Math.round((Date.now() - start) / 1000));
		start = null;
	});
}
