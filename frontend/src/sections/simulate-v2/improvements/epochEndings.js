/*
  How an epoch that ended early reads, in one sentence each — the copy
  EpochEnding shows. The way on (its one primary and links) is wired where
  the actions live, on the epoch page.

  Endings: stopped (by a person), failed (stopped on our side — C11's words:
  neutral, never the agent's fault), no-fix, not-reproduced, too-few,
  nothing (every scenario already meets the goal), over-budget (today's
  agent can't be measured inside the budget).
*/
import { OPT_STATUS, REPEATS } from "../_mock/optimizationRuns";
import { issueDraftsOf } from "../run/fixmyagent/improvementView";
import { creditsWord } from "./improvementRunsData";
import { ENDING_WORD } from "./improvementRows";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const cap = (s) => (s ? `${s.charAt(0).toUpperCase()}${s.slice(1)}` : s);

/** Which early ending this is, or null for one with a result to read. */
export const earlyEndingOf = (record, outcome) => {
  const key = outcome?.ending?.key;
  if (record?.status === OPT_STATUS.OVER_BUDGET) return "over-budget";
  if (
    [
      "stopped",
      "failed",
      "no-fix",
      "not-reproduced",
      "too-few",
      "nothing",
    ].includes(key)
  )
    return key;
  return null;
};

/** Endings that have candidates to list (tabs below the ending). */
export const ENDINGS_WITH_CANDIDATES = new Set(["stopped", "no-fix"]);

/**
 * { label, sentence, spent, danger } for an early ending. `name` is the
 * epoch's ("Epoch 5"), `credits` what it spent (epochSpendOf).
 */
export const endingCopyOf = (ending, { record, outcome, name, credits }) => {
  const spentWord = creditsWord(credits);
  const issueId = record?.trigger?.issueId || null;
  if (ending === "failed") {
    const yours = outcome?.reason?.key === "yours";
    return {
      label: `${name} · ${ENDING_WORD.failed}`,
      sentence: yours
        ? `It couldn't run: ${record?.infra?.reason || "something only you can change"}. Your agent did not change.`
        : "An issue occurred while running it. Your agent did not change, and this doesn't count against it.",
      spent: [
        `Spent ${spentWord} before it stopped.`,
        outcome?.canRetry === false
          ? "It was already retried once — Run Epoch again starts a new one."
          : null,
      ]
        .filter(Boolean)
        .join(" "),
    };
  }
  if (ending === "stopped") {
    const s = record?.stoppedBy || {};
    return {
      label: `${name} · ${ENDING_WORD.stopped}`,
      sentence: s.trials
        ? `Stopped after ${s.trials} of up to ${plural(s.of, "candidate")}. ${s.finalists ? `${plural(s.finalists, "finalist")} had been checked on held-back scenarios, but the check didn't finish` : "None was checked on held-back scenarios"}, so nothing can be chosen and your agent did not change.`
        : "Stopped before it tried a change. Your agent did not change.",
      spent: `Spent ${spentWord} before it stopped.`,
    };
  }
  if (ending === "no-fix") {
    const broke = (outcome?.checks || []).some(
      (c) => c.id === "blockers" && c.pass === false,
    );
    return {
      label: `${name} · ${ENDING_WORD["no-fix"]}`,
      danger: broke,
      sentence: `${String(
        outcome?.headline || "No change it tried was safe to ship.",
      )
        .replace(/^No fix for /, "No safe change for ")
        .replace(/\bbest trial\b/g, "best candidate")
        .replace(/\btrials\b/g, "candidates")} Your agent did not change.`,
      spent: `Spent ${spentWord}.`,
    };
  }
  if (ending === "not-reproduced") {
    const drafts = issueDraftsOf(record);
    const per = record?.reproduce?.perScenario || {};
    const inconclusive = drafts.filter(
      (d) => per[d.id]?.state === "inconclusive",
    ).length;
    const version = record?.baseAgentVersion || "today's version";
    return {
      label: `${name} · ${ENDING_WORD["not-reproduced"]}`,
      sentence: `${issueId || "The issue"} didn't happen on ${version}: its ${plural(drafts.length || record?.reproduce?.total || 0, "scenario")} ran ${REPEATS} times each and none failed reliably${inconclusive ? ` (${inconclusive} failed once, which can be chance)` : ""}, so no change was tried.`,
      spent: `Spent ${spentWord} on this check.`,
    };
  }
  if (ending === "too-few") {
    const n = record?.tooFew?.failing ?? 1;
    const total =
      record?.tooFew?.total || record?.reproduce?.total || Math.max(n, 1);
    return {
      label: `${name} · ${ENDING_WORD["too-few"]}`,
      sentence: `${n === 0 ? "None" : `Only ${n}`} of ${plural(total, "scenario")} from ${issueId || "the issue"} fail${n === 1 ? "s" : ""} today — not enough to search on, so nothing was tried. Add scenarios that fail the same way, then run the epoch again.`,
      spent: `Spent ${spentWord} reproducing it.`,
    };
  }
  if (ending === "nothing") {
    return {
      label: `${name} · ${ENDING_WORD.nothing}`,
      sentence: `${cap(String(outcome?.headline || "").replace(/^(Could not reproduce|Nothing to improve) — /, "")) || "Every scenario already meets the goal."} Nothing was tried. Pick another goal, or add harder scenarios for this one.`,
      spent: `Spent ${spentWord} measuring it.`,
    };
  }
  if (ending === "over-budget") {
    return {
      label: `${name} · ${ENDING_WORD["over-budget"]}`,
      sentence: `${cap(String(outcome?.headline || "").replace(/^Stopped at budget — /, ""))} Nothing ran past the budget.`,
      spent: `Spent ${spentWord} measuring it.`,
    };
  }
  return null;
};
