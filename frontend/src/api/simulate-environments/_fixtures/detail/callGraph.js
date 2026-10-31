// Vel's call-graph sample: one call's trajectory, the step it skipped and the
// branch it didn't take. Shown only with ?uiSample=graph; the API has no call
// graph yet.
export const SAMPLE_CALL_GRAPH = {
  spine: [
    {
      id: "start",
      label: "start",
      kind: "start",
      sub: "9 turns · 74s of speech",
      at: 0,
      status: "ok",
    },
    {
      id: "lookup_booking",
      label: "lookup_booking",
      kind: "tool",
      sub: "read 1 row · 212ms",
      at: 6.4,
      status: "ok",
    },
    {
      id: "check_refund_policy",
      label: "check_refund_policy",
      kind: "tool",
      sub: "read 3 rows · 148ms",
      at: 21.8,
      status: "warn",
    },
    {
      id: "policy_check",
      label: "policy check",
      kind: "check",
      sub: "Refund outside the 24h window",
      at: 38.2,
      status: "fail",
    },
    {
      id: "hangup",
      label: "hangup",
      kind: "end",
      sub: "ended without a resolution",
      at: 74.1,
      status: "fail",
    },
  ],
  branches: [
    {
      after: 3,
      kind: "skipped",
      label: "expected here",
      nodes: [
        {
          id: "issue_credit",
          label: "issue_credit",
          kind: "skipped",
          sub: "expected here · never called",
          status: "fail",
        },
      ],
    },
    {
      after: 3,
      kind: "alternate",
      label: "not taken",
      nodes: [
        {
          id: "transfer_to_supervisor",
          label: "transfer_to_supervisor",
          kind: "alternate",
          sub: "the branch not taken",
          status: "idle",
        },
      ],
    },
  ],
  analysis:
    "The agent read the refund policy and then stopped. It never called issue_credit, which this scenario needed, and it didn't offer a transfer either. Gate the closing line on the credit tool's result.",
};
