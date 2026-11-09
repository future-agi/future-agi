// Provider logos for catalog vendors, from the logos Future AGI already
// serves for its own providers (model_hub ProviderLogoUrls, read through
// /model-hub/develops/provider-status/ as the AI Providers page reads them).
// A vendor Future AGI has no logo for keeps its initial (LogoMark).

import { vendorSlug } from "./catalog";

// Catalog vendors whose Future AGI provider id is spelled differently (the
// ids model_hub's ProviderLogoUrls keys its logos by). Amazon's models carry
// the Bedrock mark and Microsoft's the Azure mark, the marks Future AGI
// already shows for those companies; a slug's "-" is the id's "_".
const FA_PROVIDER_ID = {
  google: "gemini",
  mistralai: "mistral",
  amazon: "bedrock",
  microsoft: "azure",
  together: "together_ai",
  fireworks: "fireworks_ai",
};

// Not drawn: the catalog's own router vendor. Whether customer screens name
// the upstream reseller is an open product decision; a logo would add
// to it.
const NO_LOGO = new Set(["openrouter"]);

/** The Future AGI provider id whose logo a catalog vendor takes, or null. */
export function logoKey(vendor) {
  const slug = vendorSlug(vendor);
  if (!slug || NO_LOGO.has(slug)) return null;
  return FA_PROVIDER_ID[slug] || slug.replace(/-/g, "_");
}

/** provider id -> logo URL, from the provider-status list. */
export function logoMap(faProviders) {
  const map = new Map();
  (Array.isArray(faProviders) ? faProviders : []).forEach((p) => {
    const url = p?.logoUrl || p?.logo_url;
    if (p?.provider && url) map.set(String(p.provider).toLowerCase(), url);
  });
  return map;
}

/** A catalog vendor's logo URL, or "". */
export function vendorLogo(vendor, logos) {
  const key = logoKey(vendor);
  return (key && logos?.get(key)) || "";
}
