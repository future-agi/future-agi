import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { MALFORMED_RESPONSE } from "src/sections/omega/shared/omegaClient";
import { OMEGA_STATE, OmegaError } from "src/sections/omega/shared/omegaStates";

import { useDeploymentsApi } from "../api/DeploymentsApiContext";
import {
  deploymentKeys,
  newIdempotencyKey,
  retryIdempotent,
} from "../api/queries";
import { activeNames, canManageSecretsFor } from "../lib/secretForm";

/**
 * Vault writes for the Deployments tab. An Idempotency-Key belongs to one
 * exact request: the same entries sent again (a double click, a retry, or
 * Save again after a lost answer) carry the same key, so EE applies the write
 * once; edited entries are a new request with a new key (EE would refuse the
 * old key with a 409). `firstSend` runs once per new request, before it is
 * sent, and may refuse it; the key is chosen before that check starts, so
 * two sends of one request that overlap it still share one key. The request
 * (it holds the value) is kept only to compare with the next save and is
 * dropped on success and on `reset`, when the dialog's own form drops it.
 * Success re-reads the workspace's credential list. Values are never cached
 * or logged.
 */
function useVaultWrite(kind, run, firstSend) {
  const api = useDeploymentsApi();
  const { currentWorkspaceId } = useWorkspace();
  const scope = currentWorkspaceId ?? "none";
  const queryClient = useQueryClient();
  const intent = useRef(null);
  const mutation = useMutation({
    mutationFn: async (variables) => {
      const request = JSON.stringify(variables);
      if (intent.current?.request !== request) {
        intent.current = {
          request,
          key: newIdempotencyKey(kind),
          checked: firstSend?.({ api, queryClient, scope, variables }),
        };
      }
      const current = intent.current;
      try {
        await current.checked;
      } catch (error) {
        // A refusal sends nothing and keeps no key: the next save checks again.
        if (intent.current === current) intent.current = null;
        throw error;
      }
      return run(api, variables, { idempotencyKey: current.key });
    },
    retry: retryIdempotent,
    retryDelay: 400,
    // The dialog shows its own error; and a finished write (whose variables
    // hold the value) leaves the mutation cache as soon as nothing observes it.
    meta: { errorHandled: true },
    gcTime: 0,
    onSuccess: async () => {
      intent.current = null;
      await queryClient.invalidateQueries({
        queryKey: deploymentKeys.credentials(scope),
      });
    },
  });
  const reset = () => {
    intent.current = null;
    mutation.reset();
  };
  return { ...mutation, reset };
}

/**
 * EE does not refuse a second active secret with the same name, and the
 * form's check reads a list that may be seconds old (another tab, another
 * admin). Read the vault once more just before a new add; a name now taken is
 * refused here and the fresh list is shown. A malformed reply is shown in the
 * dialog rather than silently accepted. Other read failures still let the add
 * proceed: this narrows the race to one round trip, only EE can close it.
 */
async function refuseTakenName({ api, queryClient, scope, variables }) {
  let fresh;
  try {
    fresh = await api.credentials();
  } catch (error) {
    if (error?.code === MALFORMED_RESPONSE) throw error;
    return;
  }
  queryClient.setQueryData(deploymentKeys.credentials(scope), fresh);
  if (activeNames(fresh).has(variables.name)) {
    throw new OmegaError({
      state: OMEGA_STATE.ERROR,
      status: 409,
      code: "name_taken",
      message: `${variables.name} is in the vault now (the list is refreshed). Replace its value instead.`,
    });
  }
}

export const useAddSecret = () =>
  useVaultWrite(
    "secret-add",
    (api, { name, kind, value }, keys) =>
      api.createCredential({ name, kind, secret: value }, keys),
    refuseTakenName,
  );

export const useRotateSecret = () =>
  useVaultWrite("secret-rotate", (api, { id, value }, keys) =>
    api.rotateCredential(id, { secret: value }, keys),
  );

export const useRevokeSecret = () =>
  useVaultWrite("secret-revoke", (api, { id }, keys) =>
    api.revokeCredential(id, keys),
  );

/** Whether this person may add, replace or delete secrets (EE checks again). */
export function useCanManageSecrets() {
  const { role } = useSafeAuthContext();
  return canManageSecretsFor(role);
}
