import { formatExactMoney } from "src/sections/common/format/money";
import { CAPABILITIES, formatTokenCount, hasOnlyRequestPrice } from "../catalog";
import { ACCESS } from "../catalogRows";
import { hasInclusivePrice, inclusivePriceRows } from "../inclusivePricing";
import { perRequestPrices, pricingRows } from "../detail/modelFacts";
import { MANAGED_MODE_LABEL, OWN_KEY_LABEL } from "../../utils/providerLabel";

// One price per model and dimension: what the organization pays. Where
// Future AGI credits can buy the model that is the price a request on credits
// is charged; otherwise it is the provider's catalog quote, which an own key
// pays. The provider's quote is never shown beside a credits price, nor the
// margin between them.
export const COMPARE_FIELDS = [
  ["input", "Input / 1M tokens"],
  ["output", "Output / 1M tokens"],
  ["otherPrices", "Other billing units / tiers"],
  ["context", "Context window"],
  ["maxOutput", "Max output"],
  ["capabilities", "Capabilities"],
  ["access", "Access"],
  ["catalogState", "Catalog route state"],
  ["health", "Observed route health"],
];
const known = (value, reason = "") => ({ value, reason });
const unknown = (reason) => known("—", reason);
const soldOnCredits = (row) => [ACCESS.MANAGED, ACCESS.BOTH].includes(row?.access);
const price = (value, reason) => value == null || formatExactMoney(value) === "—"
  ? unknown(reason) : known(formatExactMoney(value));

const ON_CREDITS = `On ${MANAGED_MODE_LABEL}: what a request is charged.`;
const NOT_ON_CREDITS = "The provider's catalog quote; with your own key, your provider bills you directly.";

function priceFacts(row, markupPercent) {
  const model = row.model || {};
  const sold = soldOnCredits(row);
  const unavailable = row.accessReason || "This model is not available on Future AGI credits.";
  const routeNote = sold ? ON_CREDITS : NOT_ON_CREDITS;
  const inclusive = hasInclusivePrice(model);
  const unreliable = model.state === "blocked" || model.unpriced || model.stale_price ||
    (model.dynamic_price && !inclusive) || (model.inclusive_price && !inclusive);
  const reason = unreliable
    ? model.state_reason || "The catalog does not provide a current fixed quote for this model."
    : inclusive
      ? "Inclusive billing uses the stated units below, not a scalar token price."
      : hasOnlyRequestPrice(model)
        ? "This model is priced per request, not per token."
        : "This token price is not reported in the catalog.";
  const rates = !unreliable && !inclusive && !hasOnlyRequestPrice(model)
    ? pricingRows(model, { sold, markupPercent }) : [];
  // A credits row with no billed quote and no reported markup has no price
  // (pricingRows leaves it null): never the provider's lower quote instead.
  const pick = (item) => (sold ? item?.managed : item?.provider);
  const tokenPrice = (label) => {
    const rate = rates.find((item) => item.label === label);
    if (sold && rate && pick(rate) == null) {
      return unknown(`The price on ${MANAGED_MODE_LABEL} is not reported in the catalog.`);
    }
    const result = price(pick(rate), reason);
    return result.value !== "—" ? { ...result, reason: routeNote } : result;
  };

  let otherPrices = unknown("No additional billing units or tiers are reported in the catalog.");
  const describe = (label, value, unit) => `${label}: ${value === "—" ? "—" : `${value}${unit}`}`;
  if (unreliable) otherPrices = unknown(reason);
  else if (inclusive) {
    const dimensions = inclusivePriceRows(model, markupPercent);
    otherPrices = dimensions.length
      ? known(dimensions.map((item) => describe(item.label,
        sold ? item.charged : item.provider, ` / ${item.unit}`)).join("\n"),
      "Inclusive requests are billed from the usage the provider reports, not a sum of the quoted dimensions.")
      : unknown("This inclusive router has no fixed quote; requests are billed from the usage the provider reports.");
  } else {
    const extra = rates.filter((item) => !["Input", "Output"].includes(item.label))
      .map((item) => describe(item.label, formatExactMoney(pick(item)), " / 1M tokens"));
    perRequestPrices(model, { sold, markupPercent }).forEach((item) => {
      extra.push(describe(item.label, formatExactMoney(pick(item)), ""));
    });
    if (extra.length) otherPrices = known(extra.join("\n"), routeNote);
  }
  const facts = { input: tokenPrice("Input"), output: tokenPrice("Output"), otherPrices };
  // A model credits cannot buy says why beside its own-key quote.
  if (!sold) {
    for (const key of ["input", "output"]) {
      if (facts[key].value !== "—") facts[key] = { ...facts[key], reason: `${unavailable} ${NOT_ON_CREDITS}` };
    }
  }
  return facts;
}

function accessFact(row) {
  const modes = [];
  if (soldOnCredits(row)) {
    modes.push(`${MANAGED_MODE_LABEL} · ${row.managedOn === true ? "on" : "off"}`);
  }
  const verified = (row.providerAccessState ?? "ready") === "ready";
  if (verified && [ACCESS.OWN_KEY, ACCESS.BOTH].includes(row.access)) modes.push(OWN_KEY_LABEL);
  if (!verified) {
    const reason = "Own-key access is not verified. Retry the provider read in the catalog.";
    return modes.length ? known(modes.join(" + "), reason) : unknown(reason);
  }
  return modes.length ? known(modes.join(" + "), row.accessReason) : known("Not callable", row.accessReason);
}

// Only loaded catalog facts. Catalog state is NOT an observed-health verdict;
// opening this comparison never starts traffic queries or inference requests.
export function comparisonFacts(row, markupPercent) {
  if (!row) return Object.fromEntries(COMPARE_FIELDS.map(([key]) =>
    [key, unknown("This model is not in the loaded catalog. It may be unavailable in this gateway.")],
  ));
  const model = row.model || {};
  const caps = CAPABILITIES.filter((cap) => cap.test(model)).map((cap) => cap.label);
  const tokens = (value, name) => typeof value === "number" && value > 0 && Number.isFinite(value)
    ? known(`${formatTokenCount(value)} tokens`)
    : unknown(`The ${name} is not reported in the catalog.`);
  const states = { active: "Active", degraded: "Degraded", deprecated: "Deprecated", blocked: "Blocked" };
  return {
    ...priceFacts(row, markupPercent),
    context: tokens(row.context, "context window"),
    maxOutput: tokens(row.maxOutput, "maximum output"),
    capabilities: caps.length ? known(caps.join(", "), "Only capabilities listed in the catalog are shown.")
      : unknown("No supported capabilities are reported in the catalog; absence is not proof of incompatibility."),
    access: accessFact(row),
    catalogState: states[model.state] ? known(states[model.state], model.state_reason || "Catalog state is not a live health check.")
      : unknown("Route state is not reported in the catalog."),
    health: unknown("Observed route health is not included in the catalog. Open model details for live health."),
  };
}
