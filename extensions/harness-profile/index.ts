/**
 * Active engine profile, written by `./harness use|setup` to <agent dir>/harness-profile.json.
 * Extensions import { profile, isMain } from "./harness-profile/index.ts" instead of assuming halogen.
 * No file = the legacy halogen defaults, so old installs behave exactly as before.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { getAgentDir } from "@earendil-works/pi-coding-agent";

export interface Profile { engine: string; provider: string; model: string; sidecarUrl: string; npu: boolean; npuUrl: string }
const LEGACY: Profile = { engine: "halogen", provider: "halogen", model: "halogen-qwen3.8-flash-next", sidecarUrl: "http://127.0.0.1:8090", npu: true, npuUrl: "http://127.0.0.1:8731" };

export const profile = (): Profile => {
	try { return { ...LEGACY, ...JSON.parse(fs.readFileSync(path.join(getAgentDir(), "harness-profile.json"), "utf8")) }; } catch { return LEGACY; }
};
/** false when the user opted out of the ling-tiny sidecar: its extensions then do nothing */
export const sidecarOn = (): boolean => {
	try { return fs.readFileSync(path.join(getAgentDir(), "harness-sidecar"), "utf8").trim() !== "0"; } catch { return true; } // saved by setup.sh / the wizard; absent = on
};
/** true when the session's model is the local engine this harness was set up for (false on cloud hybrid turns) */
export const isMain = (ctx: any): boolean => ctx?.model?.provider === profile().provider;

// pi loads every extensions/<dir>/index.ts; this one registers nothing
export default function () {}
