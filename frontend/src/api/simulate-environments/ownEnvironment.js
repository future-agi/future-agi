import { createContext, useCallback, useContext, useRef } from "react";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessIdempotencyKey } from "src/api/harness/harness";

// A shared template is read by everyone and changed by nobody. Every write in the
// workspace resolves its environment id through this context first: on your own
// environment that is the id itself; on an open template it is your copy of it,
// made by the first write that asks.
export const copyHarnessEnvironment = async (id, idempotencyKey) =>
  (
    await axios.post(
      apiPath("/simulate/api/harness-environments/{id}/copy/", { id }),
      {},
      { headers: { "Idempotency-Key": idempotencyKey } },
    )
  ).data;

const identity = async (id) => id;

export const OwnEnvironmentContext = createContext(identity);

export const useOwnEnvironmentId = () => useContext(OwnEnvironmentContext);

// The resolver the workspace provides. Concurrent writes share one copy request,
// and a failed copy keeps its key, so retrying cannot make a second copy.
export function useTemplateCopy(templateId, sharedTemplate, onCopied) {
  const pending = useRef(null);
  return useCallback(
    (id) => {
      if (!sharedTemplate || id !== templateId) return identity(id);
      if (pending.current?.templateId !== templateId) {
        pending.current = { templateId, key: harnessIdempotencyKey() };
      }
      const copy = pending.current;
      copy.promise ??= copyHarnessEnvironment(templateId, copy.key).then(
        ({ environment_id: copyId }) => {
          onCopied(copyId);
          return copyId;
        },
        (error) => {
          copy.promise = null;
          throw error;
        },
      );
      return copy.promise;
    },
    [templateId, sharedTemplate, onCopied],
  );
}
