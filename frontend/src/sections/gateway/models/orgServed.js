// A model page for a model the catalog has no row for, that the
// organization's own providers serve: on a deployment that offers no managed
// models (it has no catalog), or one whose catalog does not list the model.
//
// The page's id is "<provider>/<model>" (modelLinks.js). The gateway routes
// that id to the org's provider of that id, in any case, when the provider
// lists <model>, and to a provider that lists the whole id as it is
// (agentcc-gateway server/handlers.go orgModelMatches). So the page calls the
// model by its id, which reaches that provider and no other.

import { OWN_KEY_LABEL } from "../utils/providerLabel";
import { ACCESS } from "./catalogRows";

const listOf = (value) => (Array.isArray(value) ? value : []);
const providerLabel = (p) => p?.display_name || p?.name || p?.id || "";

// Whether a provider of id `providerId` that lists `listed` serves `id`.
function serves(providerId, listed, id) {
  if (listed === id) return true;
  const at = id.indexOf("/");
  return (
    at > 0 &&
    id.slice(0, at).toLowerCase() === providerId.toLowerCase() &&
    listed === id.slice(at + 1)
  );
}

/**
 * The org providers that serve `id`, as model routes: { providerId,
 * providerName, sendAs: the id, listedAs: the model the provider lists,
 * pushed: whether the gateway has the provider, statusReason }.
 */
export function orgServedRoutes(providers, id) {
  if (typeof id !== "string" || !id) return [];
  return listOf(providers).flatMap((p) => {
    const providerId = String(p?.id || p?.name || "");
    if (!providerId) return [];
    const listed = listOf(p.models).find((m) => serves(providerId, m, id));
    if (listed == null) return [];
    return [
      {
        providerId,
        providerName: providerLabel(p),
        sendAs: id,
        listedAs: listed,
        pushed: p.pushed !== false,
        statusReason: p.pushed === false ? String(p.status_reason || "") : "",
      },
    ];
  });
}

/**
 * The model page id for `model` as the org provider `providerId` lists it:
 * "<provider>/<model>", which the gateway routes to that provider. Null when
 * no org provider serves that id, so a link built from it always opens a
 * page with a route and Try it.
 */
export function orgModelPageId(providers, providerId, model) {
  if (!providerId || typeof model !== "string" || !model) return null;
  const id = `${providerId}/${model}`;
  return orgServedRoutes(providers, id).length ? id : null;
}

/**
 * The model page's row (catalogRows.js buildCatalogRows' shape) for `id`
 * when an org provider serves it; null when none does. The catalog knows
 * nothing more about it, so only its routes are filled in.
 */
export function orgServedRow(id, providers) {
  const ownRoutes = orgServedRoutes(providers, id);
  if (!ownRoutes.length) return null;
  const at = id.indexOf("/");
  const callable = ownRoutes.some((r) => r.pushed !== false);
  return {
    id,
    model: { id },
    name: at > 0 ? id.slice(at + 1) : id,
    vendor: "",
    vendorName: ownRoutes.map((r) => r.providerName).join(", "),
    vendorLogo: "",
    // A configured model ID says nothing about its supported request kinds.
    // Consumers must use uncatalogued to disclose unknown capabilities.
    type: "unknown",
    typeLabel: "Not reported",
    access: callable ? ACCESS.OWN_KEY : ACCESS.NONE,
    accessLabel: callable ? OWN_KEY_LABEL : "Not callable",
    accessReason: callable ? "" : "This provider is not synced to the gateway. Review it under Providers.",
    managedOn: false,
    ownRoutes,
    input: null,
    output: null,
    context: null,
    maxOutput: null,
    released: null,
    retirement: null,
    capabilities: [],
    uncatalogued: true,
  };
}
