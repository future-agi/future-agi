// W6 FE seam (W6-SCOPE.md §4.2): the extra create-body keys of a wizard
// card. Each W6 lane fills its own slot (cardExtras/{prompt,agent,endpoint,
// human}.js) with `extrasOf(card) -> {}`; experimentFormValues.js spreads
// the result into the run's entry, so new card fields (row_limits, output,
// node_overrides, preset, context_source, human) reach the create body
// without any lane editing that file. Empty slots add nothing. Pure.
import prompt from "./cardExtras/prompt";
import agent from "./cardExtras/agent";
import endpoint from "./cardExtras/endpoint";
import human from "./cardExtras/human";

/** Slots in their fixed order (Lane A, B, C); a later slot's key wins. */
export const CARD_EXTRA_SLOTS = Object.freeze({
  prompt,
  agent,
  endpoint,
  human,
});

/** The extra keys a card adds to each of its runs' entries ({} when none). */
export function cardExtrasOf(card) {
  if (!card) return {};
  return Object.values(CARD_EXTRA_SLOTS).reduce((acc, slot) => {
    if (typeof slot?.extrasOf !== "function") return acc;
    const r = slot.extrasOf(card);
    return r && typeof r === "object" ? { ...acc, ...r } : acc;
  }, {});
}

/** The entry with its card's extra keys; the same object when none. */
export function withCardExtras(entry, card) {
  const extras = cardExtrasOf(card);
  return Object.keys(extras).length ? { ...entry, ...extras } : entry;
}
