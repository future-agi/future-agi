// W6 FE slot `cardExtras/agent` (W6-SCOPE.md §4.2).
// The extra create-body keys of an agent card: `row_limits`
// (W6-16, A1).
import { rowLimitsExtras } from "../rowLimitsModel";

const kindOf = (card) =>
  card?.targetType ?? (card?.agentId ? "agent" : "prompt");

const slot = {
  extrasOf(card) {
    if (kindOf(card) !== "agent") return {};
    return rowLimitsExtras(card);
  },
};

export default slot;
