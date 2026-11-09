// W6 FE slot `runUses/endpoint` (W6-SCOPE.md §4.2; owner: Lane B
// `w6-endpoints`): Review's compare-table cells (Model / Tools / MCP /
// Permissions) of an endpoint run, through Lane A's `RunSettings/runUses.js`
// `runUsesOf(card, {kind, run})`. The card is the wizard card (promptConfig
// item). Pure. Null for a card that is not an endpoint source, and a cell
// this slot has nothing for is left out (Review keeps its own).
import {
  transformOf,
  transformSummary,
} from "src/sections/common/RunSources/endpoint/transformModel";
import { authSummary } from "src/sections/common/RunSources/endpoint/a2aAuthModel";
import { mcpEnvSecrets } from "src/sections/common/RunSources/endpoint/requestModel";

const s = (v) => String(v ?? "").trim();

const hostOf = (url) => {
  try {
    return new URL(s(url)).host;
  } catch {
    return s(url);
  }
};

const compact = (o) =>
  Object.fromEntries(
    Object.entries(o).filter(([, v]) => v != null && v !== ""),
  );

function httpUses(card) {
  const h = card.http ?? card.r4?.config?.endpoint ?? {};
  if (!s(h.url)) return null;
  const t = h.transform ?? {};
  const hasTransform =
    s(t.output) || s(t.tool_calls) || Object.values(t.usage ?? {}).some(s);
  const transform = hasTransform
    ? transformOf({ output_path: h.outputPath ?? h.output_path, transform: t })
    : null;
  const secrets = (h.headers ?? []).map((x) => s(x?.secret)).filter(Boolean);
  return compact({
    model: [
      `HTTP${s(h.preset?.name) ? ` · ${s(h.preset.name)}` : ""} · ${hostOf(h.url)}`,
      transform ? transformSummary(transform) : null,
    ]
      .filter(Boolean)
      .join(" · "),
    tools: transform?.tool_calls
      ? `from the response (${transform.tool_calls})`
      : null,
    permissions: secrets.length ? `secrets ${secrets.join(", ")}` : null,
  });
}

function a2aUses(card) {
  const a = card.a2a ?? {};
  if (!a.card && !s(a.card_url)) return null;
  return compact({
    model: `A2A · ${a.card?.name ?? hostOf(a.card_url)}${a.skill_id ? ` · skill ${a.skill_id}` : ""}`,
    permissions: authSummary(a.auth),
  });
}

function mcpUses(card) {
  const m = card.mcp_tool ?? {};
  if (!m.tool) return null;
  const env = mcpEnvSecrets(m.server).filter((x) => x.name && x.secret);
  const server = s(m.server?.name) || s(m.server?.url) || s(m.server?.command);
  return compact({
    tools: m.tool,
    mcp: [
      server || null,
      env.length
        ? `env ${env.map((x) => `${s(x.name)} ← secret ${s(x.secret)}`).join(", ")}`
        : null,
    ]
      .filter(Boolean)
      .join(" · "),
  });
}

/** Review's cells for an endpoint card, or null. */
export function endpointUses(card = {}, { kind } = {}) {
  const k = kind ?? card.targetType;
  if (k === "http") return httpUses(card);
  if (k === "a2a") return a2aUses(card);
  if (k === "mcp_tool") return mcpUses(card);
  return null;
}

const slot = { usesOf: endpointUses };

export default slot;
