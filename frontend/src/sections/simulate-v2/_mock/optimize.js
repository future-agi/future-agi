/**
 * What to change, derived from what actually failed.
 *
 * The proposals here used to be three hand-written examples about refund
 * ceilings and order details — which read convincingly right up until you
 * opened them on an airline environment that has no refund ceiling, no orders,
 * and no `escalate_to_human`. A fix for a rule the environment does not have is
 * worse than no fix: it is a confident answer to a question nobody asked, and
 * it teaches people not to read this panel.
 *
 * So every proposal is clustered from the run's own evidence — the tools the
 * call log says were never called, the claims it could not support, the rules
 * this environment actually declares — and every one names the tasks it would
 * address. Which is also what makes the arithmetic checkable: a proposal's lift
 * is the share of measured tasks it claims to fix, and two proposals that
 * address the same task cannot both count it.
 */

import { isMeasured } from "./failures";

const readable = (name = "") => name.replace(/_/g, " ");

/**
 * A proposal's kind maps to a stereotypical file in the agent's own
 * codebase. When the drawer previews the change as a code diff, this is
 * how it decides which file to name and which extension colours the header
 * with. Fixes at the env layer (Verifier / Reward spec) map to the
 * environment side and are handled separately by the "Apply to checks"
 * button rather than this flow.
 */
export const FILE_FOR_KIND = {
  "System prompt":     { path: (env) => `${env?.id || "agent"}/prompts/system.md`, language: "markdown" },
  "Tool description":  { path: (env, p) => `${env?.id || "agent"}/tools/${(p?.step || "tool").toLowerCase()}.py`, language: "python" },
  Memory:              { path: (env) => `${env?.id || "agent"}/state/memory.py`, language: "python" },
  Architecture:        { path: (env) => `${env?.id || "agent"}/graph.py`, language: "python" },
};

/**
 * Where a proposal lands as a code change. Undefined for env-layer fixes
 * so the drawer can filter those out of the "new version" bundle — those
 * belong to the "apply to checks" affordance and mint an environment
 * version, not an agent version.
 */
export const changeFileFor = (env, proposal) => {
  const meta = FILE_FOR_KIND[proposal?.kind];
  if (!meta) return null;
  return { path: meta.path(env, proposal), language: meta.language };
};

/** The rule this environment declares about a tool, if it declares one. */
const ruleAbout = (env, tool) =>
  (env?.rules || []).find((r) => new RegExp(readable(tool).split(" ")[0], "i").test(r));

/**
 * Proposals for one run.
 *
 * `tasks` should already be the agent's failures — scenarios nothing could be
 * measured on are not a prompt problem, and the panel filters them out before
 * calling this.
 */
export const proposalsFor = (env, failing = [], measured = []) => {
  const out = [];
  const pct = (n) => (measured.length ? Math.round((n / measured.length) * 100) : 0);

  /* ── 1. tools the scenarios needed and the agent never called ── */
  const byMissingTool = new Map();
  failing.forEach((t) => {
    (t.callLog?.missing || []).forEach((name) => {
      if (!byMissingTool.has(name)) byMissingTool.set(name, []);
      byMissingTool.get(name).push(t);
    });
  });

  [...byMissingTool.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .forEach(([tool, list]) => {
      const rule = ruleAbout(env, tool);
      const desc = (env?.tools || []).find((x) => x.name === tool)?.desc;
      out.push({
        id: `missing-${tool}`,
        kind: "Tool description",
        title: `Say when ${tool} applies`,
        why: `${list.length} ${list.length === 1 ? "task" : "tasks"} needed ${tool} and never called it${desc ? ` — the description says only "${desc}"` : ""}.`,
        diff: [
          {
            type: "add",
            /* The rule quoted as the rule, not bent into a sentence around
               it — "Use when: State the fare difference" reads as nonsense. */
            text: rule
              ? `Call it whenever the caller's request depends on ${readable(tool)}. Policy: ${rule.replace(/\.$/, "")}.`
              : `Call it whenever the caller's request depends on ${readable(tool)} — do not describe the result without calling it.`,
          },
        ],
        addresses: list.map((t) => t.id),
        /* The node in the trajectory this is about, so the evidence opens at
           the step rather than at the top of the trace. */
        step: tool,
      });
    });

  /* ── 2. claims the call log could not support ── */
  const claimed = failing.filter((t) => t.callLog?.unsupportedClaim);
  if (claimed.length) {
    out.push({
      id: "unsupported-claims",
      kind: "System prompt",
      title: "Gate the closing statement on the tool result",
      why: `${claimed.length} ${claimed.length === 1 ? "task" : "tasks"} told the caller something the call log does not support — every word-reading grader passed them.`,
      diff: [
        { type: "remove", text: "Confirm the outcome to the caller once you have handled it." },
        { type: "add", text: "Only state that something is done after the tool that does it has returned successfully. If it has not, say what you attempted and what is outstanding." },
      ],
      addresses: claimed.map((t) => t.id),
      step: "closing",
    });
  }

  /* ── 3. rules this environment declares that a failing task breached ── */
  const critical = failing.filter((t) => t.critical);
  if (critical.length && (env?.rules || []).length) {
    /* The rule the failing blockers are actually about, matched on the
       scenario titles rather than assumed. */
    const rule = (env.rules || []).find((r) => {
      const key = r.toLowerCase().match(/[a-z]{5,}/g) || [];
      return critical.some((t) => key.filter((w) => `${t.title} ${t.task}`.toLowerCase().includes(w)).length >= 2);
    }) || env.rules[0];

    out.push({
      id: "state-rule",
      kind: "System prompt",
      title: "State the rule in the prompt, not just in the world",
      why: `${critical.length} release ${critical.length === 1 ? "blocker" : "blockers"} failed on a rule the environment enforces but the prompt never states.`,
      diff: [
        { type: "add", text: `${rule} If the caller pushes back, explain the rule rather than making an exception.` },
      ],
      addresses: critical.map((t) => t.id),
      step: "policy_check",
    });
  }

  /* ── 4. the grader that fails most often, when it is not covered above ── */
  const graderCounts = new Map();
  failing.forEach((t) => {
    (t.evalResults || []).filter((r) => !r.passed).forEach((r) => {
      graderCounts.set(r.name, [...(graderCounts.get(r.name) || []), t]);
    });
  });
  const [worstGrader, worstList] = [...graderCounts.entries()].sort((a, b) => b[1].length - a[1].length)[0] || [];
  if (worstGrader && !out.some((p) => worstList.every((t) => p.addresses.includes(t.id)))) {
    out.push({
      id: `grader-${worstGrader}`,
      kind: "System prompt",
      title: `Answer what ${worstGrader.toLowerCase()} is measuring`,
      why: `${worstList.length} ${worstList.length === 1 ? "task" : "tasks"} came in under the ${worstGrader.toLowerCase()} threshold.`,
      diff: [
        { type: "add", text: `Before finishing, check the answer against ${worstGrader.toLowerCase()}: say only what the tools returned, in the caller's terms, and name anything you could not confirm.` },
      ],
      addresses: worstList.map((t) => t.id),
    });
  }

  /*
    Two levers beyond the prompt.

    The improvement plan is memory and architecture first, weights later and
    only on open models — so a lens that only ever proposes prompt edits is
    working with one hand. A fact the agent had and lost between turns is a
    memory problem; a step it should never have been able to skip is an
    architecture problem, and no amount of prompt wording fixes either.
  */
  const forgot = failing.filter((t) => (t.steps || []).length > 6 && t.callLog?.unsupportedClaim);
  if (forgot.length) {
    out.push({
      id: "memory-carry",
      kind: "Memory",
      title: "Carry the verified facts forward instead of re-deriving them",
      why: `${forgot.length} long ${forgot.length === 1 ? "episode" : "episodes"} lost track of what had already been established and closed on a claim instead of a result.`,
      diff: [
        { type: "add", text: "Working memory: { verified_identity, tools_called, results_returned } — written after every tool result, read before every claim." },
      ],
      addresses: forgot.map((t) => t.id),
      step: "closing",
    });
  }

  const skipped = failing.filter((t) => (t.callLog?.missing || []).length);
  if (skipped.length > 1) {
    out.push({
      id: "architecture-gate",
      kind: "Architecture",
      title: "Add a verification step before the closing turn",
      why: `${skipped.length} episodes reached their closing statement with a required tool uncalled. A prompt line asks the agent not to; a step in the graph does not let it.`,
      diff: [
        { type: "add", text: "before_finish: assert every required tool for this scenario returned successfully — otherwise route to the recovery branch instead of closing." },
      ],
      addresses: skipped.map((t) => t.id),
      step: "closing",
    });
  }

  /* Weights are deliberately not a lever here: closed models cannot be tuned,
     and offering it would be a promise the product cannot keep. */

  return out.map((p) => ({ ...p, lift: pct(p.addresses.length) }));
};

/**
 * The projected rate for a set of included proposals.
 *
 * Computed from the union of the tasks they address, not the sum of their
 * percentages — two changes that fix the same task fix it once, and summing
 * lifts is how a panel ends up projecting 118%.
 */
export const projectedRate = (measured = [], included = []) => {
  if (!measured.length) return 0;
  const fixed = new Set(included.flatMap((p) => p.addresses));
  const share = (t) => t.passShare ?? (t.status === "passed" ? 1 : 0);
  /*
    Measured the same way the current rate is — the mean of per-scenario pass
    proportions, with an addressed scenario counted as passing outright. Using
    a strict pass count here instead made the projection read *below* the
    current rate with nothing selected, because a flaky scenario contributes
    two thirds to one number and zero to the other.
  */
  const total = measured.reduce((a, t) => a + (fixed.has(t.id) ? 1 : share(t)), 0);
  return Math.min(100, Math.round((total / measured.length) * 100));
};

/** How many distinct tasks a selection would address. */
export const addressedCount = (included = []) =>
  new Set(included.flatMap((p) => p.addresses)).size;

/** The tasks a run can still be optimised against. */
export const optimisable = (tasks = []) =>
  tasks.filter((t) => isMeasured(t) && (t.status === "failed" || t.status === "flaky"));

/**
 * Fixes to the measurement, not the agent.
 *
 * When the gaming analyzer fires, the honest next move is not a prompt edit.
 * A check that passes an episode which called nothing will pass the next
 * version too — so "improving" the agent against it moves a number that was
 * never measuring anything. These are the changes that make the number real,
 * and they are the one kind of proposal that is expected to push the pass rate
 * *down*: they stop counting passes that were never earned.
 *
 * They change the environment rather than the agent, so applying one mints an
 * environment version and lapses the scenario proofs that were made against the
 * older checks. That is the correct consequence, not a side effect to hide.
 */
const VERIFIER_FIXES = {
  "unsupported-claim": {
    title: "Require tool evidence before task success can pass",
    kind: "Verifier",
    why: "Task success currently reads the closing sentence. An episode that says the booking was moved scores the same as one that moved it.",
    diff: [
      { type: "remove", text: "task_success: the response confirms the caller's request was handled." },
      { type: "add", text: "task_success: the response confirms the request AND the tool that performs it returned successfully in this episode." },
    ],
  },
  "hollow-pass": {
    title: "Make the scenario's required steps a gate, not a note",
    kind: "Verifier",
    why: "Episodes passed while a step the scenario declares as required was never taken. A checklist that does not gate is documentation.",
    diff: [
      { type: "add", text: "gate: every step marked required in the scenario must appear in the call log before any check may return pass." },
    ],
  },
  "efficiency-exploit": {
    title: "Pay the efficiency term only on episodes that succeeded",
    kind: "Reward spec",
    why: "The efficiency bonus is paid for finishing under the reference length. Episodes that got there by skipping the work collected it.",
    diff: [
      { type: "remove", text: "efficiency: +0.2 when steps < reference length." },
      { type: "add", text: "efficiency: +0.2 when steps < reference length AND the terminal task reward was earned." },
    ],
  },
};

/** The measurement fixes a report's gaming signals call for. */
export const verifierFixes = (report = []) => {
  const gaming = report.find((r) => r.id === "gaming");
  return (gaming?.signals || [])
    .map((s) => (VERIFIER_FIXES[s.id] ? { id: `verifier-${s.id}`, ...VERIFIER_FIXES[s.id], addresses: s.tasks || [], signal: s.label } : null))
    .filter(Boolean);
};

/**
 * Would this change help a scenario it was not derived from?
 *
 * A proposal is written from the scenarios that failed in front of it, and its
 * `addresses` list holds exactly those. That list is the wrong thing to score a
 * held-out set with: none of its ids are in the held-out split, so every
 * held-out projection came back identical to the baseline and the number could
 * only ever fall. The whole point of a held-out set — does this change
 * generalise — was structurally unanswerable.
 *
 * What actually generalises is the mechanism, not the task list. A prompt line
 * that gates the closing statement on a tool result helps any episode that
 * closed on an unsupported claim, including ones the optimizer never saw. So
 * the predicate re-applies the reason the change exists, rather than checking
 * membership of a list it could not be a member of.
 *
 * Kept as a pure function of ids rather than a closure on the proposal, because
 * proposals are persisted with their optimization run and functions do not
 * survive a round trip through storage.
 */
export const generalisesTo = (proposal, task) => {
  if (!proposal || !task) return false;
  const missing = task.callLog?.missing || [];

  if (proposal.id.startsWith("missing-")) {
    return missing.includes(proposal.id.slice("missing-".length));
  }
  if (proposal.id === "unsupported-claims") return !!task.callLog?.unsupportedClaim;
  if (proposal.id === "state-rule") return !!task.critical;
  if (proposal.id.startsWith("grader-")) {
    const name = proposal.id.slice("grader-".length);
    return (task.evalResults || []).some((r) => !r.passed && r.name === name);
  }
  if (proposal.id === "memory-carry") {
    return (task.steps || []).length > 6 && !!task.callLog?.unsupportedClaim;
  }
  if (proposal.id === "architecture-gate") return missing.length > 0;
  return false;
};

/** A scenario's pass share, the one definition every surface uses. */
export const passShareOf = (t) => t.passShare ?? (t.status === "passed" ? 1 : 0);

/**
 * A suite-wide projection for a change set that was derived from a subset.
 *
 * `projectedRate` counts a scenario as fixed only if it appears in a proposal's
 * `addresses` list, which is right when the proposals were written from the
 * whole suite. An optimizer's winner was written from the training split, so
 * scoring it that way across the full suite silently ignores every held-out
 * scenario it would in fact help — and the expectation recorded against the
 * next run would be too low by exactly the amount the change generalises.
 */
export const projectedWithGeneralisation = (measured = [], proposals = []) => {
  if (!measured.length) return 0;
  const helps = (t) => proposals.some((p) => p.addresses.includes(t.id) || generalisesTo(p, t));
  const total = measured.reduce((a, t) => a + (helps(t) ? 1 : passShareOf(t)), 0);
  return Math.min(100, Math.round((total / measured.length) * 100));
};
