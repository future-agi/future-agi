import { create } from "zustand";

// Single slice that holds the in-flight Observe feedback target. Mirrors the
// shape of useAddDevelopEvalFeedbackStore on the Develop side (one nullable
// target field + one setter), so the wrapper code reads symmetrically.
//
// Target shape — every field uses BE snake_case keys directly (no FE
// normalization). All snake_case on purpose; matches the
// `internal-docs/api-contracts/management-api-contracts/README.md` rule:
// "Use canonical snake_case only at the API boundary."
//
//   {
//     target_type: "span" | "trace" | "session",
//     observation_span_id?: string,   // present when target_type !== "session"
//     trace_id?: string,              // present when target_type === "trace"
//     trace_session_id?: string,      // present when target_type === "session"
//     custom_eval_config_id: string,
//     name: string,                   // eval display name shown at top of drawer
//     output_type: string,            // "reason" | "score" | "Pass/Fail" | "choices"
//     value_infos?: { reason?: string },  // existing verdict text the drawer renders
//     eval_task_id?: string,          // gates the third radio (RETUNE_RECALCULATE)
//     has_error?: boolean,            // dispatcher-side error gate (chip is disabled)
//     error_message?: string,
//   }
const useAddObserveEvalFeedbackStore = create((set) => ({
  addObserveEvalFeedbackTarget: null,
  setAddObserveEvalFeedbackTarget: (value) =>
    set(() => ({ addObserveEvalFeedbackTarget: value })),
}));

export default useAddObserveEvalFeedbackStore;
