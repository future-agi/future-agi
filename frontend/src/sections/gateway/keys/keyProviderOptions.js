// The providers a key's "Allowed providers" list offers: the org's own, and,
// when the organization has Future AGI credits enabled on a deployment that
// offers them, the provider the Future AGI catalogue is served by. A key restricted to a list reaches only the
// providers on it (the gateway's key ACL), so without that option a
// restricted key could never reach Future AGI models.

// The label a Future AGI provider id is shown with.
export function futureAgiProviderLabel(id) {
  return `Future AGI credits (${id})`;
}

// The provider ids the Future AGI catalogue names: each model's provider,
// and each catalogue source's.
export function futureAgiProviderIds(catalog) {
  const ids = [];
  const add = (id) => {
    if (typeof id === "string" && id && !ids.includes(id)) ids.push(id);
  };
  const models = Array.isArray(catalog?.models) ? catalog.models : [];
  models.forEach((m) => add(m?.provider));
  const sources = Array.isArray(catalog?.providers) ? catalog.providers : [];
  sources.forEach((p) => add(p?.provider_id ?? p?.provider ?? p?.id));
  return ids;
}

// options: provider ids, the org's own first, then the Future AGI ones
// (only when futureAgiOffered). labelFor: how an id is shown.
export function keyProviderOptions({ providers, futureAgiOffered, catalog }) {
  const own =
    providers && typeof providers === "object" && !Array.isArray(providers)
      ? Object.keys(providers).sort()
      : [];
  const futureAgi = futureAgiOffered
    ? futureAgiProviderIds(catalog).filter((id) => !own.includes(id))
    : [];
  const options = [...own, ...futureAgi];
  const labelFor = (id) =>
    futureAgi.includes(id) ? futureAgiProviderLabel(id) : String(id ?? "");
  return { options, labelFor };
}
