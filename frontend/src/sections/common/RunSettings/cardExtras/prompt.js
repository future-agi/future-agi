// W6 FE slot `cardExtras/prompt` (W6-SCOPE.md §4.2; owner: Lane A
// `w6-engine`). The extra create-body keys of a prompt card: `row_limits`
// (W6-16, A1) and `tool_schemas` (each tool's schema by name, so the Try
// and the run read the same descriptions and parameters, W6-1).
import { rowLimitsExtras } from "../rowLimitsModel";
import { cardTools } from "src/sections/develop-detail/Experiment/toolSetup";

const kindOf = (card) =>
  card?.targetType ?? (card?.agentId ? "agent" : "prompt");

const slot = {
  extrasOf(card) {
    if (kindOf(card) !== "prompt") return {};
    const schemas = Object.fromEntries(
      cardTools(card?.toolSetup ?? {}, card?.configuration?.tools)
        .filter((t) => t?.name && t.schema)
        .map((t) => [t.name, t.schema]),
    );
    return {
      ...rowLimitsExtras(card),
      ...(Object.keys(schemas).length ? { tool_schemas: schemas } : {}),
    };
  },
};

export default slot;
