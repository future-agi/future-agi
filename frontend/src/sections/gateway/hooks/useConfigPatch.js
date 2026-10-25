import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isEqual } from "lodash";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

import {
  asRequestError,
  refreshGatewayConfig,
} from "../providers/hooks/useGatewayConfig";

const REQUEST_CONFIG = { timeout: 30000 };

export const ORG_CONFIG_QUERY_KEY = "agentcc-org-config";
export const ORG_CONFIG_HISTORY_QUERY_KEY = "agentcc-org-config-history";

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const isAbsent = (value) => value === undefined || value === null;

// An RFC 7386 merge patch that turns `before` into `after`, or undefined when
// they are equal. update-config deep-merges each section and keeps keys the
// patch leaves out, so a removed key has to be sent as null to be deleted.
// Arrays are replaced whole.
export function mergePatch(before, after) {
  if (!isPlainObject(before) || !isPlainObject(after)) {
    return isEqual(before, after) ? undefined : after;
  }
  const patch = {};
  Object.keys(before).forEach((key) => {
    if (!isAbsent(before[key]) && isAbsent(after[key])) patch[key] = null;
  });
  Object.entries(after).forEach(([key, value]) => {
    if (isAbsent(value)) return;
    const change = isAbsent(before[key])
      ? value
      : mergePatch(before[key], value);
    if (change !== undefined) patch[key] = change;
  });
  return Object.keys(patch).length ? patch : undefined;
}

// The update-config body for the listed sections: only the sections that
// changed, each as a merge patch.
export function buildConfigPatch(before, after, sections) {
  const patch = {};
  sections.forEach((section) => {
    const change = mergePatch(before?.[section] || {}, after?.[section] || {});
    if (change !== undefined) patch[section] = change;
  });
  return patch;
}

// The guardrail config keys the backend encrypts, and the placeholder it
// stores in their place (agentcc/serializers/guardrail_policy.py).
const GUARDRAIL_SECRET_KEYS = [
  "api_key",
  "secret_key",
  "access_key",
  "password",
];
const ENCRYPTED_PLACEHOLDER = "__encrypted__";

function guardrailEntries(guardrails) {
  const { rules, checks } = guardrails || {};
  return [
    ...(Array.isArray(rules) ? rules.map((rule) => [rule?.name, rule]) : []),
    ...(Array.isArray(checks)
      ? checks.map((check) => [check?.name, check])
      : Object.entries(isPlainObject(checks) ? checks : {})),
  ];
}

// The clear-text credentials in one guardrail's config, as "key=value" ids.
function plaintextSecrets(check) {
  const config = check?.config;
  if (!isPlainObject(config)) return [];
  return GUARDRAIL_SECRET_KEYS.filter(
    (key) => config[key] && config[key] !== ENCRYPTED_PLACEHOLDER,
  ).map((key) => `${key}=${JSON.stringify(config[key])}`);
}

// Names of the guardrails in a guardrails patch that carry a new credential
// value (anything but the "__encrypted__" placeholder). A value the saved
// config already holds is not new, so it does not count.
export function guardrailsWithPlaintextSecrets(guardrails, savedGuardrails) {
  const stored = new Set(
    guardrailEntries(savedGuardrails).flatMap(([, check]) =>
      plaintextSecrets(check),
    ),
  );
  return guardrailEntries(guardrails)
    .filter(([, check]) =>
      plaintextSecrets(check).some((secret) => !stored.has(secret)),
    )
    .map(([name]) => name || "a guardrail");
}

// Catalog check names whose org-config rule name differs.
const CATALOG_TO_RULE = {
  "pii-detection": "pii-detector",
  "secret-detection": "secrets-detector",
  "prompt-injection": "injection-detector",
};

// The org-config rule name of an entry in a checks map keyed by catalog name.
function ruleName(catalogName, check) {
  return check?._originalName || CATALOG_TO_RULE[catalogName] || catalogName;
}

// Each saved guardrail's credential policy, by rule name. The guardrail policy
// sync stores the section as a `checks` list; a section can also be stored as
// a checks map or as `rules`; any of them can carry `_policy`.
function savedPolicies(saved) {
  const { rules, checks } = saved || {};
  const entries = [
    ...(Array.isArray(checks)
      ? checks.map((check) => [check?.name, check])
      : Object.entries(isPlainObject(checks) ? checks : {}).map(
          ([catalogName, check]) => [ruleName(catalogName, check), check],
        )),
    ...(Array.isArray(rules) ? rules.map((rule) => [rule?.name, rule]) : []),
  ];
  return new Map(
    entries
      .filter(([name, entry]) => name && entry?._policy)
      .map(([name, entry]) => [name, entry._policy]),
  );
}

// The guardrail editors work on a `checks` map keyed by catalog name; the org
// config stores a `rules` list. Converts back, keeping each rule's original
// name, its action and threshold (a synced check keeps the threshold as
// `threshold`), the check's provider (which a rule reads from its config), and
// its `_policy`: the policy holding its encrypted credentials, which the
// config push needs to swap the "__encrypted__" placeholders back in. A check
// that carries no action or threshold gets none: the push runs a rule without
// them with block and 0.8, as it runs a check of the policy sync's list
// without them.
function checksToRules(guardrails, saved) {
  const { checks, ...rest } = guardrails;
  if (!isPlainObject(checks)) return guardrails;

  const policies = savedPolicies(saved);
  const rules = Object.entries(checks).map(([catalogName, cfg]) => {
    const name = ruleName(catalogName, cfg);
    const policy = cfg._policy || policies.get(name);
    const config = cfg.config || {};
    const threshold =
      cfg.confidence_threshold ?? cfg.confidenceThreshold ?? cfg.threshold;
    return {
      name,
      stage: cfg.stage || cfg.phase || "pre",
      mode: cfg.mode || "sync",
      ...(cfg.action ? { action: cfg.action } : {}),
      ...(isAbsent(threshold) ? {} : { threshold }),
      enabled: cfg.enabled !== false,
      config:
        cfg.provider && !config.provider
          ? { provider: cfg.provider, ...config }
          : config,
      ...(policy ? { _policy: policy } : {}),
    };
  });

  return { ...rest, rules };
}

// A saved section stored as a non-empty `checks` map. The config push
// forwards such a map to the gateway as stored
// (agentcc/services/config_push.py), and the gateway runs a check stored
// without an action as log and one without a threshold at 0. A rule cannot
// leave either out: the push gives it block and 0.8.
export const isStoredChecksMap = (saved) =>
  isPlainObject(saved?.checks) && Object.keys(saved.checks).length > 0;

// An edited draft of a section stored as a checks map, saved as a map, so every
// check keeps what it does. The rules editor edits that map as stored, and a
// saved check comes back from its dialog as stored apart from the fields the
// admin edited. Each check is saved as it comes, with two exceptions. A check
// whose draft lost its `_policy` gets the saved one back, so the push can
// still swap its credentials in. And a check-level `provider`, which the
// editor adds when it switches on or creates a check that has a provider,
// goes into the check's config: the gateway reads an external check's
// provider there, and the gateway contract takes no other key on a check
// than enabled, action, confidence_threshold and config, so update-config
// refuses a map whose check carries a provider of its own (agentcc/contracts/
// gateway_admin.py, GuardrailCheck). A rule gets its provider into its config
// the same way (checksToRules).
function checksMapForSave(edited, saved) {
  if (!isPlainObject(edited.checks)) return edited;
  const checks = Object.fromEntries(
    Object.entries(edited.checks).map(([name, check]) => {
      if (!isPlainObject(check)) return [name, check];
      let kept = check;
      const stored = saved.checks[name];
      if (!kept._policy && isPlainObject(stored) && stored._policy) {
        kept = { ...kept, _policy: stored._policy };
      }
      if ("provider" in kept) {
        const { provider, ...rest } = kept;
        const config = isPlainObject(rest.config) ? rest.config : {};
        kept =
          provider && !config.provider
            ? { ...rest, config: { provider, ...config } }
            : rest;
      }
      return [name, kept];
    }),
  );
  return { ...edited, checks };
}

// The guardrails section to save from an editor's draft. An untouched draft is
// the saved section, so it adds nothing to the patch. A section stored as a
// checks map is saved as a map (checksMapForSave), so the patch carries only
// what the admin changed. Any other edited section goes back to the stored
// rules shape. A saved `checks` list (or an empty map) is emptied rather than
// dropped: a non-empty `checks` left in place would win over `rules` in the
// config push. The push and both editors read an empty `checks` as absent.
export function guardrailsForSave(edited, saved) {
  if (!edited || isEqual(edited, saved)) return saved;
  if (isStoredChecksMap(saved)) return checksMapForSave(edited, saved);
  const converted = checksToRules(edited, saved);
  if (converted === edited) return edited;
  if (Array.isArray(saved?.checks)) return { ...converted, checks: [] };
  if (isPlainObject(saved?.checks)) return { ...converted, checks: {} };
  return converted;
}

// A timed-out save carries its own actionable message (see asRequestError);
// anything else goes through the app's safe-message filter.
export function saveErrorMessage(err, fallback) {
  return err?.cause ? err.message : getSafeActionErrorMessage(err, fallback);
}

export function postConfigPatch(gatewayId, config, action) {
  return axios
    .post(endpoints.gateway.updateConfig(gatewayId), config, REQUEST_CONFIG)
    .then(({ data }) => data.result)
    .catch((err) => {
      throw asRequestError(err, action);
    });
}

// Re-reads everything a config write stales. Returned from onSuccess so the
// mutation settles only after the gateway config has been re-read.
export function refreshAfterConfigSave(queryClient, ...alsoStaleKeys) {
  return refreshGatewayConfig(
    queryClient,
    [ORG_CONFIG_QUERY_KEY],
    [ORG_CONFIG_HISTORY_QUERY_KEY],
    ...alsoStaleKeys,
  );
}

// A config write's `warnings[]` entry as a sentence. The server sends either
// a message or a code, e.g. {code: "managed_models_change", from, to}.
export function configWarningMessage(warning) {
  if (typeof warning === "string") return warning;
  if (warning?.message) return warning.message;
  if (warning?.code === "managed_models_change") {
    return `Managed models are now ${warning.to ? "on" : "off"} for this organization.`;
  }
  return warning?.code || "The config change came back with a warning.";
}

export function reportConfigWarnings(warnings) {
  (Array.isArray(warnings) ? warnings : []).forEach((warning) =>
    enqueueSnackbar(configWarningMessage(warning), { variant: "warning" }),
  );
}

function reportSaved(result, successMessage) {
  if (result?.gateway_synced === false) {
    enqueueSnackbar(result.gateway_warning || "Config saved, but not synced", {
      variant: "warning",
    });
  } else {
    enqueueSnackbar(successMessage, { variant: "success" });
  }
  reportConfigWarnings(result?.warnings);
}

// Saves a patch built with buildConfigPatch through update-config; `saved` is
// the config the patch was taken against. An empty patch is not sent:
// update-config rejects it. A guardrails patch that carries a new credential
// value is not sent from this page. The hook reports the outcome itself, so
// the app-wide error toast stays off.
export function useConfigPatch({
  action = "Saving config",
  successMessage = "Config saved",
} = {}) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, patch, saved }) => {
      if (!Object.keys(patch || {}).length) return null;
      if (!gatewayId) {
        // `cause` marks a message written here, which saveErrorMessage shows.
        throw new Error("No gateway is connected yet, so nothing was saved.", {
          cause: "missing_gateway",
        });
      }
      const exposed = guardrailsWithPlaintextSecrets(
        patch.guardrails,
        saved?.guardrails,
      );
      if (exposed.length) {
        throw new Error(
          `New credentials for ${exposed.join(", ")} are not saved from this page. Nothing was saved.`,
          { cause: "guardrail_secret_unencrypted" },
        );
      }
      return postConfigPatch(gatewayId, patch, action);
    },
    onSuccess: (result) => {
      if (!result) {
        enqueueSnackbar("No changes to save", { variant: "info" });
        return undefined;
      }
      reportSaved(result, successMessage);
      return refreshAfterConfigSave(queryClient);
    },
    onError: (err) =>
      enqueueSnackbar(saveErrorMessage(err, "Failed to save config"), {
        variant: "error",
      }),
    meta: { errorHandled: true },
  });
}
