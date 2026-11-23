/*
  An epoch's candidates, read for the winner-first page: where the epoch
  stopped each one, the sentence that leads the page, and what the winner
  changed in a few words. Everything here comes from the stored result —
  the ladder each candidate climbed (ladderOf), the finalists' held-back
  scores and what each one tried. Nothing is estimated: a candidate the
  held-back check never ran on says so.
*/
import { ladderOf, projectedHeld } from "../_mock/optimizationRuns";
import { changeFileFor } from "../_mock/optimize";
import { brokeOutsideMustPassOf } from "./epochResultRows";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/*
  The groups, in the order the Candidates tab lists them: the winner, the
  ones that passed every step they ran, then each step that stopped one —
  the last step first, since a candidate that got that far was the closest
  call. `open` marks the groups worth reading before anyone asks.
*/
export const CANDIDATE_GROUPS = [
  { id: "winner", label: "Winner", open: true },
  { id: "passed", label: "Passed, not picked", open: true },
  { id: "mustpass", label: "Rejected at must-pass scenarios", open: true },
  { id: "held", label: "Rejected at held-back scenarios" },
  { id: "repeats", label: "Rejected at holds over repeats" },
  { id: "target", label: "Rejected at target scenarios" },
  { id: "limits", label: "Rejected at reply time and cost" },
  { id: "repoTests", label: "Rejected at the repository's tests" },
  { id: "compat", label: "Rejected at fits the environment" },
  { id: "static", label: "Rejected at change is valid" },
];

/* A stopped epoch never reached the held-back check, so nothing was "not picked". */
const PASSED_WHEN_STOPPED = "Stopped before the held-back check";
const passedLabel = ({ stopped }) =>
  stopped ? PASSED_WHEN_STOPPED : "Passed, not picked";

/* Where the epoch stopped a candidate the ladder passed but the gate didn't. */
const stageOfWhy = (c) => {
  if (!c || c.eligible) return null;
  if (c.brokeBlocker) return "mustpass";
  if (c.cheated) return "static";
  if (c.unfit) return /static/.test(c.why || "") ? "static" : "compat";
  if (c.repoFailed) return "repoTests";
  if (/cost|latency/.test(c.why || "")) return "limits";
  if (/noise/.test(c.why || "")) return "held";
  return null;
};

const cap = (s) => (s ? `${s.charAt(0).toUpperCase()}${s.slice(1)}` : s);
/* The ladder's note, without the "it stops here" its own screen adds. */
const shortNote = (note) =>
  cap(
    String(note || "")
      .replace(/\s*[.—-]*\s*(It stops here|it stops here|dropped here)\.?$/, "")
      .replace(/\.$/, "")
      .trim(),
  );

/**
 * Every candidate the epoch tried, with the step that stopped it.
 *
 * `rows` are the page's candidate rows (trialRowsFor), `candidates` the
 * gate's reading (candidatesOf). The winner is the kept candidate, and only
 * when the epoch has one to review — a "no safe change" epoch's best try is
 * listed at the step that stopped it.
 */
export const candidateRowsOf = (
  record,
  {
    rows = [],
    candidates = [],
    keptN = null,
    hasWinner = false,
    stopped = false,
  } = {},
) => {
  const r = record?.result;
  if (!r?.trials?.length) return [];
  const thin = !!r.split?.thin;
  /* The kept candidate's target score — what a passed one is read against. */
  const keptScore = r.trials.find((x) => x.n === keptN)?.score ?? null;
  return r.trials.map((t) => {
    const row = rows.find((x) => x.n === t.n) || {};
    const c = candidates.find((x) => x.n === t.n) || null;
    const finalist = (r.finalists || []).find((f) => f.n === t.n) || null;
    const tiers = ladderOf(record, t);
    const fail = tiers.find((x) => x.state === "fail") || null;
    const winner = hasWinner && t.n === keptN;
    const stage = winner
      ? "winner"
      : fail
        ? fail.id
        : stageOfWhy(c) || "passed";

    /* Held back: a finalist's own score; the kept pick's projection until
       it's verified; otherwise the check never ran on it. */
    let held = null;
    if (!thin && finalist?.heldScore != null)
      held = { from: r.heldBase, to: finalist.heldScore };
    else if (!thin && winner && r.winner?.n === t.n && r.heldScore != null)
      held = { from: r.heldBase, to: r.heldScore };
    else if (!thin && winner)
      held = {
        from: r.heldBase,
        to: projectedHeld(r, { n: t.n, delta: t.delta }),
        projected: true,
      };

    /* Must-pass failures that repeated — the count the result names. */
    const mp = (finalist?.mustPassFails || t.mustPassFails || []).filter(
      (f) => f.repeated,
    );
    let result;
    let tone = null;
    /* Why a candidate that passed every check wasn't kept. Held back is
       pass or fail — it never ranks the finalists, so the one that wins is
       the best on target, and a tie goes to the one found first. */
    let reason = null;
    let short = null;
    if (stage === "passed" && hasWinner && keptScore != null) {
      const mine = Math.round(t.score);
      const theirs = Math.round(keptScore);
      if (!finalist) {
        reason = `Not a finalist: only the best ${plural((r.finalists || []).length || 1, "candidate")} on target ${(r.finalists || []).length === 1 ? "is" : "are"} checked on held-back scenarios.`;
        short = "not a finalist";
      } else if (mine < theirs) {
        reason = `Lower on target than Candidate ${keptN} (${mine}% vs ${theirs}%). Held back only has to pass — it doesn't rank the finalists.`;
        short = "lower on target";
      } else if (mine === theirs && t.n > keptN) {
        reason = `Tied with Candidate ${keptN} on target (${mine}%), found after it. Held back only has to pass — it doesn't rank the finalists.`;
        short = "tied, found later";
      }
    }
    if (stage === "winner") result = "Winner";
    else if (stage === "passed")
      /* With no winner, one the held-back check never ran on says why. */
      result =
        !hasWinner && !stopped && !finalist
          ? "Not a finalist"
          : short
            ? `Passed · ${short}`
            : passedLabel({ stopped });
    else if (stage === "mustpass") {
      result = mp.length
        ? `Broke ${plural(mp.length, "must-pass scenario")}`
        : "Broke a must-pass scenario";
      tone = "broke";
    } else if (stage === "held" && finalist?.why) result = cap(finalist.why);
    else if (fail) result = shortNote(fail.note);
    else result = cap(c?.why || "Rejected");

    return {
      n: t.n,
      runId: row.runId || `${record.id}-t${t.n}`,
      name: `Candidate ${t.n}`,
      tried: row.tried || (c?.proposals || []).map((p) => p.title).join(" · "),
      score: Math.round(t.score),
      /* Points against the agent before the change, on the same scenarios. */
      delta: r.base != null ? Math.round(t.score - r.base) : null,
      held,
      finalist: !!finalist,
      stage,
      result,
      tone,
      /* The full reason, for the tooltip. */
      note: reason || fail?.note || c?.why || null,
      reason,
      proposals: c?.proposals || [],
    };
  });
};

/** The candidate rows in their groups — only the groups that have rows. */
export const candidateGroupsOf = (
  list,
  { stopped = false, hasWinner = true } = {},
) =>
  CANDIDATE_GROUPS.map((g) => ({
    ...g,
    /* No winner and not stopped: one that passed its steps never reached
       the held-back check — it wasn't a finalist. */
    label:
      g.id === "passed"
        ? !hasWinner && !stopped
          ? "Not a finalist"
          : passedLabel({ stopped })
        : g.label,
    /* No winner: the best try was stopped at the held-back check — open it. */
    open: g.open || (!hasWinner && g.id === "held"),
    rows: list
      .filter((x) => x.stage === g.id)
      .sort((a, b) => b.score - a.score || a.n - b.n),
  })).filter((g) => g.rows.length);

/** What a change touches, in a few words: its files, and how many edits. */
export const changeSummaryOf = (env, proposals = []) => {
  if (!proposals.length) return null;
  const files = [
    ...new Set(
      proposals.map((p) => {
        const path = changeFileFor(env, p)?.path;
        return path ? path.split("/").pop() : p.kind;
      }),
    ),
  ];
  const edits = plural(proposals.length, "change");
  return files.length === 1
    ? `${files[0]} only · ${edits}`
    : `${files.join(", ")} · ${edits}`;
};

/*
  The sentence that leads an epoch with a winner: which candidate, what it
  did on the held-back scenarios it never saw (an issue's fix leads with the
  issue), and whether a must-pass scenario broke. Null when there's no
  winner — the outcome's own headline says why.
*/
export const winnerSentenceOf = (outcome, result, kept) => {
  if (!kept || !result) return null;
  const parts = [];
  const issue = outcome?.issue;
  if (issue?.targets && issue.fixed)
    parts.push(
      `fixed ${issue.id} on ${issue.fixed} of ${plural(issue.targets, "failing scenario")}`,
    );
  if (result.split?.thin) {
    parts.push(`target scenarios went ${result.base}% → ${kept.score}%`);
  } else {
    const held = outcome?.heldScore ?? result.heldScore;
    if (held != null && result.heldBase != null)
      parts.push(
        `held-back scenarios it never saw ${outcome?.chosen?.projected ? "would likely go" : "went"} ${result.heldBase}% → ${held}%`,
      );
  }
  const blockers = (outcome?.checks || []).find((c) => c.id === "blockers");
  const mustPass =
    blockers?.pass === null
      ? "Must-pass scenarios run when you verify this pick."
      : blockers?.pass === false
        ? "A must-pass scenario broke."
        : "No must-pass scenario broke.";
  /* Scenarios it broke that the gate doesn't protect — the Scenarios tab
     lists them as Broke, so the verdict says so too. */
  const otherBroke =
    blockers?.pass === null ? 0 : brokeOutsideMustPassOf(result).length;
  const otherNote = otherBroke
    ? ` ${plural(otherBroke, "scenario")} that ${otherBroke === 1 ? "isn't" : "aren't"} must-pass broke.`
    : "";
  const thinNote = result.split?.thin
    ? " Too few failing scenarios to hold any back."
    : "";
  return `${kept.name}: ${parts.join("; ")}.${thinNote} ${mustPass}${otherNote}`;
};

/** The checks line: passed, unmeasured and failed, in that order. */
export const checksCountOf = (checks = []) => ({
  passed: checks.filter((c) => c.pass === true).length,
  unknown: checks.filter((c) => c.pass === null).length,
  failed: checks.filter((c) => c.pass === false),
});

/*
  The numbers behind the verdict, as a spec sheet: held back (at the pick
  and on its re-run), target, the overfit gap, cost and reply time, and
  where the candidates ran. Shown under the verdict's Show all.
*/
export const evidenceRowsOf = (outcome, result, kept, proofLine) => {
  if (!result || !kept) return [];
  const thin = !!result.split?.thin;
  const projected = !!outcome?.chosen?.projected;
  const heldNow = outcome?.heldScore ?? result.heldScore;
  const confirm = result.confirm || null;
  const signed = (n, unit) =>
    `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(Math.round(n * 10) / 10)}${unit}`;
  return [
    confirm && !projected
      ? [
          "Held back · at the pick",
          `${result.heldBase}% → ${confirm.selectionScore}%`,
        ]
      : null,
    [
      confirm && !projected ? "Held back · re-run" : "Held back",
      thin
        ? "Too few held back"
        : `${result.heldBase}% → ${heldNow}%${projected ? " · projected" : ""}${result.noise ? ` · noise ±${result.noise}` : ""}`,
    ],
    ["Target", `${result.base}% → ${kept.score}%`],
    [
      "Overfit gap",
      thin || outcome?.gap == null
        ? "—"
        : `${outcome.gap} pts · target minus held back`,
    ],
    [
      "Cost / conversation",
      outcome?.costKnown
        ? `${signed(outcome.effect.cost, "%")} vs today`
        : "Not measured",
    ],
    [
      "Reply time",
      outcome?.latencyKnown === false || !outcome?.effect
        ? "Not measured"
        : `${signed(outcome.effect.latency, "%")} vs today`,
    ],
    proofLine ? ["Proof", proofLine] : null,
  ].filter(Boolean);
};
