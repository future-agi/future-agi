// A2-15: what `More settings` (New experiment, step 1) holds that is not
// the default, for its collapsed header. Pure.
import { experimentTypeOptions } from "./constants";

const TEXT = "llm";

/**
 * @param {{experimentType?: string, startFrom?: {experimentId?: string,
 *   label?: string}|null}} values  the form's values
 * @returns {string[]} e.g. `["TTS", "starting from #2"]`; empty = defaults
 */
export function moreSettingsChanges({ experimentType, startFrom } = {}) {
  const out = [];
  if (experimentType && experimentType !== TEXT) {
    const o = experimentTypeOptions.find((x) => x.value === experimentType);
    out.push(o?.label ?? experimentType);
  }
  if (startFrom?.experimentId)
    out.push(`starting from ${startFrom.label ?? "an earlier experiment"}`);
  return out;
}

/**
 * v2.1 (type:llm): how many run cards a type switch would drop: cards with
 * a picked prompt, agent, model, endpoint or tool (an empty card is not
 * work). Pure.
 */
export function configuredCardCount(cards = []) {
  return (cards ?? []).filter((c) => {
    if (!c) return false;
    const models = (c.model ?? []).filter((m) =>
      m && typeof m === "object" ? m.value : m,
    );
    return Boolean(
      c.promptId ||
        c.agentId ||
        c.r4 ||
        models.length ||
        String(c.http?.url ?? "").trim() ||
        String(c.a2a?.card_url ?? "").trim() ||
        c.mcp_tool?.tool ||
        String(c.runName ?? "").trim(),
    );
  }).length;
}

/** The confirm's sentence: `Your 2 TTS runs are removed when you switch.` */
export function typeSwitchText(n, fromLabel = "this type") {
  const k = Math.max(0, Number(n) || 0);
  return k === 1
    ? `Your ${fromLabel} run is removed when you switch. Each experiment type has its own runs.`
    : `Your ${k} ${fromLabel} runs are removed when you switch. Each experiment type has its own runs.`;
}
