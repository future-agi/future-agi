import { paths } from "src/routes/paths";
import { BILLING_MODE_LABELS, providerLabel } from "../utils/providerLabel";

const SUPPORTED = new Set([
  "model",
  "provider",
  "billing_mode",
  "application",
  "service",
  "status_code",
  "api_key_id",
  "user_id",
]);
const billingValue = (value) =>
  Object.entries(BILLING_MODE_LABELS).find(
    ([, label]) => label === value,
  )?.[0] || value;

// Logs treats these filters as comma-separated lists. Encoding a comma in a
// value does not make it an exact match. Synthetic and missing groups also
// cannot identify a set of requests: offer the range, explicitly ungrouped.
function exactValue(value, key) {
  return (
    value !== "" &&
    value === value.trim() &&
    (["api_key_id", "user_id"].includes(key) || !value.includes(",")) &&
    !/^(other|unknown)$/i.test(value)
  );
}

/** Pure navigation only: no fetching, replay, rounding or relative time. */
export function buildLogsDrilldown({
  start,
  end,
  gatewayId,
  dimensions = {},
  isError = false,
} = {}) {
  if (!start || !end) return { href: null, scoped: false };
  const params = new URLSearchParams({
    started_after: start,
    started_before: end,
  });
  if (gatewayId) params.set("gateway_id", gatewayId);
  if (isError) params.set("is_error", "true");
  const entries = Object.entries(dimensions).map(([key, raw]) => {
    let value = String(raw ?? "");
    if (key === "billing_mode") value = billingValue(value);
    if (key === "provider")
      value = providerLabel(value, billingValue(dimensions.billing_mode));
    return [key, value];
  });
  const scoped =
    entries.length > 0 &&
    entries.every(
      ([key, value]) =>
        SUPPORTED.has(key) &&
        exactValue(value, key) &&
        (key !== "status_code" || /^\d+$/.test(value)),
    );
  if (scoped) entries.forEach(([key, value]) => params.set(key, value));
  return { href: `${paths.dashboard.gateway.logs}?${params}`, scoped };
}
