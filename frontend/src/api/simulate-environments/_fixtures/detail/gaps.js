// Vel's Needs input sample for a voice support line (his prototype's
// env-voice-support), copy without em dashes. Shown only with
// ?uiSample=gaps: the API has no setup questions yet.

export const SAMPLE_GAP_SUITE = { total: 68 };

export const SAMPLE_GAPS = [
  {
    id: "gap_tool_link:issue_refund+lookup_order",
    status: "optional",
    title: "Do issue_refund and lookup_order share a store?",
    why: "issue_refund works on the order that lookup_order looks up, but nothing in the source says they read the same records. If they do, the world keeps them consistent: what one writes, the other sees within the run. If not, each answers from its own data.",
    suggested: "Yes, one store, kept consistent across both tools",
    options: [
      "Yes, one store, kept consistent across both tools",
      "No, separate systems",
    ],
    coverage: 10,
    coverageWhat: "scenarios that call both",
  },
  {
    id: "gap_tool_fidelity:shipment_status",
    status: "optional",
    title: "shipment_status replays one recorded answer",
    why: "Live carrier tracking, but Replay answers with a response captured once, so a scenario that needs it to change (a parcel moving, a slot filling) sees the same answer every time. Stateful answers from the world's data and changes as the run does.",
    suggested: "Keep Replay: deterministic, may be stale",
    options: ["Keep Replay: deterministic, may be stale", "Switch to Stateful"],
    coverage: 7,
    coverageWhat: "scenarios that call shipment_status",
  },
  {
    id: "manifest",
    status: "assumed",
    by: "builder",
    title: "8 tools read, argument types inferred for 2",
    why: "Most arguments came back with exact names and permitted values. Two are untyped in the source, so we inferred them from how they are used at the call sites. Worth a glance: an inferred type that is wrong shows up as a scenario the agent cannot pass.",
    answered: "Inferred from call sites.",
    ask: { label: "Review the tools", to: "world" },
  },
  {
    id: "stub",
    status: "assumed",
    by: "builder",
    title: "How issue_refund fails is inferred",
    why: "Refund up to the order total. The source shows how it succeeds but not how it fails, so the world's handler fails the way the call sites expect it to: an unknown id, and a value over its limit.",
    answered: "Errors on an unknown id; rejects a value over its limit",
    options: [
      "Errors on an unknown id; rejects a value over its limit",
      "Always succeeds",
    ],
    input: { label: "Or describe how it fails" },
  },
  {
    id: "gap_modality",
    status: "assumed",
    by: "builder",
    title: "Caller audio quality",
    why: "The source says the agent takes phone calls, not what the line sounds like. Background noise changes how often speech-to-text mishears an order number.",
    answered: "Clean line; 1 call in 5 with background noise",
    options: [
      "Clean line on every call",
      "Clean line; 1 call in 5 with background noise",
      "Background noise on every call",
    ],
  },
  {
    id: "promptonly",
    status: "assumed",
    by: "builder",
    title: "4 of 6 rules are prompt-only",
    why: "These rules are stated in the prompt but not enforced in code, so the world cannot prevent a breach, only notice one. They are graded rather than guaranteed, which is a weaker claim and worth knowing before you read a pass rate.",
    answered: "Graded by judge, not enforced by the environment.",
    ask: { label: "See which rules", to: "contract" },
  },
];

// Scenarios that sit out while the optional questions stay open (Vel's
// meter counts the union, not the sum).
export const SAMPLE_SIT_OUT = 17;

export const SAMPLE_CREDENTIALS = [
  {
    id: "vapi_api_key",
    purpose: "Connect to the agent's room and place the call",
    detectedFrom: ".env.example",
    ref: "vault://acme/sim/vapi_api_key",
  },
  {
    id: "deepgram_api_key",
    purpose: "Speech to text for the caller side",
    detectedFrom: "imported SDK",
  },
  {
    id: "model_api_key",
    purpose: "Run the simulated caller and the judges",
    detectedFrom: "platform connection",
    satisfiedBy: "voice-simulation-prod",
  },
  {
    id: "observability_token",
    purpose: "Post traces and recordings back to the platform",
    detectedFrom: "platform connection",
    satisfiedBy: "workspace default",
  },
];
