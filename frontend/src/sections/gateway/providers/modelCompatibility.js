// Which of the models a provider lists can be routed to chat completions.
//
// A provider's models endpoint is a catalogue, not a statement about endpoints.
// Perplexity runs two APIs with disjoint model vocabularies, verified against
// the live API on 2026-10-06:
//
//   GET  /v1/models                                   -> 51 models, all of them
//                                                        Agent API models
//   POST /v1/agent (alias /v1/responses) + any of them -> 200
//   POST /chat/completions        + any of them        -> 400 "Invalid model"
//   POST /chat/completions        + sonar / sonar-pro  -> 200
//
// So every model the catalogue returns works — on the Responses API — and none
// of them work on chat completions. The models that *do* serve chat completions
// (`sonar`, `sonar-pro`) are not in the catalogue at all and are entered by
// hand. Tagging the whole catalogue is therefore the accurate statement; the
// tag names which endpoint to use, it does not mean the model is unusable.

export const RESPONSES_ONLY_TAG = "Responses API only";

export const MODEL_LIST_SOURCE_HELP =
  "This list is fetched live from the provider's models endpoint.";

export const RESPONSES_ONLY_HELP =
  `Models tagged "${RESPONSES_ONLY_TAG}" are served on /v1/responses, not on ` +
  "chat completions. For chat completions, enter the provider's own model ID " +
  "manually.";

// Providers whose catalogue endpoint lists Responses-API models only. Keyed by
// saved provider name and by base-URL host, because the provider that needs
// this has no preset — it is configured through Custom / Self-hosted, so in
// create mode the URL is all there is to match on.
export const RESPONSES_ONLY_CATALOGUES = [
  { names: ["perplexity"], hosts: ["api.perplexity.ai"] },
];

// The host of a base URL, or "" when there is not one yet. The field is typed a
// character at a time, so "https:/" is an ordinary intermediate state.
const hostOf = (baseUrl) => {
  const match = String(baseUrl ?? "")
    .trim()
    .match(/^[a-z][a-z0-9+.-]*:\/\/(?:[^@/]*@)?([^/?#:]+)/i);
  return match ? match[1].toLowerCase() : "";
};

/**
 * True when this provider's catalogue lists Responses-API-only models.
 *
 * Unknown providers are false, so their models render untagged rather than
 * guessed at — and are never hidden.
 *
 * @param {{providerName?: string, baseUrl?: string}} context
 */
export function catalogueIsResponsesOnly(context = {}) {
  const name = String(context.providerName ?? "")
    .trim()
    .toLowerCase();
  const host = hostOf(context.baseUrl);
  return RESPONSES_ONLY_CATALOGUES.some(
    (entry) =>
      (!!name && entry.names.includes(name)) ||
      (!!host &&
        entry.hosts.some((h) => host === h || host.endsWith(`.${h}`))),
  );
}

/** The subset of `models` to tag. Empty for a provider with no known rule. */
export function responsesOnlyModelSet(models, context = {}) {
  if (!catalogueIsResponsesOnly(context)) return new Set();
  return new Set(Array.isArray(models) ? models : []);
}

/**
 * The models a bulk "select all" should cover: the ones served on chat
 * completions. Tagged models are left out so they are not selected without an
 * explicit click — they stay individually selectable for Responses traffic.
 */
export function defaultSelectableModels(models, context = {}) {
  const tagged = responsesOnlyModelSet(models, context);
  return (Array.isArray(models) ? models : []).filter((m) => !tagged.has(m));
}
