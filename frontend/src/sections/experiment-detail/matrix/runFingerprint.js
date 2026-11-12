// IT-R5 G6 P3: a run header's config fingerprint (model, framework, tools)
// and the de-duplication of identical headers side by side. Pure.
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import { runModelsText } from "src/sections/common/Runs/runLabel";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * `gpt-4o-mini +1 · Agent graph · 6 tools · 1 MCP` (unknown parts drop).
 * @returns {{text: string, model: string, framework: string|null,
 *   tools: number, mcp: number}}
 */
export function runFingerprint(run) {
  let p = null;
  try {
    p = run?.config ? provenanceOf(run) : null;
  } catch {
    p = null;
  }
  const ids = (p?.models ?? [])
    .map((m) => (typeof m === "string" ? m : m?.id))
    .filter(Boolean);
  const model =
    runModelsText(run) ||
    (ids.length
      ? ids.length > 1
        ? `${ids[0]} +${ids.length - 1}`
        : ids[0]
      : "");
  const framework = p?.source?.name ?? null;
  const tools = (p?.tools ?? []).length;
  const mcp = (p?.mcp ?? []).length;
  const text = [
    model || null,
    framework,
    tools ? plural(tools, "tool") : null,
    mcp ? `${mcp} MCP` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return { text, model, framework, tools, mcp };
}

/**
 * Per column: the label of the column on its left when both show the same
 * name and fingerprint (`Same setup as #2F`), else null.
 * @param {{runId: string, label: string, name: string, fingerprint: string}[]} cols
 * @returns {Object<string, string|null>}
 */
export function sameSetupAs(cols) {
  const out = {};
  const list = cols ?? [];
  let root = null;
  list.forEach((c, i) => {
    const prev = list[i - 1];
    const same = Boolean(
      prev &&
        c.fingerprint &&
        prev.name === c.name &&
        prev.fingerprint === c.fingerprint,
    );
    // a run of identical headers all point at the first one
    root = same ? root ?? prev.label : null;
    out[c.runId] = same ? root : null;
  });
  return out;
}
