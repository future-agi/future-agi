import { useCallback } from "react";
import { STORED_ONLY_CHECKS } from "../../constants/storedOnlyChecks";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import axios, { endpoints } from "src/utils/axios";

import {
  buildConfigPatch,
  guardrailsForSave,
  guardrailsWithPlaintextSecrets,
  isStoredChecksMap,
  postConfigPatch,
  refreshAfterConfigSave,
  reportConfigWarnings,
  saveErrorMessage,
} from "../../hooks/useConfigPatch";
import {
  SavedButNotAppliedError,
  asRequestError,
  isSavedButNotApplied,
} from "../../providers/hooks/useGatewayConfig";
import {
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
} from "../catalog/rule-model";

const REQUEST_CONFIG = { timeout: 30000 };

// What every guardrail write reports: a warning when Django saved the config
// but could not push it to the gateway (`gateway_synced: false`), in the
// words every gateway config write uses ("Saved, but the gateway did not
// apply it: <gateway_warning>"), a success only when the gateway has it, and
// each warning the write came back with. A save that changed nothing sent
// nothing, and says so.
function report(result, success) {
  if (result === null) {
    enqueueSnackbar("No changes to save", { variant: "info" });
    return;
  }
  if (result?.gateway_synced === false) {
    enqueueSnackbar(new SavedButNotAppliedError(result).message, {
      variant: "warning",
    });
  } else {
    enqueueSnackbar(success, { variant: "success" });
  }
  reportConfigWarnings(result?.warnings);
}

const postGateway = (url, body, action) =>
  axios
    .post(url, body, REQUEST_CONFIG)
    .then(({ data }) => data?.result)
    .catch((err) => {
      throw asRequestError(err, action);
    });

/**
 * The writes the Guardrails page makes. A rule is switched with
 * toggle-guardrail and edited with update-guardrail, whatever shape the
 * section is stored in: both edit a `rules` list as rules, a checks map in
 * the map and a checks list in the policy that wrote it, and update-guardrail
 * encrypts a new credential in each. A check of a checks map is sent only the
 * keys the form changed, so nothing else it does changes. Checks-list removal
 * uses DELETE update-guardrail to remove the policy sources, not just their
 * projection. Rules/map removal and the timeout still use update-config.
 */
export function useRuleActions({ gatewayId, saved }) {
  const queryClient = useQueryClient();
  const write = useMutation({
    mutationFn: (fn) => {
      if (!gatewayId) {
        throw new Error("No gateway is connected yet, so nothing was saved.", {
          cause: "missing_gateway",
        });
      }
      return fn();
    },
    onSuccess: (result) =>
      result === null ? undefined : refreshAfterConfigSave(queryClient),
    meta: { errorHandled: true },
  });

  // `rejectUnapplied`: an edit the gateway did not apply rejects with
  // SavedButNotAppliedError after its one warning, so the drawer that made it
  // stays open and shows why; it is never reported as an error.
  const run = useCallback(
    (fn, success, fallback, { rejectUnapplied = false } = {}) =>
      write
        .mutateAsync(fn)
        .then((result) => {
          report(result, success);
          if (rejectUnapplied && result?.gateway_synced === false) {
            throw new SavedButNotAppliedError(result);
          }
          return result;
        })
        .catch((err) => {
          if (!isSavedButNotApplied(err)) {
            enqueueSnackbar(saveErrorMessage(err, fallback), {
              variant: "error",
            });
          }
          throw err;
        }),
    [write],
  );

  // The whole section, edited as a checks map, saved through update-config.
  const saveSection = useCallback(
    (edit, action) => {
      const normalized = normalizeGuardrails(saved || {});
      const edited = { ...normalized, checks: edit({ ...normalized.checks }) };
      const toSave = guardrailsForSave(edited, saved || {});
      const patch = buildConfigPatch(
        { guardrails: saved || {} },
        { guardrails: toSave },
        ["guardrails"],
      );
      const exposed = guardrailsWithPlaintextSecrets(patch.guardrails, saved);
      if (exposed.length) {
        throw new Error(
          `New credentials for ${exposed.join(", ")} can't be saved this way yet: they would be stored unencrypted. Nothing was saved.`,
          { cause: "guardrail_secret_unencrypted" },
        );
      }
      return postConfigPatch(gatewayId, patch, action);
    },
    [gatewayId, saved],
  );

  const saveRule = useCallback(
    ({ catalogName, meta, check, form, label = undefined }) => {
      if (STORED_ONLY_CHECKS.has(catalogName))
        return Promise.reject(new Error("This saved check is read-only."));
      const name = label || meta?.label || catalogName;
      const body = isStoredChecksMap(saved)
        ? mapEditForSave({ check, meta, catalogName, form })
        : ruleBodyForSave({ saved, check, meta, catalogName, form });
      const fn = () =>
        body === null
          ? null
          : postGateway(
              endpoints.gateway.updateGuardrail(gatewayId),
              body,
              `Saving ${name}`,
            );
      return run(fn, `${name} saved`, `Could not save ${name}`, {
        rejectUnapplied: true,
      });
    },
    [gatewayId, run, saved],
  );

  const toggleRule = useCallback(
    (row, enabled) => {
      if (enabled && STORED_ONLY_CHECKS.has(row.id || row.ruleName))
        return Promise.reject(
          new Error("This saved check cannot be enabled here."),
        );
      const verb = enabled ? "on" : "off";
      return run(
        () =>
          postGateway(
            endpoints.gateway.toggleGuardrail(gatewayId),
            { name: row.ruleName, enabled },
            `Turning ${row.label} ${verb}`,
          ),
        `${row.label} turned ${verb}`,
        `Could not turn ${row.label} ${verb}`,
      );
    },
    [gatewayId, run],
  );

  const removeRule = useCallback(
    (row) =>
      run(
        () =>
          Array.isArray(saved?.checks)
            ? axios
                .delete(endpoints.gateway.updateGuardrail(gatewayId), {
                  ...REQUEST_CONFIG,
                  data: { name: row.ruleName },
                })
                .then(({ data }) => data?.result)
                .catch((err) => {
                  throw asRequestError(err, `Removing ${row.label}`);
                })
            : saveSection((checks) => {
                const next = { ...checks };
                delete next[row.id];
                return next;
              }, `Removing ${row.label}`),
        `${row.label} removed`,
        `Could not remove ${row.label}`,
        { rejectUnapplied: true },
      ),
    [gatewayId, run, saved, saveSection],
  );

  const saveTimeout = useCallback(
    (timeoutMs) =>
      run(
        () =>
          postConfigPatch(
            gatewayId,
            { guardrails: { timeout_ms: timeoutMs } },
            "Saving the check timeout",
          ),
        "Check timeout saved",
        "Could not save the check timeout",
      ),
    [gatewayId, run],
  );

  return {
    saveRule,
    toggleRule,
    removeRule,
    saveTimeout,
    isSaving: write.isPending,
  };
}
