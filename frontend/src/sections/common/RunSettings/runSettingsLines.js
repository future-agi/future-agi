// W6 (A1): the run settings run details show from a run's config, as
// `[label, text]` lines: the limits per row (W6-16) and each Live tool's
// host (W6-1, `tools:all-mock`: "issue_refund → sandbox.acme.ai"). Pure.
import { rowLimitsLine } from "./rowLimitsModel";

const hostOf = (url) => {
  try {
    return new URL(String(url)).host || null;
  } catch {
    return null;
  }
};

/** `issue_refund → sandbox.acme.ai` per Live tool with an endpoint. */
export function liveToolHosts(config) {
  return (config?.tools ?? [])
    .filter((t) => t?.name && t.mode === "live" && hostOf(t.endpoint))
    .map((t) => `${t.name} → ${hostOf(t.endpoint)}`);
}

/** The lines (none when the run sets nothing). */
export function runSettingsLines(config) {
  if (!config) return [];
  const out = [];
  const limits = rowLimitsLine(config.row_limits);
  if (limits)
    out.push(["Limits per row", limits.replace(/^Limits per row · /, "")]);
  const hosts = liveToolHosts(config);
  if (hosts.length) out.push(["Live tools", hosts.join(" · ")]);
  return out;
}
