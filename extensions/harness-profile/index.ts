/**
 * Active engine profile, written by `./harness use|setup` to <agent dir>/harness-profile.json.
 * Extensions import { profile, isMain } from "./harness-profile/index.ts" instead of assuming halogen.
 * No file = the legacy halogen defaults, so old installs behave exactly as before.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { getAgentDir } from "@earendil-works/pi-coding-agent";

/** One JSON line per sidecar/NPU/guard event in <agent dir>/harness-events.jsonl (scripts/usage-stats.py, session-audit.sh read it). Never throws. */
export const ev = (o: object) => { try { fs.appendFileSync(path.join(getAgentDir(), "harness-events.jsonl"), JSON.stringify({ t: new Date().toISOString(), cwd: process.cwd(), ...o }) + "\n"); } catch {} };

export interface Profile { engine: string; provider: string; model: string; sidecarUrl: string; npu: boolean; npuUrl: string }
const LEGACY: Profile = { engine: "halogen", provider: "halogen", model: "halogen-qwen3.8-flash-next", sidecarUrl: "http://127.0.0.1:8090", npu: true, npuUrl: "http://127.0.0.1:8731" };

export const profile = (): Profile => {
	try { return { ...LEGACY, ...JSON.parse(fs.readFileSync(path.join(getAgentDir(), "harness-profile.json"), "utf8")) }; } catch { return LEGACY; }
};
/** false when the user opted out of the ling-tiny sidecar: its extensions then do nothing */
export const sidecarOn = (): boolean => {
	try { return fs.readFileSync(path.join(getAgentDir(), "harness-sidecar"), "utf8").trim() !== "0"; } catch { return true; } // saved by setup.sh / the wizard; absent = on
};
/** Saved weights answer (setup.sh / `./harness config` -> weights): "stock" | "abliterated"; absent = stock.
 *  The uncensored build removes the model's instinct to push back, which is what the
 *  tool-result firewall in auto-guard.ts compensates for — extensions read it here. */
export const weights = (): string => {
	try { return fs.readFileSync(path.join(getAgentDir(), "harness-weights"), "utf8").trim() || "stock"; } catch { return "stock"; }
};
// ---- outside content: text written by somebody else (network, another machine) ----
// shared by auto-guard (optional NPU screen) and taint-gate (confirm risky actions afterwards)
const NET_TOOL = /^(webfetch|websearch|fetch|fetch_url|read_url|browser|list_mcp_resources|read_mcp_resource|list_mcp_resource_templates)$/;
const NET_MCP = /^mcp__(ddg|jina|firecrawl|exa|brave|tavily|perplexity|context7|playwright|browserless|http)[_t]/;
/** bash that pulled bytes from elsewhere: its stdout carries remote text. */
const NET_BASH = /\b(curl|wget|lynx|httpie|ssh|scp|rsync|git\s+(clone|fetch|pull)|npm\s+(view|show|info)|pip(?:3)?\s+download|docker\s+pull)\b|https?:\/\//i;

/** Is this tool result outside content? Local file reads and project greps are not. */
export function isOutsideContent(toolName: string, input: any): boolean {
	if (NET_TOOL.test(toolName) || NET_MCP.test(toolName)) return true;
	if (toolName === "bash") return NET_BASH.test(String(input?.command ?? ""));
	return false;
}

/** true when the session's model is the local engine this harness was set up for (false on cloud hybrid turns) */
export const isMain = (ctx: any): boolean => ctx?.model?.provider === profile().provider;

// pi loads every extensions/<dir>/index.ts; this one registers nothing
export default function () {}
