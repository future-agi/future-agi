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
 * that rests on it is never quietly trusted.
 *
 * Ported from the prototype's setup-gaps mock (the gaps half). The
 * credential manifest helpers and the dead `secretName` helper are left out —
 * they belong to the workspace-credentials surface, not here. Colours are read
 * from BUILD_TONES.
 */
import { BUILD_TONES } from "src/sections/simulate/environments/buildEnvironment/buildTones";

export const GAP_STATUS = {
  blocking: { label: "Blocking", color: BUILD_TONES.red, blurb: "A run cannot start until this is answered" },
  assumed: { label: "Assumed", color: BUILD_TONES.amber, blurb: "Guessed so the run can proceed — confirm when you can" },
  resolved: { label: "Resolved", color: BUILD_TONES.green, blurb: "Answered by you" },
};

/**
 * Derived from the environment so the list is never generic: the tool that got
 * stubbed is a tool this agent actually declared.
 */
export const setupGaps = (env, envState) => {
  if (!env) return [];
  const tools = env.tools || [];
  const writeTool =
    tools.find((t) => /refund|issue|charge|send|delete|book/i.test(t.name)) || tools[tools.length - 1];
  const rules = env.rules || [];
  const promptOnly = Math.max(1, Math.round(rules.length * 0.6));

  const all = [
    // "secret" and "objective" gaps used to sit here as blocking, but neither
    // has a UI in this workspace to resolve — the secret is handled elsewhere
    // and success criteria are answered by picking evaluations (below). Leaving
    // them as blocking inflated the "N steps to complete" chip with items the
    // user could not actually address on this screen.
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
      ask: { type: "link", label: "Review the contract", to: "summary" },
      answered: null,
    },
    // A run cannot be scored without evaluations. Suggested ones sit in the
    // Evaluations tab waiting to be added; until at least one is added, this is
    // a blocking gap. Disappears the moment something lands in envState.evals.
    !envState?.evals?.length && {
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
      ask: { type: "link", label: "See which rules", to: "summary" },
      answered: null,
    },
  ].filter(Boolean);

  const resolved = envState?.gapsResolved || {};
  return all.map((g) =>
    resolved[g.id] ? { ...g, status: "resolved", answered: resolved[g.id] } : g,
  );
};

export const gapCounts = (gaps) => ({
  blocking: gaps.filter((g) => g.status === "blocking").length,
  assumed: gaps.filter((g) => g.status === "assumed").length,
  resolved: gaps.filter((g) => g.status === "resolved").length,
});
