// Pure helpers for tool-trim. Rules: never touch short output, never drop the head or the tail
// (errors and final results live there), and the full text is always recoverable from a file.

const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
export const LIMITS = { chars: 16_000, lines: 400, head: 60, tail: 140, lineChars: 2_000 };

export function clean(text: string): string {
	// carriage-return progress bars: keep only the last frame of each line
	const noCr = text.replace(ANSI, "").split("\n").map((l) => (l.includes("\r") ? l.split("\r").filter(Boolean).pop() ?? "" : l));
	const out: string[] = [];
	for (let i = 0; i < noCr.length; ) {
		let j = i;
		while (j + 1 < noCr.length && noCr[j + 1] === noCr[i]) j++;
		const n = j - i + 1;
		out.push(noCr[i]);
		if (n >= 3) out.push(`[previous line repeated ${n - 1} more times]`);
		else for (let k = 1; k < n; k++) out.push(noCr[i]);
		i = j + 1;
	}
	return out.join("\n");
}

/** Returns the text to show, plus whether the middle was cut (caller must save the original then). */
export function trim(text: string, savedTo: string): { text: string; cut: boolean } {
	const lines = clean(text).split("\n").map((l) => (l.length > LIMITS.lineChars ? `${l.slice(0, LIMITS.lineChars)} [line cut, ${l.length} chars]` : l));
	const joined = lines.join("\n");
	if (lines.length <= LIMITS.lines && joined.length <= LIMITS.chars) return { text: joined, cut: false };
	if (lines.length <= LIMITS.head + LIMITS.tail) return { text: joined, cut: false }; // few but long lines: already capped per line
	const head = lines.slice(0, LIMITS.head), tail = lines.slice(-LIMITS.tail);
	const omitted = lines.length - head.length - tail.length;
	const marker = `[tool-trim: ${omitted} middle lines omitted of ${lines.length}; full original output: ${savedTo}]`;
	return { text: [...head, marker, ...tail].join("\n"), cut: true };
}

// Commands whose output the agent already bounded itself, or that read a saved full output back.
export const BOUNDED = /\|\s*(head|tail)\b|^\s*(head|tail)\b|^\s*sed\s+-n\b|pi-out\//;
