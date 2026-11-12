// Run Prompt execution eligibility (one key, R1-02). Before create, edit/run
// or preview starts a run, the picked model must be executable either by
// Future AGI credits (managed) or by the workspace's own provider key (BYOK),
// per the server's model-list verdict (Common/modelAccess.js). A server
// without the one-key fields keeps the provider-key rule. When eligibility is
// unknown (lookup failed, model not listed, unexpected value) no run starts.
// This is the client's early answer; the server still enforces its own.
import { useCallback, useRef } from "react";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  CREDENTIAL_MODE,
  fetchModelListEntry,
  getModelEligibility,
  readModelAccess,
} from "../Common/modelAccess";
import {
  CREDITS_ROUTE,
  fetchGateways,
  fetchManagedModelsStatus,
  noUsableModelSentence,
  readFutureAgiCreditsRoute,
} from "src/components/custom-model-dropdown/noUsableModel";
import { knownProviderDisplayName } from "src/utils/providerDisplayName";

export const ELIGIBILITY = Object.freeze({
  ELIGIBLE: "eligible",
  INELIGIBLE: "ineligible",
  UNKNOWN: "unknown",
});

/**
 * A listed provider's display name (src/utils/providerDisplayName.js), as its
 * key card shows it; any other provider id is shown as given.
 * @param {string|null|undefined} provider
 */
export const providerLabel = (provider) => {
  const id = String(provider ?? "").trim();
  return knownProviderDisplayName(id) ?? (id || "provider");
};

/**
 * @typedef {object} EligibilityVerdict
 * @property {string} status       one of ELIGIBILITY
 * @property {string|null} mode    'managed' | 'byok' when eligible
 * @property {string|null} payer   the contract payer, when known
 * @property {string|null} message what the user is told when refused
 */

const verdict = (status, { mode = null, payer = null, message = null } = {}) => ({
  status,
  mode,
  payer,
  message,
});

const unknown = (name, next) =>
  verdict(ELIGIBILITY.UNKNOWN, {
    message: `Couldn't confirm access to ${name}. ${next}`,
  });

/**
 * @param {{ modelName?: string, provider?: string, entry?: any, lookupFailed?: boolean, creditsRoute?: string }} input
 *   creditsRoute: the credits path a refusal also names, as the model menus'
 *   sentence does (CREDITS_ROUTE; NONE when credits are not on).
 * @returns {EligibilityVerdict}
 */
export const resolveExecutionEligibility = ({
  modelName,
  provider,
  entry,
  lookupFailed = false,
  creditsRoute = CREDITS_ROUTE.NONE,
}) => {
  const name = typeof modelName === "string" ? modelName.trim() : "";
  if (!name) {
    return verdict(ELIGIBILITY.UNKNOWN, {
      message: "Select a model before running this prompt.",
    });
  }
  if (lookupFailed) {
    return unknown(name, "Check your connection and try again.");
  }
  const access = readModelAccess(entry);
  if (!access) {
    return unknown(name, "Pick the model again or choose another one.");
  }
  const label = providerLabel(access.provider ?? provider);

  if (access.contract) {
    if (
      (access.mode === CREDENTIAL_MODE.MANAGED || access.mode === CREDENTIAL_MODE.BYOK) &&
      getModelEligibility(entry, access.isAvailable === true).selectable
    ) {
      return verdict(ELIGIBILITY.ELIGIBLE, {
        mode: access.mode,
        payer: access.payer,
      });
    }
    if (access.mode === CREDENTIAL_MODE.UNAVAILABLE || access.mode === CREDENTIAL_MODE.BYOK) {
      return verdict(ELIGIBILITY.INELIGIBLE, {
        message: `${name} isn't available in this workspace. ${noUsableModelSentence(creditsRoute)}`,
      });
    }
    return unknown(name, "Try again in a moment.");
  }

  // A server without the one-key fields: availability is provider-key
  // membership, with the message Run Prompt always showed.
  if (access.isAvailable === true) {
    return verdict(ELIGIBILITY.ELIGIBLE, { mode: CREDENTIAL_MODE.BYOK });
  }
  if (access.isAvailable === false) {
    return verdict(ELIGIBILITY.INELIGIBLE, {
      message: `Please configure ${label} API key`,
    });
  }
  return unknown(name, "Try again in a moment.");
};

/**
 * Looks the model up now (never a cached answer) and resolves its verdict.
 * A refused model with the one-key fields also reads whether Future AGI
 * credits are on and which gateway path serves them, so the message can name
 * it; a failed credits read leaves it out.
 * @param {{ modelName?: string, provider?: string, fetchEntry?: (name: string) => Promise<any>, fetchCreditsStatus?: () => Promise<any>, fetchGatewayList?: () => Promise<any> }} input
 * @returns {Promise<EligibilityVerdict>}
 */
export const checkExecutionEligibility = async ({
  modelName,
  provider,
  fetchEntry = fetchModelListEntry,
  fetchCreditsStatus = fetchManagedModelsStatus,
  fetchGatewayList = fetchGateways,
}) => {
  const name = typeof modelName === "string" ? modelName.trim() : "";
  if (!name) return resolveExecutionEligibility({ modelName: name, provider });
  let entry;
  try {
    entry = await fetchEntry(name);
  } catch {
    // The reason stays out of the UI: never echo a server or provider error.
    return resolveExecutionEligibility({
      modelName: name,
      provider,
      lookupFailed: true,
    });
  }
  const result = resolveExecutionEligibility({
    modelName: name,
    provider,
    entry,
  });
  if (
    result.status !== ELIGIBILITY.INELIGIBLE ||
    !readModelAccess(entry)?.contract
  ) {
    return result;
  }
  return resolveExecutionEligibility({
    modelName: name,
    provider,
    entry,
    creditsRoute: await readFutureAgiCreditsRoute({
      fetchStatus: fetchCreditsStatus,
      fetchGatewayList,
    }),
  });
};

/**
 * The model and provider of a body Run Prompt is about to send (create,
 * edit/run and preview all carry them in config).
 * @param {any} submitObject
 * @returns {{ modelName: string|undefined, provider: string|undefined }}
 */
export const submittedModelOf = (submitObject) => {
  const config = submitObject?.config ?? {};
  const runConfig = config.run_prompt_config ?? {};
  return {
    modelName: config.model,
    provider: runConfig.providers ?? runConfig.provider ?? undefined,
  };
};

/**
 * Returns `passesExecutionGate(submitObject) -> Promise<boolean>`. A refusal
 * tells the user why and starts nothing. While one check is in flight another
 * submit is refused, so a double click cannot start two runs. The body is
 * only read, never changed.
 * @param {{ fetchEntry?: (name: string) => Promise<any>, fetchCreditsStatus?: () => Promise<any>, fetchGatewayList?: () => Promise<any>, notify?: (message: string, options: { variant: string }) => any }} [options]
 */
export const useExecutionEligibilityGate = ({
  fetchEntry = fetchModelListEntry,
  fetchCreditsStatus = fetchManagedModelsStatus,
  fetchGatewayList = fetchGateways,
  notify = enqueueSnackbar,
} = {}) => {
  const inFlight = useRef(false);
  return useCallback(
    async (submitObject) => {
      if (inFlight.current) {
        notify("Checking model access. Please wait.", { variant: "info" });
        return false;
      }
      inFlight.current = true;
      try {
        const result = await checkExecutionEligibility({
          ...submittedModelOf(submitObject),
          fetchEntry,
          fetchCreditsStatus,
          fetchGatewayList,
        });
        if (result.status === ELIGIBILITY.ELIGIBLE) return true;
        notify(result.message, {
          variant:
            result.status === ELIGIBILITY.INELIGIBLE ? "error" : "warning",
        });
        return false;
      } finally {
        inFlight.current = false;
      }
    },
    [fetchEntry, fetchCreditsStatus, fetchGatewayList, notify],
  );
};
