// Fallback sample-preview fixtures for the five non-Observe onboarding paths.
// The backend sample manifest can provide the same preview shape through
// sampleProject.artifactRefs.optional_paths; these fixtures keep older API
// payloads renderable. Nothing here is provisioned for the workspace and none
// of it is the user's real/production data; the preview chrome labels every
// fixture as a Sample.
//
// Pure data plus a small selector. No React, no network, no activation state.

export const PATH_SAMPLE_FIXTURES = {
  prompt: {
    primaryPath: "prompt",
    eyebrow: "Prompt sample",
    headline: "An edit improved one case but quietly broke another",
    summary:
      "Two prompt versions on the same example set. v2 sharpened one reply and regressed a different one - the trade-off you would otherwise ship blind.",
    ctaLabel: "Now do it with your data",
    starterAction: {
      label: "Run sample prompt comparison",
      resultLabel: "Sample comparison complete",
      description:
        "Runs a starter prompt edit against three labelled cases in this browser.",
    },
    layout: "promptDiff",
    versions: [
      { id: "v1", label: "Baseline (v1)", note: "Current production prompt" },
      { id: "v2", label: "Edit (v2)", note: "Added a politeness instruction" },
    ],
    cases: [
      {
        id: "case-refund",
        title: "Refund eligibility question",
        v1: "Eligible if the order shipped under 30 days ago.",
        v2: "Yes, you are eligible for a refund - the order shipped 12 days ago.",
        verdict: "improved",
        verdictLabel: "Improved",
        reason: "v2 answers directly with the concrete window.",
      },
      {
        id: "case-policy",
        title: "Out-of-policy escalation",
        v1: "This is outside policy. I'll route you to a human agent.",
        v2: "I completely understand and I'd be happy to help with that!",
        verdict: "regressed",
        verdictLabel: "Regressed",
        reason: "v2 drops the escalation and over-promises.",
      },
      {
        id: "case-status",
        title: "Order status lookup",
        v1: "Your order is out for delivery, arriving today.",
        v2: "Your order is out for delivery, arriving today.",
        verdict: "unchanged",
        verdictLabel: "No change",
        reason: "Identical output across both versions.",
      },
    ],
    metrics: [
      { label: "Avg cost / call", from: "$0.0021", to: "$0.0034", trend: "up" },
      { label: "Avg latency", from: "0.9s", to: "1.4s", trend: "up" },
      { label: "Cases changed", from: "-", to: "2 of 3", trend: "neutral" },
    ],
    takeaway:
      "Side-by-side scoring catches the silent regression before it reaches users.",
  },

  evals: {
    primaryPath: "evals",
    eyebrow: "Eval run sample",
    headline: "See exactly which examples fail - and why",
    summary:
      "A finished quality run over 10 examples. Seven passed; the three failures are grouped by root cause so you know what to fix first.",
    ctaLabel: "Now do it with your data",
    starterAction: {
      label: "Run sample eval",
      resultLabel: "Sample eval complete",
      description:
        "Runs a starter dataset and scorer in this browser so the failure review is visible immediately.",
    },
    layout: "evalRun",
    distribution: { total: 10, pass: 7, fail: 3 },
    failureGroups: [
      {
        cause: "Hallucinated refund amount",
        count: 2,
        rows: [
          {
            id: "ex-04",
            input: "How much will I be refunded for order #8841?",
            expected: "$48.00 (original charge)",
            got: "$62.50",
          },
          {
            id: "ex-07",
            input: "Refund total for the cancelled annual plan?",
            expected: "$120.00",
            got: "$144.00 plus tax",
          },
        ],
      },
      {
        cause: "Missing citation",
        count: 1,
        rows: [
          {
            id: "ex-09",
            input: "What is the data-retention window?",
            expected: "30 days (cite retention policy)",
            got: "Around a month",
          },
        ],
      },
    ],
    passSummary:
      "7 examples met every check - correct value, grounded answer, on-policy tone.",
    takeaway:
      "Failures cluster by cause, so one fix often clears several examples at once.",
  },

  agent: {
    primaryPath: "agent",
    eyebrow: "Agent trace sample",
    headline: "Follow the agent's reasoning to the exact step that failed",
    summary:
      "One scenario run, step by step. The trace shows where the tool call went wrong instead of just a final wrong answer.",
    ctaLabel: "Now do it with your data",
    starterAction: {
      label: "Run sample scenario",
      resultLabel: "Sample scenario complete",
      description:
        "Runs a packaged refund scenario in this browser and reveals the trace steps.",
    },
    layout: "agentTrace",
    scenario: 'Customer: "Refund my last order, it arrived damaged."',
    steps: [
      {
        id: "s1",
        kind: "reasoning",
        label: "Plan",
        detail:
          "Identify the customer's most recent order, then call the refund tool.",
        status: "ok",
      },
      {
        id: "s2",
        kind: "tool",
        label: "Tool call: lookup_orders",
        detail: "Returned orders #8841 (latest) and #8830 (previous).",
        status: "ok",
      },
      {
        id: "s3",
        kind: "tool",
        label: "Tool call: refund_api",
        detail: "Called refund_api with order_id #8830 - the previous order.",
        status: "failed",
      },
      {
        id: "s4",
        kind: "reasoning",
        label: "Respond",
        detail: "Confirmed a refund to the customer for the wrong order.",
        status: "warning",
      },
    ],
    failureCause: {
      step: "Tool call: refund_api",
      detail:
        "Passed order_id #8830 instead of the latest order #8841 - the agent picked the wrong item from the lookup result.",
    },
    takeaway:
      "The wrong order_id is visible at the tool boundary, not buried in a wrong final reply.",
  },

  voice: {
    primaryPath: "voice",
    eyebrow: "Voice call sample",
    headline: "Voice quality is more than a transcript",
    summary:
      "A short call snippet with turn timing, a caller interruption, the outcome, and an extracted field - the signals a plain transcript hides.",
    ctaLabel: "Now do it with your data",
    starterAction: {
      label: "Play sample voice call",
      resultLabel: "Sample call reviewed",
      description:
        "Plays through a starter call review in this browser before any telephony setup.",
    },
    layout: "voiceCall",
    transcript: [
      {
        id: "t1",
        at: "00:04",
        speaker: "agent",
        text: "Thanks for calling - how can I help today?",
      },
      {
        id: "t2",
        at: "00:07",
        speaker: "caller",
        text: "I want to cancel my subscription before the next charge.",
      },
      {
        id: "t3",
        at: "00:11",
        speaker: "agent",
        text: "I can help with that. First, let me confirm your account-",
        interrupted: true,
      },
      {
        id: "t4",
        at: "00:12",
        speaker: "caller",
        text: "Just cancel it, please.",
        note: "Caller interrupted the agent",
      },
    ],
    extracted: [
      { label: "Intent", value: "cancel_subscription" },
      { label: "Sentiment", value: "Impatient" },
      { label: "Outcome", value: "Cancellation scheduled" },
    ],
    timingNote: "Agent responded 0.6s after the interruption - no dead air.",
    laterStepNote:
      "Connecting telephony to monitor live calls is a later, optional step - not the first thing you set up.",
    takeaway:
      "Interruptions, timing, and extracted intent decide call quality long before a transcript does.",
  },

  gateway: {
    primaryPath: "gateway",
    eyebrow: "Gateway log sample",
    headline: "Route and observe requests by changing one line",
    summary:
      "A single routed request that fell back from one provider to another, with cost and latency captured - and the one-line change that enables it.",
    ctaLabel: "Now do it with your data",
    starterAction: {
      label: "Run sample gateway request",
      resultLabel: "Sample request routed",
      description:
        "Runs a starter gateway request in this browser to show the one-line routing result.",
    },
    layout: "gatewayLog",
    logRow: {
      requestId: "req_3f9a-sample",
      requested: "openai/gpt-4.1-mini",
      served: "mistral/mistral-small",
      fallback: "OpenAI timed out, fell back to Anthropic",
      status: "200 (after fallback)",
      latency: "1.2s",
      cost: "$0.0009",
    },
    snippet: {
      label: "Change one line - your base URL",
      before: 'base_url="https://api.openai.com/v1"',
      after: 'base_url="https://gateway.futureagi.com/v1"',
    },
    takeaway:
      "The fallback kept the request alive, and every routed call is logged for cost and control.",
  },
};

export const PATH_SAMPLE_FIRST_STAGES = {
  prompt: ["start_prompt"],
  agent: ["create_agent"],
  gateway: ["configure_gateway_provider"],
  evals: ["create_eval_dataset"],
  voice: ["create_voice_agent"],
};

const optionalPathRefs = (sampleProject) =>
  sampleProject?.artifactRefs?.optional_paths ||
  sampleProject?.artifactRefs?.optionalPaths ||
  {};

const serverPreviewForPath = (primaryPath, sampleProject) => {
  const ref = optionalPathRefs(sampleProject)[primaryPath];
  const preview = ref?.preview;
  if (!preview || preview.primaryPath !== primaryPath || !preview.layout) {
    return null;
  }
  return preview;
};

export const getPathSampleFixture = (primaryPath, sampleProject = null) =>
  serverPreviewForPath(primaryPath, sampleProject) ||
  PATH_SAMPLE_FIXTURES[primaryPath] ||
  null;

export const isPathSampleFirstStage = (primaryPath, stage) =>
  Boolean(PATH_SAMPLE_FIRST_STAGES[primaryPath]?.includes(stage));

export const hasPathSampleFixture = (primaryPath, sampleProject = null) =>
  Boolean(getPathSampleFixture(primaryPath, sampleProject));
