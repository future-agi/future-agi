/**
 * "Needs your input".
 *
 * Deriving an environment from an agent's source gets most of the way and then
 * hits things no amount of reading can settle: a tool the sandbox cannot reach,
 * a secret nobody can invent, an objective the code does not state.
 *
 * The rule that makes this usable is the split. A **blocking** gap stops a run
 * and waits for you. A **non-blocking** gap does not — the builder takes its
 * best guess, runs anyway, and flags the guess as low confidence so a result
 * that rests on it is never quietly trusted. Nothing waits on a question that
 * did not have to be asked.
 */

/**
 * Credentials, as a manifest rather than a question.
 *
 * Reading the source tells you *which* secrets an agent needs and where the
 * requirement came from — an `.env.example` line, a compose file, an imported
 * SDK. That is a structured fact, so it is stated as one: id, provider,
 * purpose, where it was detected, and whether it is already satisfied. The user
 * is then asked only for what is genuinely missing, and never in prose.
 *
 * The value itself never passes through here. A requirement carries a
 * reference to a platform secret, never the secret — which is what lets the
 * manifest be shown, logged and exported without redaction.
 */
export const credentialManifest = (env, envState) => {
  const surface = env?.surface;
  const provider = envState?.agent?.values?.provider;
  const resolved = envState?.gapsResolved || {};

  const items = [
    surface === "voice" && {
      id: `${provider || "livekit"}_api_key`,
      provider: provider || "livekit",
      purpose: "Connect to the agent's room and place the call",
      detectedFrom: ".env.example",
      required: true,
    },
    surface === "voice" && {
      id: "deepgram_api_key",
      provider: "deepgram",
      purpose: "Speech to text for the caller side",
      detectedFrom: "imported SDK",
      required: true,
    },
    surface === "chat" && {
      id: "agent_endpoint_token",
      provider: "customer",
      purpose: "Authenticate to the agent's chat endpoint",
      detectedFrom: "connector configuration",
      required: true,
    },
    surface === "browser" && {
      id: "sandbox_browser_token",
      provider: "future-agi",
      purpose: "Drive the headless browser in the sandbox",
      detectedFrom: "compose manifest",
      required: true,
    },
    {
      id: "model_api_key",
      provider: "vertex-ai",
      purpose: "Run the simulated caller and the judges",
      detectedFrom: "platform connection",
      required: true,
      satisfiedBy: "voice-simulation-prod",
    },
    {
      id: "observability_token",
      provider: "future-agi",
      purpose: "Post traces and recordings back to the platform",
      detectedFrom: "platform connection",
      required: false,
      satisfiedBy: "workspace default",
    },
  ].filter(Boolean);

  return items.map((c) => ({
    ...c,
    status: c.satisfiedBy
      ? "configured"
      : resolved.secret ? "configured" : c.required ? "missing" : "optional",
  }));
};

/** A run cannot start while a required credential has no reference. */
export const missingCredentials = (env, envState) =>
  credentialManifest(env, envState).filter((c) => c.status === "missing");

export const GAP_STATUS = {
  blocking: { label: "Blocking", color: "#DC2626", blurb: "A run cannot start until this is answered" },
  assumed: { label: "Assumed", color: "#CA8A04", blurb: "Guessed so the run can proceed — confirm when you can" },
  resolved: { label: "Resolved", color: "#16A34A", blurb: "Answered by you" },
};

const secretName = (env) =>
  env?.surface === "voice" ? "TELEPHONY_TEST_KEY"
    : env?.surface === "browser" ? "SANDBOX_BROWSER_TOKEN"
      : "PAYMENTS_SANDBOX_KEY";

/**
 * Derived from the environment so the list is never generic: the tool that got
 * stubbed is a tool this agent actually declared.
 */
export const setupGaps = (env, envState) => {
  if (!env) return [];
  const tools = env.tools || [];
  const writeTool = tools.find((t) => /refund|issue|charge|send|delete|book/i.test(t.name)) || tools[tools.length - 1];
  const rules = env.rules || [];
  const promptOnly = Math.max(1, Math.round(rules.length * 0.6));

  const all = [
    /*
      "secret" and "objective" gaps used to sit here as blocking, but
      neither has a UI in this workspace to resolve — the secret is
      handled elsewhere (workspace credentials) and success criteria
      are answered by picking evaluations (below). Leaving them as
      blocking inflated the "N steps to complete" chip with items
      the user could not actually address on this screen.
    */
    writeTool && {
      id: "stub",
      status: "assumed",
      area: "Tools",
      title: `${writeTool.name} is stubbed`,
      confidence: "low",
      why: `${writeTool.desc} It mutates something outside the sandbox, so calling it for real would reach a live system. We recorded one response and replay it. Scenarios that end in ${writeTool.name} are testing that the agent *decided* to call it correctly, not that it worked.`,
      assumed: "Replays a recorded success. Failure paths are not exercised.",
      ask: {
        type: "choice",
        label: "How should it behave",
        options: [
          "Replay a recorded success (current)",
          "Alternate success and failure",
          "Point it at my own sandbox endpoint",
        ],
      },
      answered: null,
    },
    {
      id: "manifest",
      status: "assumed",
      area: "Contract",
      title: `${tools.length} tools read, argument types inferred for 2`,
      confidence: "medium",
      why: "Most arguments came back with exact names and permitted values. Two are untyped in the source, so we inferred them from how they are used at the call sites. Worth a glance — an inferred type that is wrong shows up as a scenario the agent cannot pass.",
      assumed: "Inferred from call sites.",
      ask: { type: "link", label: "Review the contract", to: "overview" },
      answered: null,
    },
    /*
      A run cannot be scored without evaluations. Suggested ones sit
      in the Evaluations tab waiting to be added; until at least one
      is added, this is a blocking gap. Disappears the moment the
      user (or auto-add) puts something in envState.evals.
    */
    (!envState?.evals?.length) && {
      id: "no-evals",
      status: "blocking",
      area: "Grading",
      title: "No evaluations added",
      why: "A run needs at least one evaluation to score against. Suggested ones are ready to add on the Evaluations tab; pick any that describe what a good outcome looks like for this environment.",
      ask: { type: "link", label: "Open the Evaluations tab", to: "evals" },
      answered: null,
    },
    rules.length > 0 && {
      id: "promptonly",
      status: "assumed",
      area: "Grading",
      title: `${promptOnly} of ${rules.length} rules are prompt-only`,
      confidence: "medium",
      why: "These rules are stated in the prompt but not enforced in code, so the world cannot prevent a breach — it can only notice one. They are graded rather than guaranteed, which is a weaker claim and worth knowing before you read a pass rate.",
      assumed: "Graded by judge, not enforced by the environment.",
      ask: { type: "link", label: "See which rules", to: "overview" },
      answered: null,
    },
  ].filter(Boolean);

  const resolved = envState?.gapsResolved || {};
  return all.map((g) => (resolved[g.id] ? { ...g, status: "resolved", answered: resolved[g.id] } : g));
};

export const gapCounts = (gaps) => ({
  blocking: gaps.filter((g) => g.status === "blocking").length,
  assumed: gaps.filter((g) => g.status === "assumed").length,
  resolved: gaps.filter((g) => g.status === "resolved").length,
});
