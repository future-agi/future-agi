// W6 FE seam (W6-SCOPE.md §4.2): what a run uses, for Review's compare
// table (Model / Tools / Uses cells). Each W6 lane fills its slot
// (runUses/{prompt,agent,endpoint,human}.js) with
// `usesOf(card, {kind, run}) -> {model?, tools?, mcp?, ...}`; reviewModel.js
// spreads the result over its own cells, so an empty slot changes nothing.
// Pure.
import prompt from "./runUses/prompt";
import agent from "./runUses/agent";
import endpoint from "./runUses/endpoint";
import human from "./runUses/human";

/** Slots in their fixed order (Lane A, B, C); a later slot's key wins. */
export const RUN_USES_SLOTS = Object.freeze({ prompt, agent, endpoint, human });

/**
 * The cells a run's slots fill ({} when none).
 * @param {object} card  the wizard card (promptConfig item)
 * @param {{kind?: string, run?: object}} [ctx]  flattenRuns entry and kind
 */
export function runUsesOf(card, ctx = {}) {
  if (!card) return {};
  return Object.values(RUN_USES_SLOTS).reduce((acc, slot) => {
    if (typeof slot?.usesOf !== "function") return acc;
    const r = slot.usesOf(card, ctx);
    return r && typeof r === "object" ? { ...acc, ...r } : acc;
  }, {});
}
