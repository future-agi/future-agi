// When no model in a menu can run here. An organization with no provider
// key gets every model-list row back unusable (credential_mode
// "unavailable"), so the menus say what works instead.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import { modelPath } from "src/sections/gateway/models/modelLinks";
import { normalizeModelOption, pickerEligibility } from "./common";
import { NO_USABLE_MODEL_COPY as COPY } from "./constants";

export const AI_PROVIDERS_HREF = paths.dashboard.settings.aiProviders;
export const GATEWAY_MODELS_HREF = paths.dashboard.gateway.models;
export const GATEWAY_KEYS_HREF = paths.dashboard.gateway.keys;

// Which credits path the sentence names, if any.
export const CREDITS_ROUTE = Object.freeze({
  NONE: "none",
  TRY_IT: "try_it",
  API_KEY: "api_key",
});

const CREDITS_COPY = Object.freeze({
  [CREDITS_ROUTE.TRY_IT]: Object.freeze({
    start: COPY.TRY_IT_START,
    link: COPY.TRY_IT_LINK,
    end: COPY.TRY_IT_END,
  }),
  [CREDITS_ROUTE.API_KEY]: Object.freeze({
    start: COPY.API_KEY_START,
    link: COPY.API_KEY_LINK,
    end: COPY.API_KEY_END,
  }),
});

/**
 * The credits part ({ start, link, end }; `link` is the link label), or null
 * when there is none.
 * @param {string} route one of CREDITS_ROUTE
 */
export const creditsCopy = (route) => CREDITS_COPY[route] ?? null;

/**
 * The sentence as plain text (snackbars, tests).
 * @param {string} [route] one of CREDITS_ROUTE
 */
export const noUsableModelSentence = (route = CREDITS_ROUTE.NONE) => {
  const credits = creditsCopy(route);
  return [
    COPY.START,
    COPY.PROVIDERS_LINK,
    COPY.PROVIDERS_END,
    ...(credits ? [credits.start, credits.link, credits.end] : [COPY.END]),
  ].join("");
};

/**
 * Where the credits link goes: the model's Try it tab in AI Gateway → Models
 * when one model applies and has a gateway page, else the models list; the
 * gateway's API keys for the API-key path.
 * @param {string} route one of CREDITS_ROUTE
 * @param {{ provider?: string, model?: string }} [model]
 */
export const creditsLinkHref = (route, model) => {
  if (route === CREDITS_ROUTE.API_KEY) return GATEWAY_KEYS_HREF;
  if (route !== CREDITS_ROUTE.TRY_IT) return null;
  const name = model?.model ?? "";
  const provider = model?.provider ?? "";
  const id = name.includes("/") || !provider ? name : `${provider}/${name}`;
  return modelPath(id, "try") ?? GATEWAY_MODELS_HREF;
};

/**
 * True when the menu has loaded at least one model and none can be picked
 * to run (the shared picker's own eligibility). Placeholder rows ("No model
 * found") are not models.
 * @param {Array<object>} options normalized picker options
 */
export const noModelUsable = (options) => {
  const rows = (options ?? []).filter(
    (option) => option && option.value !== "no",
  );
  return (
    rows.length > 0 &&
    rows.every((option) => !pickerEligibility(option).selectable)
  );
};

/**
 * Whether one raw model-list row can run, by the shared picker's rule.
 * @param {object} row a models_list item
 */
export const isRowUsable = (row) =>
  pickerEligibility(normalizeModelOption(row)).selectable;

// Two reads the AI Gateway pages already make, with their query keys and
// payloads, so both share one cache entry each: the managed-models status
// (useManagedModelsStatus, models/hooks/useManagedModels.js) and the gateway
// list (GatewayContext.jsx). They are read here directly because those
// modules pull the gateway's config hooks and route context into every menu.
const MANAGED_MODELS_STATUS_KEY = ["agentcc-managed-models"];
const GATEWAYS_KEY = ["agentcc-gateways"];

/** @returns {Promise<object|null>} the status, or null where none is served */
export const fetchManagedModelsStatus = async () => {
  const url = endpoints?.gateway?.managedModels;
  if (!url) return null;
  const { data } = await axios.get(url, { timeout: 30000 });
  return data?.result ?? null;
};

/** @returns {Promise<object|null>} the gateway list response, as served */
export const fetchGateways = async () => {
  const url = endpoints?.gateway?.list;
  if (!url) return null;
  const { data } = await axios.get(url, { timeout: 30000 });
  return data ?? null;
};

// Credits are on when the gateway offers models on Future AGI credits and
// this organization has turned them on. Anything else (off, not offered, not
// read, failed) means no credits part.
export const creditsOnFromStatus = (status) =>
  status?.available === true && status?.enabled === true;

// The gateway serves delegated calls (its Try it tab) unless it says it does
// not: a missing delegated_playground_available reads as available, as the
// gateway pages read it. Only an explicit false switches to the API-key path.
export const creditsRouteFrom = (status, gateways) => {
  if (!creditsOnFromStatus(status)) return CREDITS_ROUTE.NONE;
  const gateway = Array.isArray(gateways?.result) ? gateways.result[0] : null;
  return gateway?.delegated_playground_available === false
    ? CREDITS_ROUTE.API_KEY
    : CREDITS_ROUTE.TRY_IT;
};

/**
 * The credits path, read once at the moment it is needed. A failed status
 * read is no credits; a failed gateway read leaves the Try it path.
 * @param {{ fetchStatus?: () => Promise<any>, fetchGatewayList?: () => Promise<any> }} [options]
 */
export const readFutureAgiCreditsRoute = async ({
  fetchStatus = fetchManagedModelsStatus,
  fetchGatewayList = fetchGateways,
} = {}) => {
  let status;
  try {
    status = await fetchStatus();
  } catch {
    return CREDITS_ROUTE.NONE;
  }
  if (!creditsOnFromStatus(status)) return CREDITS_ROUTE.NONE;
  let gateways = null;
  try {
    gateways = await fetchGatewayList();
  } catch {
    gateways = null;
  }
  return creditsRouteFrom(status, gateways);
};

/**
 * The gateway list, in the AI Gateway pages' cache entry. `enabled: false`
 * skips the read.
 * @param {{ enabled?: boolean }} [options]
 */
export const useGatewayList = ({ enabled = true } = {}) =>
  useQuery({
    queryKey: GATEWAYS_KEY,
    queryFn: fetchGateways,
    enabled: enabled && Boolean(endpoints?.gateway?.list),
    staleTime: 30000,
    retry: false,
    meta: { errorHandled: true },
  });

/**
 * The credits path for this organization. `enabled: false` skips both reads
 * (and answers NONE), so a menu reads them only when it shows the sentence;
 * the gateway list is read only once credits are known to be on.
 * @param {{ enabled?: boolean }} [options]
 */
export const useFutureAgiCreditsRoute = ({ enabled = true } = {}) => {
  const { data: status } = useQuery({
    queryKey: MANAGED_MODELS_STATUS_KEY,
    queryFn: fetchManagedModelsStatus,
    enabled: enabled && Boolean(endpoints?.gateway?.managedModels),
    staleTime: 30000,
    retry: false,
    meta: { errorHandled: true },
  });
  const creditsOn = enabled && creditsOnFromStatus(status);
  const { data: gateways } = useQuery({
    queryKey: GATEWAYS_KEY,
    queryFn: fetchGateways,
    enabled: creditsOn && Boolean(endpoints?.gateway?.list),
    staleTime: 30000,
    retry: false,
    meta: { errorHandled: true },
  });
  return creditsOn ? creditsRouteFrom(status, gateways) : CREDITS_ROUTE.NONE;
};
