// IT-R4 G6 P4 (rule 30): how a run ran, for the hover / focus card on
// each compared run's header — model(s), tool count + the first 6 tool
// names, MCP servers, where it ran, and what Steps / Turns count. Pure;
// reads provenanceOf (the run's config) and runModelsText.
import { provenanceOf } from "./provenanceModel";
import { runModelsText, runSpokenName } from "./runLabel";

// L7c g5 (identity:how-it-ran-hover): names no harness (an absent way to
// run); it says what the Steps count of this run is made of.
export const STEPS_NOTE = "Steps: the run's tool calls + model turns.";

const TOOLS_SHOWN = 6;

/**
 * @param {object} run  canonical run with `config`
 * @returns {{title: string, lines: {k: string, v: string}[], note: string}}
 */
export function runHowItRan(run) {
  if (!run) return { title: "", lines: [], note: STEPS_NOTE };
  let p = null;
  try {
    p = provenanceOf(run);
  } catch {
    p = null;
  }
  const lines = [];
  // IT-R5 G6 S1: the full identity — every model, the framework or harness
  // with its version, tools, MCP servers, permissions, where it ran.
  const c = run.config ?? {};
  const ids = (p?.models ?? [])
    .map((m) => (typeof m === "string" ? m : m?.id))
    .filter(Boolean);
  const models = ids.length ? [...new Set(ids)].join(", ") : runModelsText(run);
  if (models) lines.push({ k: ids.length > 1 ? "Models" : "Model", v: models });
  const framework = p?.source?.name ?? null;
  const version =
    p?.harness?.version ?? c.framework?.version ?? c.harness?.version ?? null;
  if (framework)
    lines.push({
      k: p?.kind === "harness" ? "Harness" : "Framework",
      v: version ? `${framework} ${version}` : framework,
    });
  const tools = (p?.tools ?? []).map((t) => t.name).filter(Boolean);
  if (tools.length) {
    const more = tools.length - TOOLS_SHOWN;
    lines.push({
      k: "Tools",
      v: `${tools.length}: ${tools.slice(0, TOOLS_SHOWN).join(", ")}${more > 0 ? ` +${more} more` : ""}`,
    });
  }
  const mcp = (p?.mcp ?? []).map((m) =>
    Number.isFinite(m.tools) ? `${m.name} (${m.tools} tools)` : m.name,
  );
  if (mcp.length) lines.push({ k: "MCP", v: mcp.join(", ") });
  if (p?.harness?.permissionsLabel || p?.harness?.permissions)
    lines.push({
      k: "Permissions",
      v: p.harness.permissionsLabel ?? p.harness.permissions,
    });
  const where = p?.where?.text ?? p?.where?.label ?? null;
  if (where) lines.push({ k: "Ran on", v: where });
  const title = [run.label, runSpokenName(run)].filter(Boolean).join(" ");
  return { title, lines, note: STEPS_NOTE };
}
