import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isEqual } from "lodash";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

import { STORED_ONLY_CHECKS } from "../constants/storedOnlyChecks";
import {
  RULE_TO_CATALOG,
  listEntryAsCheck,
  normalizeGuardrails,
  ruleAsCheck,
} from "../guardrails/catalog/rule-model";

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

// Names of the guardrails in a guardrails patch that would add a credential in
// clear text. update-config stores the section as sent, unlike POST
// org-configs/, which moved credentials into an encrypted policy. A value the
// saved config already holds is not new, so it does not count.
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
// sync stores the section as a `checks` list, older editor saves as a checks
// map, and newer ones as `rules`; any of them can carry `_policy`.
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

// A stored-only check goes back exactly as it is saved: the editors show it
// read-only, so its draft can differ only by being turned off, and then only
// its `enabled` changes. Rebuilding it would add the defaults the editor
// fills in (a threshold, a stage, a mode) and drop keys the rule carries.
function keptAsStored(stored, draft) {
  return stored.enabled !== false && draft?.enabled === false
    ? { ...stored, enabled: false }
    : stored;
}

// The catalog name the editors key a stored rule or list entry by.
const catalogNameOf = (entry) =>
  isPlainObject(entry) && entry.name
    ? RULE_TO_CATALOG[entry.name] || entry.name
    : null;

// A rule or policy-sync list entry for a check the section does not hold
// yet: its original name, the stage and mode a console rule is saved with,
// its action and threshold only when it has them (the push runs a rule
// without them with block and 0.8), its provider in its config, where a rule
// reads it, and the credential policy a saved entry of that name carries.
function newEntry(catalogName, check, policies) {
  const name = ruleName(catalogName, check);
  const config = check.config || {};
  const threshold =
    check.confidence_threshold ?? check.confidenceThreshold ?? check.threshold;
  const policy = check._policy || policies.get(name);
  return {
    name,
    stage: check.stage || check.phase || "pre",
    mode: check.mode || "sync",
    ...(check.action ? { action: check.action } : {}),
    ...(isAbsent(threshold) ? {} : { threshold }),
    enabled: check.enabled !== false,
    config:
      check.provider && !config.provider
        ? { provider: check.provider, ...config }
        : config,
    ...(policy ? { _policy: policy } : {}),
  };
}

// A stored entry after the editors' draft of it. A check left exactly as the
// editors read it (`shown`) goes back as the stored entry, byte for byte.
// Otherwise only the fields that differ from what the editor opened on are
// written over the stored entry, never what storage would default: a null or
// absent enabled, action or threshold, a missing stage or mode, unknown keys
// and the entry's `_policy` survive an unrelated edit.
function editedEntry(stored, shown, check) {
  if (isEqual(shown, check)) return stored;
  const next = { ...stored };
  ["enabled", "action", "stage", "mode", "config"].forEach((key) => {
    if (!isEqual(check[key], shown[key])) next[key] = check[key];
  });
  const threshold =
    check.confidence_threshold ?? check.confidenceThreshold ?? check.threshold;
  if (!isEqual(threshold, shown.confidence_threshold)) {
    next.threshold = threshold;
  }
  if (check._policy) next._policy = check._policy;
  if (
    check.provider &&
    check.provider !== shown.provider &&
    !next.config?.provider
  ) {
    next.config = { provider: check.provider, ...next.config };
  }
  return next;
}

// A stored `rules` list or policy-sync `checks` list after the editors' draft
// (`checks`, keyed by catalog name). The editors and the config push read the
// last entry of a catalog name or its alias, so only that entry is edited and
// earlier duplicates stay in place. Removing a check removes every alias of
// it, or an older entry would take effect. A stored-only check keeps its
// stored entry (keptAsStored). Checks the section does not hold yet are
// appended.
function savedList(entries, checks, asCheck, saved) {
  const policies = savedPolicies(saved);
  const last = new Map();
  entries.forEach((entry, index) => {
    const name = catalogNameOf(entry);
    if (name) last.set(name, index);
  });
  const next = entries.flatMap((entry, index) => {
    const name = catalogNameOf(entry);
    if (!name) return [entry];
    if (!Object.prototype.hasOwnProperty.call(checks, name)) return [];
    if (last.get(name) !== index) return [entry];
    // A draft value that is not a check (none of the editors writes one)
    // edits nothing: the stored entry goes back as it is.
    if (!isPlainObject(checks[name])) return [entry];
    if (STORED_ONLY_CHECKS.has(entry.name)) {
      return [keptAsStored(entry, checks[name])];
    }
    return [editedEntry(entry, asCheck(entry), checks[name])];
  });
  Object.entries(checks).forEach(([name, check]) => {
    if (!last.has(name) && isPlainObject(check)) {
      next.push(newEntry(name, check, policies));
    }
  });
  return next;
}

// A saved section stored as a non-empty `checks` map, the shape older console
// saves left. The config push forwards such a map to the gateway as stored
// (agentcc/services/config_push.py), and the gateway runs a check stored
// without an action as log and one without a threshold at 0. A rule cannot
// leave either out: the push gives it block and 0.8.
export const isStoredChecksMap = (saved) =>
  isPlainObject(saved?.checks) && Object.keys(saved.checks).length > 0;

// An edited draft of a section stored as a checks map, saved as a map, so every
// check keeps what it does. A check the draft leaves as stored is saved as
// stored. An edited one is saved as it comes, with two exceptions. A check
// whose draft lost its `_policy` gets the saved one back, so the push can
// still swap its credentials in. And a check-level `provider` the editor adds
// when it switches on or creates a check that has a provider goes into the
// check's config: the gateway reads an external check's provider there, and
// the gateway contract takes no other key on a check than enabled, action,
// confidence_threshold and config, so update-config refuses a map whose check
// carries a provider of its own (agentcc/contracts/gateway_admin.py,
// GuardrailCheck). A provider the stored check already carries is left as it
// is. A rule gets its provider into its config the same way (newEntry).
function checksMapForSave(edited, saved) {
  if (!isPlainObject(edited.checks)) return edited;
  const checks = Object.fromEntries(
    Object.entries(edited.checks).map(([name, check]) => {
      const stored = saved.checks[name];
      if (!isPlainObject(check) || isEqual(check, stored)) return [name, check];
      let kept = check;
      if (!kept._policy && isPlainObject(stored) && stored._policy) {
        kept = { ...kept, _policy: stored._policy };
      }
      if ("provider" in kept && kept.provider !== stored?.provider) {
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

// The guardrails section to save from an editor's draft. An untouched draft
// (as stored, or as the editors read it) is the saved section, so it adds
// nothing to the patch. Every other save undoes only the editors'
// normalization, never the stored policy: a section stored as a checks map is
// saved as a map (checksMapForSave), a policy-sync `checks` list stays a list
// and `rules` stay rules (savedList), each untouched entry exactly as stored,
// with its absent or null defaults, unknown fields, credential policy and
// shadowed aliases. An empty stored `checks` carrier is kept (never dropped:
// a null directly under `guardrails` is refused by update-config's request
// serializer), and emptying a non-empty `checks` section empties `rules` too,
// so an older, shadowed rules list never takes effect again.
export function guardrailsForSave(edited, saved) {
  if (
    !edited ||
    isEqual(edited, saved) ||
    isEqual(edited, normalizeGuardrails(saved))
  ) {
    return saved;
  }
  if (!isPlainObject(edited.checks)) return edited;
  const { checks, ...rest } = edited;
  let next;
  if (isStoredChecksMap(saved)) {
    next = checksMapForSave(edited, saved);
  } else if (Array.isArray(saved?.checks) && saved.checks.length > 0) {
    next = {
      ...rest,
      checks: savedList(saved.checks, checks, listEntryAsCheck, saved),
    };
  } else {
    next = {
      ...rest,
      rules: savedList(
        Array.isArray(saved?.rules) ? saved.rules : [],
        checks,
        ruleAsCheck,
        saved,
      ),
    };
    if (Array.isArray(saved?.checks)) next.checks = [];
    else if (isPlainObject(saved?.checks)) next.checks = {};
    return next;
  }
  if (Object.keys(next.checks).length === 0 && saved?.rules?.length) {
    next.rules = [];
  }
  return next;
}

// A timed-out save carries its own actionable message (see asRequestError);
// anything else goes through the app's safe-message filter.
export function saveErrorMessage(err, fallback) {
  // The axios interceptor rejects with this typed API envelope directly.
  // Preserve the server's actionable refusal through the same length,
  // status and infrastructure-detail filters as every other validation error.
  if (err?.code === "guardrail_name_ambiguous") {
    return getSafeActionErrorMessage({ ...err, result: err.message }, fallback);
  }
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
    return `Future AGI credits are now ${warning.to ? "on" : "off"} for this organization.`;
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
// update-config rejects it. A new guardrail credential in clear text is
// refused here, failing closed, until update-config encrypts it the way
// POST org-configs/ did. The hook reports the outcome itself, so the app-wide
// error toast stays off.
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
          `New credentials for ${exposed.join(", ")} can't be saved from this page yet: they would be stored unencrypted. Nothing was saved.`,
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
