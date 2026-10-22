// Readers for the billing and auto-routing facts the gateway writes into a
// request's metadata. Values arrive as strings; auto_decision is a JSON
// document. Logs from older gateways carry some keys or none.

export const asNumber = (value) => {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

export const asText = (value) =>
  value == null || value === "" ? null : String(value);

export const asList = (value) => {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value !== "string") return [];
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
};

const isObject = (value) =>
  value != null && typeof value === "object" && !Array.isArray(value);

/**
 * Parses metadata.auto_decision. Returns null when it is missing or not a JSON
 * object; ranked, rejected and fallbacks are always arrays. affinity (a
 * string) and constraints (an object) come with auto-decision-v2 and are
 * null on decisions that do not carry them.
 */
export function parseAutoDecision(raw) {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!isObject(value)) return null;
  return {
    ...value,
    needs: asList(value.needs),
    ranked: Array.isArray(value.ranked) ? value.ranked.filter(isObject) : [],
    rejected: Array.isArray(value.rejected)
      ? value.rejected.filter(isObject)
      : [],
    fallbacks: asList(value.fallbacks),
    affinity: asText(value.affinity),
    constraints: isObject(value.constraints) ? value.constraints : null,
  };
}
