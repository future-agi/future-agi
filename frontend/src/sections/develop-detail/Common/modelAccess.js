// One-key adapter (ONEKEY-ASSUMED-CONTRACT-r1). The only module in the DSX
// frontend that knows where the managed-or-BYOK verdict lives: every
// model-list item carries credential_mode ('managed' | 'byok' | 'unavailable')
// and payer ('future_agi_credits' | 'own_provider_key' | null); is_available
// stays for compatibility. When the real contract names these differently, or
// moves the embedding entitlement, only this file changes.
//
// The verdict is the server's (GET models_list, scoped to the request's
// organization and workspace). The UI uses it to decide what to offer and
// whether to start a run; it never replaces the server's own checks.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

export const CREDENTIAL_MODE = Object.freeze({
  MANAGED: "managed",
  BYOK: "byok",
  UNAVAILABLE: "unavailable",
});

export const PAYER = Object.freeze({
  FUTURE_AGI_CREDITS: "future_agi_credits",
  OWN_PROVIDER_KEY: "own_provider_key",
});

export const PAYER_LABEL = Object.freeze({
  [PAYER.FUTURE_AGI_CREDITS]: "Future AGI credits",
  [PAYER.OWN_PROVIDER_KEY]: "Your provider key",
});

/**
 * @typedef {object} ModelAccess
 * @property {boolean} contract  the item has the one-key fields at all
 * @property {string|null} mode  a contract credential_mode, else null
 * @property {string|null} payer a contract payer, else null
 * @property {boolean|null} isAvailable
 * @property {string|null} provider
 * @property {string|null} modelName
 */

/**
 * The access verdict of one model-list item (snake_case from the server or
 * the camelCase aliases a picked option carries). An unrecognised
 * credential_mode or payer is never trusted: it reads as null.
 * @param {any} item
 * @returns {ModelAccess|null}
 */
export const readModelAccess = (item) => {
  if (!item || typeof item !== "object") return null;
  const contract =
    Object.hasOwn(item, "credential_mode") ||
    Object.hasOwn(item, "credentialMode");
  const available = item.is_available ?? item.isAvailable;
  return {
    contract,
    mode: readCredentialMode(item),
    payer: readPayer(item),
    isAvailable: typeof available === "boolean" ? available : null,
    provider: item.providers ?? item.provider ?? null,
    modelName: item.model_name ?? item.modelName ?? null,
  };
};

/**
 * The server's list item for one model: GET models_list?name=<model>, the
 * single-model read the picker hover already uses. Null when the server lists
 * no such model; throws on a malformed answer so callers fail safe.
 * @param {string} modelName
 * @returns {Promise<any|null>}
 */
export const fetchModelListEntry = async (modelName) => {
  const response = await axios.get(endpoints.develop.modelList, {
    params: { name: modelName },
  });
  const results = response?.data?.results;
  if (!Array.isArray(results)) {
    throw new Error("Unexpected model list response");
  }
  const wanted = String(modelName).toLowerCase();
  return (
    results.find(
      (item) =>
        String(item?.model_name ?? item?.modelName ?? "").toLowerCase() ===
        wanted,
    ) ?? null
  );
};

// Assumed embedding entitlement: the credential_mode of the model-list item
// for each embedding type's server default model (dynamic_columns.py uses
// these when no model is given). Sentence Transformers is not gated here, as
// before. At 651ab5bb the list drops embedding models, so these lookups find
// no item and the selector keeps the provider-key rule until the server lists
// them with credential_mode.
export const EMBEDDING_ACCESS_MODELS = Object.freeze({
  openai: "text-embedding-3-small",
  huggingface: "all-mpnet-base-v2",
});

/**
 * The raw list item (or null) per entitlement-gated embedding type. A failed
 * lookup is null: no verdict, never a managed grant.
 * @returns {Promise<Record<string, any|null>>}
 */
export const fetchEmbeddingAccess = async () => {
  const entries = await Promise.all(
    Object.entries(EMBEDDING_ACCESS_MODELS).map(async ([type, model]) => {
      try {
        return [type, await fetchModelListEntry(model)];
      } catch {
        return [type, null];
      }
    }),
  );
  return Object.fromEntries(entries);
};

// The organization and workspace the request runs as (the X-Workspace-Id the
// client sends), so a cached verdict is never reused across tenants.
const tenantScope = () => {
  try {
    return [
      sessionStorage.getItem(SS_KEY_ORG_ID) || null,
      sessionStorage.getItem(SS_KEY_WORKSPACE_ID) || null,
    ];
  } catch {
    return [null, null];
  }
};

export const embeddingAccessQueryKey = () => [
  "embedding-access",
  ...tenantScope(),
];

// The server's list item for one picked model, cached per tenant. It sits
// under the "model-list" key so saving or deleting a provider key (which
// invalidates "model-list") reads the verdict again. A failed lookup has no
// data: no verdict.
export const modelListEntryQueryKey = (modelName) => [
  "model-list",
  "entry",
  ...tenantScope(),
  modelName,
];

export const useModelListEntry = (modelName) =>
  useQuery({
    queryKey: modelListEntryQueryKey(modelName),
    queryFn: () => fetchModelListEntry(modelName),
    enabled: Boolean(modelName),
    staleTime: 60 * 1000,
    retry: false,
    meta: { errorHandled: true },
  });

export const useEmbeddingAccess = () =>
  useQuery({
    queryKey: embeddingAccessQueryKey(),
    queryFn: fetchEmbeddingAccess,
    staleTime: 60 * 1000,
    meta: { errorHandled: true },
  });

export const PAYER_LABELS = Object.freeze({
  [PAYER.FUTURE_AGI_CREDITS]: "Future AGI credits",
  [PAYER.OWN_PROVIDER_KEY]: "Your provider key",
});

// The payer each selectable mode implies. A payer that disagrees with the
// mode is not shown, rather than shown wrong.
const PAYER_FOR_MODE = Object.freeze({
  [CREDENTIAL_MODE.MANAGED]: PAYER.FUTURE_AGI_CREDITS,
  [CREDENTIAL_MODE.BYOK]: PAYER.OWN_PROVIDER_KEY,
});

const KNOWN_MODES = new Set(Object.values(CREDENTIAL_MODE));
const KNOWN_PAYERS = new Set(Object.values(PAYER));
export const CONTRACT_FIELDS = ["credential_mode", "credentialMode", "payer"];

// Rows arrive snake_case from the API, or camelCased by some readers.
export const readCredentialMode = (item) => {
  const raw = item?.credential_mode ?? item?.credentialMode;
  return KNOWN_MODES.has(raw) ? raw : null;
};

export const readPayer = (item) =>
  KNOWN_PAYERS.has(item?.payer) ? item.payer : null;

export const isManagedEligible = (item) =>
  getModelEligibility(item, false).mode === CREDENTIAL_MODE.MANAGED && getModelEligibility(item, false).selectable;

/**
 * What a model menu should do with one row.
 *
 * @param {object} item a model-list / model-options row
 * @param {*} legacyAvailable the surface's pre-contract verdict (shared
 *   picker: normalized isAvailable; evaluator picker: is_available !== false;
 *   eval config: apiKeyAvailable). It is used unchanged for byok rows and for
 *   rows without a recognised mode, so BYOK behaviour stays exactly as before.
 * @returns {{mode: (string|null), selectable: boolean,
 *   needsProviderSetup: boolean, payer: (string|null),
 *   payerLabel: (string|null)}}
 */
export const getModelEligibility = (item, legacyAvailable) => {
  const mode = readCredentialMode(item);
  const contract = Object.hasOwn(item ?? {}, "credential_mode") || Object.hasOwn(item ?? {}, "credentialMode");
  const available = item?.is_available ?? item?.isAvailable;
  let selectable;
  if (mode === CREDENTIAL_MODE.MANAGED) {
    // Funded by Future AGI credits: no provider key is ever needed.
    selectable = available !== false;
  } else if (mode === CREDENTIAL_MODE.UNAVAILABLE) {
    // Fail closed: the server says this model cannot run for the workspace.
    selectable = false;
  } else if (contract && mode === null) {
    selectable = false;
  } else {
    selectable = Boolean(legacyAvailable);
  }
  const reported = readPayer(item);
  const payer =
    selectable && mode && PAYER_FOR_MODE[mode] === reported ? reported : null;
  return {
    mode,
    selectable,
    needsProviderSetup: !selectable && mode !== CREDENTIAL_MODE.MANAGED && (!contract || mode === CREDENTIAL_MODE.BYOK || mode === CREDENTIAL_MODE.UNAVAILABLE),
    payer,
    payerLabel: payer ? PAYER_LABELS[payer] : null,
  };
};

// The contract fields a row carries, as they are, for a picked value that
// keeps the server's verdict. A row without them gives {}.
export const pickContractFields = (item) =>
  Object.fromEntries(
    CONTRACT_FIELDS.filter((field) => Object.hasOwn(item ?? {}, field)).map(
      (field) => [field, item[field]],
    ),
  );

// A shallow copy of a row without the contract fields, for spreading the rest
// of a row onto DOM elements.
export const omitContractFields = (item) => {
  const rest = { ...item };
  CONTRACT_FIELDS.forEach((field) => delete rest[field]);
  return rest;
};
