import { useMutation } from "@tanstack/react-query";

// MOCK — there is no env-level "enable tool call evaluation" endpoint yet. The
// backend's tool-evaluation flag lives on the run-test (run_test_evals'
// `enable_tool_evaluation`), a different scope, so the environment can't persist
// this toggle server-side today.
//
// This hook is the seam: the toggle drives client state now AND calls through
// here, so when an env-level endpoint lands, only `mockSetToolCallEval` swaps for
// the real call (e.g. PATCH /simulate/api/harness-environments/{id}/
// { tool_call_eval: enabled }) — the component and its optimistic update stay put.
export function mockSetToolCallEval(envId, enabled) {
  return Promise.resolve({ env_id: envId, tool_call_eval: enabled });
}

// Persist the env-level "enable tool call evaluation" flag. Mocked until the API
// exists — see mockSetToolCallEval.
export function useToolCallEval() {
  return useMutation({
    mutationFn: ({ envId, enabled }) => mockSetToolCallEval(envId, enabled),
  });
}
