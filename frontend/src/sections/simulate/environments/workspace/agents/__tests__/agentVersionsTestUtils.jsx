import { useState } from "react";

// What each stubbed mutation answers: undefined (success with `data`),
// "pending", or { error }. `calls` keeps every request.
export const mutations = {};
export const resetMutations = () => {
  [
    "move",
    "discard",
    "retry",
    "change",
    "upload",
    "create",
    "answer",
    "stop",
  ].forEach((name) => {
    mutations[name] = { answer: undefined, data: undefined, calls: [] };
  });
};
resetMutations();

export function useFakeMutation(name, onAnswer) {
  const [status, setStatus] = useState({});
  return {
    isPending: Boolean(status.pending),
    isError: Boolean(status.error),
    isSuccess: Boolean(status.success),
    error: status.error || null,
    variables: status.variables,
    reset: () => {
      mutations[name].resets = (mutations[name].resets || 0) + 1;
      setStatus({});
    },
    mutate: (variables, { onSuccess, onError, onSettled } = {}) => {
      const mutation = mutations[name];
      mutation.calls.push(variables);
      if (mutation.answer === "pending") {
        setStatus({ pending: true, variables });
        return;
      }
      if (mutation.answer?.error) {
        setStatus({ error: mutation.answer.error, variables });
        onError?.(mutation.answer.error);
      } else {
        onAnswer?.(mutation);
        setStatus({ success: true, variables });
        onSuccess?.(mutation.data);
      }
      onSettled?.();
    },
  };
}

export const agentItem = (overrides) => ({
  status: "active",
  is_default: false,
  created_at: "2026-10-15T10:00:00Z",
  created_by: null,
  origin: null,
  summary: "",
  parent_id: null,
  preflight_status: null,
  preflight_report: null,
  credential_names: null,
  required_variable_names: null,
  activation_outcome: null,
  requested_here: false,
  discardable: false,
  // As the server answers it outside the default's lineage.
  pickable:
    Boolean(overrides?.is_default) || overrides?.preflight_status === "passed",
  ...overrides,
});
