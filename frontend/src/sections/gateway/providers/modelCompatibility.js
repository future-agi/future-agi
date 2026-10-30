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

import { isSystemOneFormat } from "./utils";

export const RESPONSES_ONLY_TAG = "Responses API only";

export const DECISIONS_ONLY_TAG = "Decisions API only";

export const MODEL_LIST_SOURCE_HELP =
  "This list is fetched live from the provider's models endpoint.";

export const RESPONSES_ONLY_HELP =
  `Models tagged "${RESPONSES_ONLY_TAG}" are served on /v1/responses, not on ` +
  "chat completions. For chat completions, enter the provider's own model ID " +
  "manually.";

export const DECISIONS_ONLY_HELP =
  `Models tagged "${DECISIONS_ONLY_TAG}" are built in, since the listing may ` +
  "leave them out, and are served on /v1/decisions, not on chat completions.";

// System One providers are never probed for models, so model discovery can
// never turn into a billed decision call. The dialog offers a built-in list.
export const SYSTEM_ONE_MODELS_HELP =
  "Decision models are not fetched from the provider. Pick a listed model ID, " +
  "or type the one your endpoint serves.";

// Providers whose catalogue endpoint lists Responses-API models only. Keyed by
// saved provider name and by base-URL host, because the provider that needs
// this has no preset — it is configured through Custom / Self-hosted, so in
// create mode the URL is all there is to match on.
export const RESPONSES_ONLY_CATALOGUES = [
  { names: ["perplexity"], hosts: ["api.perplexity.ai"] },
];

// Decision models served on the same key as a chat provider. OpenAI's
// Decisions API is in beta and its models listing is not guaranteed to carry
// gpt-6-luna, while the gateway only routes a decision to an org provider that
// lists the model, so it is offered next to the live list.
export const DECISION_MODEL_CATALOGUES = [
  { names: ["openai"], hosts: ["api.openai.com"], models: ["gpt-6-luna"] },
];

// The host of a base URL, or "" when there is not one yet. The field is typed a
// character at a time, so "https:/" is an ordinary intermediate state.
const hostOf = (baseUrl) => {
  const match = String(baseUrl ?? "")
    .trim()
    .match(/^[a-z][a-z0-9+.-]*:\/\/(?:[^@/]*@)?([^/?#:]+)/i);
  return match ? match[1].toLowerCase() : "";
};

const matchesCatalogue = (entry, context) => {
  const name = String(context.providerName ?? "")
    .trim()
    .toLowerCase();
  const host = hostOf(context.baseUrl);
  return (
    (!!name && entry.names.includes(name)) ||
    (!!host && entry.hosts.some((h) => host === h || host.endsWith(`.${h}`)))
  );
};

/**
 * True when this provider's catalogue lists Responses-API-only models.
 *
 * Unknown providers are false, so their models render untagged rather than
 * guessed at — and are never hidden. A System One provider is false too: the
 * Perplexity Decider shares Perplexity's host, but none of its models are
 * chat or Responses models.
 *
 * @param {{providerName?: string, baseUrl?: string, apiFormat?: string}} context
 */
export function catalogueIsResponsesOnly(context = {}) {
  if (isSystemOneFormat(context.apiFormat)) return false;
  return RESPONSES_ONLY_CATALOGUES.some((entry) =>
    matchesCatalogue(entry, context),
  );
}

/**
 * The decision models a chat provider also serves, to offer beside its live
 * listing. Empty for a System One provider, whose whole list is decisions.
 *
 * @param {{providerName?: string, baseUrl?: string, apiFormat?: string}} context
 */
export function decisionModelsFor(context = {}) {
  if (isSystemOneFormat(context.apiFormat)) return [];
  return DECISION_MODEL_CATALOGUES.filter((entry) =>
    matchesCatalogue(entry, context),
  ).flatMap((entry) => entry.models);
}

/**
 * The picker's options: the live listing plus the decision models it left
 * out. Added only beside a listing that worked, so a built-in option never
 * stands in for proof that a key is good.
 */
export function withDecisionModels(models, context = {}) {
  const listed = Array.isArray(models) ? models : [];
  if (listed.length === 0) return listed;
  return Array.from(new Set([...listed, ...decisionModelsFor(context)]));
}

/** The subset of `models` to tag. Empty for a provider with no known rule. */
export function responsesOnlyModelSet(models, context = {}) {
  if (!catalogueIsResponsesOnly(context)) return new Set();
  return new Set(Array.isArray(models) ? models : []);
}

/**
 * The models a bulk "select all" should cover: the ones served on chat
 * completions. Tagged models are left out so they are not selected without an
 * explicit click — they stay individually selectable for Responses or
 * Decisions traffic.
 */
export function defaultSelectableModels(models, context = {}) {
  const tagged = responsesOnlyModelSet(models, context);
  const decisions = new Set(decisionModelsFor(context));
  return (Array.isArray(models) ? models : []).filter(
    (m) => !tagged.has(m) && !decisions.has(m),
  );
}
