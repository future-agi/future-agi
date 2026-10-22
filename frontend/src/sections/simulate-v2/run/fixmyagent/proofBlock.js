/*
  The proof, as it goes into a draft PR's description: the verdict, the same
  request before and after, what happened to every scenario, and each
  constraint checked — so a reviewer who never opened Future AGI can judge
  the change where they review it.
*/
import { heldSplitText, markedNotCountedText, REPEATS } from "../../_mock/optimizationRuns";
import { TRUST_TIERS, proofKindOf } from "../../_mock/rsiModel";
import { scenarioTitle } from "../../_mock/scenarios";

const OUTCOME = { fixed: "Fixed", broke: "Broke", "still-failing": "Still failing", same: "Unchanged" };

/* "Sealed · Run by Future AGI", from the record, else from the agent. */
const proofOf = (record, agent) => record?.proof || (agent ? (() => { const p = proofKindOf(agent); return { kind: p.kind, tier: p.tier, label: p.label, tierLabel: p.tierLabel }; })() : null);

/*
  A prompt-only candidate — an agent built in Future AGI, run as its prompt
  on our platform model — names its trust tier first (we ran it), then what
  it was: a prompt simulation, with none of the agent's tools.
*/
export const PROMPT_SIMULATION = { label: "Prompt simulation", tierLabel: TRUST_TIERS.ours.label, note: "the prompt runs without your tools" };

/* What each proof adds after its label: where its tools ran, or — on your
   machine — who reported the code identity. */
const tailOf = (proof) => (proof.tier === "local" || proof.tierLabel === TRUST_TIERS.local.label
  ? " — the code identity is reported by your agent"
  : proof.kind === "live" ? " — tools reached your real systems" : "");

/** The proof as every surface names it: `{ ...proof, label, tierLabel, line }`. */
export const proofDisplay = (proof) => {
  if (!proof) return null;
  if (proof.kind === "prompt-only") {
    return { ...proof, label: PROMPT_SIMULATION.label, tierLabel: PROMPT_SIMULATION.tierLabel, line: `${PROMPT_SIMULATION.tierLabel} · ${PROMPT_SIMULATION.label.toLowerCase()} — ${PROMPT_SIMULATION.note}` };
  }
  return { ...proof, line: `${proof.label} · ${proof.tierLabel}${tailOf(proof)}` };
};

/** The proof label in one line — and a Live proof says its tools reached real systems. */
export const proofLabelLine = (proof) => (proof
  ? proof.kind === "prompt-only"
    ? proofDisplay(proof).line
    : `${proof.label} · ${proof.tierLabel}${proof.kind === "sealed" ? " — tools answered by the seeded world" : tailOf(proof) || " — the agent's own tools weren't part of it"}`
  : null);

export const proofMarkdown = ({ record, outcome, name, agent, members, channelNotes = [], identity = null }) => {
  const r = record?.result;
  if (!r || !outcome?.headline) return null;
  const winner = r.winner;
  const rows = [
    ...(r.heldTasks || []).map((t) => ({ ...t, outcome: r.heldPerScenario?.[t.id] || "same", held: true })),
    ...(r.trainMeasuredTasks || []).map((t) => ({ ...t, outcome: winner?.perScenario?.[t.id] || "same", held: false })),
  ];
  const count = (k, held) => rows.filter((x) => x.outcome === k && (held === undefined || x.held === held)).length;
  const line = (held) => `| ${held ? "Held back" : "Target"} | ${["fixed", "broke", "still-failing", "same"].map((k) => count(k, held)).join(" | ")} |`;

  /* One request, before and after — the issue's own scenario when there is
     one, preferring one the search never saw. */
  const example = rows
    .filter((x) => x.outcome === "fixed")
    .sort((a, b) => (b.fromIssue ? 2 : 0) + (b.held ? 1 : 0) - ((a.fromIssue ? 2 : 0) + (a.held ? 1 : 0)))[0];
  const failsBefore = example && record.reproduce?.perScenario?.[example.id]?.fails;
  const structural = (winner?.proposals || []).filter((p) => p.kind === "Architecture");
  const smaller = (r.pool || []).filter((p) => ["System prompt", "Tool description"].includes(p.kind)
    && structural.some((x) => x.addresses.some((id) => p.addresses.includes(id))));

  return [
    `## Proof · ${name || record.name} (${record.id})`,
    "",
    `**${outcome.headline}**`,
    ...(outcome.strength ? ["", `${outcome.strength.label}: ${outcome.strength.note}.`] : []),
    "",
    /* The numbers of what ships — the search's pick, or a chosen run once verified. */
    `Held back, never seen by the search: ${r.heldBase}% → ${outcome.heldScore ?? r.heldScore}% on ${r.heldMeasured} scenarios${r.confirm ? `, on a re-run after the pick (${r.confirm.selectionScore}% when picked)` : ""}. Target: ${r.base}% → ${outcome.train?.to ?? winner?.score ?? r.base}%.${outcome.gap != null ? ` Overfit gap: ${outcome.gap} pts.` : ""}`,
    "",
    `Proof: ${proofLabelLine(proofOf(record, agent)) || "Run by Future AGI"}.`,
    ...(identity ? [`Measured on ${identity} — the environment version held fixed, only the agent changed.`] : []),
    /* An evaluation as the goal is only as good as its agreement with people. */
    ...(r.goal?.reliability ? [r.goal.reliability.tier === "unmeasured"
      ? `Goal: ${r.goal.label}, not yet checked against annotations.`
      : `Goal: ${r.goal.label} — agrees with annotators at κ ${r.goal.reliability.kappa.toFixed(2)} on ${r.goal.reliability.labelled} labelled conversations${r.goal.reliability.tier === "unreliable" ? ", about chance" : ""}.`] : []),
    ...(r.textSearch ? ["Searched in text: runs were text conversations; the finalists ran as real calls."] : []),
    ...(r.split?.kind === "issue"
      ? [r.split.thin
        ? "Held-back check is thin: too few of the issue's scenario groups reproduced to hold any back."
        : `Held back: ${heldSplitText(r.split)} — whole groups, so no near-copy was among the targets.`]
      : r.split?.kind === "goal"
        ? [r.split.thin
          ? "Held-back check is thin: too few scenario groups fail the goal to hold any back."
          : `Held back: ${heldSplitText(r.split)} — whole groups, so no near-copy was among the targets.`]
        : []),
    ...((r.finalists || []).length ? [`Finalists ${r.confirm ? "ranked on one held-back run" : "scored once on the held-back set"}: ${r.finalists.map((f) => `Run ${f.n} ${f.heldScore}%${f.winner ? " (winner)" : f.accepted ? "" : ` (${f.why})`}`).join(", ")}.`] : []),
    /* Newer records: what deeper proof a code change got, and the noise it had to clear. */
    ...(r.calibration ? [`Run-to-run noise: ${record.baseAgentVersion || "the current version"} ran twice against itself — ${r.calibration.disagreed} of ${r.calibration.scenarios} scenarios disagreed, so a gain had to clear ±${r.noise} pts and a flip on those had to repeat.`] : []),
    ...((r.finalists || []).find((f) => f.winner)?.fullRegression ? [`Code change → ${(r.finalists || []).find((f) => f.winner).repeats} repeats per finalist and the full regression set${winner?.repoTests ? `, and ${winner.repoTests.command} passed` : ""}.`] : []),
    ...((r.finalists || []).find((f) => f.winner)?.envs?.length ? [`Also ran once in ${(r.finalists || []).find((f) => f.winner).envs.map((e) => `${e.name} (${e.held ? `${e.mustPass} must-pass held` : `“${e.broke.title}” failed twice`})`).join(", ")}.`] : []),
    ...(r.mustPass ? [`Must-pass: ${r.mustPass.total} scenarios — ${[r.mustPass.marked ? `${r.mustPass.marked} marked` : null, r.mustPass.remembered ? `${r.mustPass.remembered} regression memory` : null, `${r.mustPass.passing} passing every repeat today`].filter(Boolean).join(", ")}. A failure counts only when it repeats on a re-run.${(r.flakyToday || []).length ? ` ${r.flakyToday.length} flaky today, shown but not counted.` : ""}${markedNotCountedText(r) ? ` ${markedNotCountedText(r).charAt(0).toUpperCase()}${markedNotCountedText(r).slice(1)}.` : ""}`] : []),
    ...(members?.length ? [`Changes: ${members.join(", ")}.`] : []),
    ...channelNotes.map((n) => `${n}.`),
    "",
    ...(example ? [
      "### Same request, before and after",
      `> ${example.title}${example.held ? " _(held back)_" : ""}`,
      "",
      `- Before (agent ${record.baseAgentVersion || "today"}): failed ${failsBefore ?? (r.repeats || REPEATS)} of ${r.repeats || REPEATS} tries`,
      `- After (this change): passed ${r.repeats || REPEATS} of ${r.repeats || REPEATS} tries`,
      "",
    ] : []),
    "### Per scenario",
    "| | Fixed | Broke | Still failing | Unchanged |",
    "| --- | --- | --- | --- | --- |",
    line(true),
    line(false),
    "",
    ...(count("broke") ? [
      "Broke:",
      ...rows.filter((x) => x.outcome === "broke").map((x) => `- ${scenarioTitle(x.title)}${x.held ? " _(held back)_" : ""}`),
      "",
    ] : []),
    /* A structural change ships with its reasoning, for stricter review. */
    ...(structural.length ? [
      "### Why a structural change",
      ...structural.flatMap((p) => [`**${p.title}.** ${p.why}`, ""]),
      smaller.length
        ? `Smaller changes were tried first and didn't fix these on their own: ${smaller.map((p) => p.title.toLowerCase()).join("; ")}.`
        : "No prompt or tool-description change addressed these scenarios.",
      "Structural changes get stricter review: the full regression set ran, every scenario repeated.",
      "",
    ] : []),
    "### Constraints",
    ...(outcome.checks || []).map((c) => `- ${c.pass === null ? "◌" : c.pass ? "✓" : "✗"} ${c.label} — ${c.actual}`),
    "",
    `_Verified by Future AGI: baseline and change on the same scenarios and seed, each repeated ${r.repeats || REPEATS} times. ${OUTCOME.fixed} means it failed before and passes now._`,
  ].join("\n");
};
