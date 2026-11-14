// Keep the amount and its provenance together when flattening API metadata.
export const modelCostMetadata = (cost) => ({
  cost: cost?.total_cost,
  pricingSource: cost?.pricing_source,
});

export const formatModelCost = (amount, source) => {
  if (typeof amount !== "number" || !Number.isFinite(amount)) return "-";
  const estimated = [
    "default",
    "fallback",
    "decision_catalog_estimate",
    "litellm",
    "mixed_estimate",
  ].includes(source);
  return `${amount}${estimated ? " (estimated)" : ""}`;
};

export const modelCostSourceLabel = (source) => {
  const labels = {
    available_models: "Catalog pricing",
    default: "Default estimate, not a verified provider charge",
    fallback: "Custom rate estimate, not a verified provider charge",
    decision_catalog_estimate: "Decision model catalog estimate",
    litellm: "LiteLLM cost estimate",
    mixed_estimate: "Includes estimated costs",
    provider_usage: "Provider-reported cost",
    jev_flat_fee: "Flat fee",
  };
  return labels[source] || "Pricing source not recorded";
};
