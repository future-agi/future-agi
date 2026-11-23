/*
  What the analyzer stores about an issue and each of its conversations,
  read into the shapes the Overview shows. Nothing here runs an analysis:
  the Overview only shows what is already saved.

  Per issue: the likely cause, the suggested change and what would prove that
  change wrong (description · fix_hypothesis · falsifier).
  Per conversation: outcome, recovered, failure type, the requirement
  checklist, and three evidence steps: where the problem starts (origin),
  where it goes wrong (decisive) and where it shows (symptom).
*/

const sentence = (t) => (t ? `${t.charAt(0).toUpperCase()}${t.slice(1)}` : t);

/* The issue's root cause, as key-value rows. Missing rows are left out. */
export const rootCauseRowsOf = (issue) => {
  const fix = issue?.suggested_fix || {};
  const cause = fix.cause || issue?.why_shown || null;
  return [
    { key: "cause", label: "Likely cause", text: cause },
    { key: "change", label: "Suggested change", text: fix.change || null },
    { key: "wrongIf", label: "Wrong if", text: fix.wrongIf || null },
  ]
    .filter((r) => r.text)
    .map((r) => ({ ...r, text: sentence(r.text) }));
};

/* Step-role labels, in reading order. */
export const STEP_ROLES = [
  { key: "origin", label: "Starts" },
  { key: "decisive", label: "Goes wrong" },
  { key: "symptom", label: "Shows" },
];

/*
  The three evidence steps of one conversation, as indexes into its failing
  reel. Stored spans win (evidence.origin_step / decisive_step /
  symptom_step); otherwise: the decisive step is the one marked as the
  failure, the origin is the agent or tool step just before it, and the
  symptom is the last step after it.
*/
export const evidenceStepsOf = (trace) => {
  const ev = trace?.evidence || {};
  const reel = ev.fail_reel || [];
  if (!reel.length) return [];
  const valid = (i) => Number.isInteger(i) && i >= 0 && i < reel.length;
  const failAt = reel.findIndex((s) => s.isFailure || s.status === "fail");
  const decisive = valid(ev.decisive_step) ? ev.decisive_step : failAt;
  if (decisive < 0) return [];
  let origin = valid(ev.origin_step) ? ev.origin_step : -1;
  if (origin < 0) {
    for (let i = decisive - 1; i >= 0; i -= 1) {
      if (reel[i].label !== "User") {
        origin = i;
        break;
      }
    }
  }
  const symptom = valid(ev.symptom_step)
    ? ev.symptom_step
    : decisive < reel.length - 1
      ? reel.length - 1
      : -1;
  const at = { origin, decisive, symptom };
  return STEP_ROLES.filter((r) => at[r.key] >= 0).map((r) => ({
    ...r,
    index: at[r.key],
    step: reel[at[r.key]],
  }));
};

export const OUTCOME = {
  succeeded: { label: "Succeeded", color: "status.success.fg" },
  failed: { label: "Failed", color: "status.danger.fg" },
  unclear: { label: "Unclear", color: "text.secondary" },
};

export const CHECK = {
  met: {
    icon: "solar:check-circle-linear",
    color: "status.success.fg",
    label: "Done",
  },
  broken: {
    icon: "solar:close-circle-linear",
    color: "status.danger.fg",
    label: "Broken",
  },
  unclear: {
    icon: "solar:question-circle-linear",
    color: "text.tertiary",
    label: "Unclear",
  },
};

/* "3 of 5 done · 1 broken · 1 unclear" */
export const checklistTally = (list = []) => {
  const n = (k) => list.filter((c) => c.status === k).length;
  return [
    `${n("met")} of ${list.length} done`,
    n("broken") ? `${n("broken")} broken` : null,
    n("unclear") ? `${n("unclear")} unclear` : null,
  ]
    .filter(Boolean)
    .join(" · ");
};

/* Outcome · recovered · failure type, as short labels. */
export const conversationLabelsOf = (trace) =>
  [
    trace?.recovered == null
      ? null
      : trace.recovered
        ? "Recovered"
        : "Not recovered",
    trace?.failure_type || null,
  ].filter(Boolean);

/* Scroll the evidence reel to one step and flash it. */
export const goToStep = (i) => {
  const el = document.querySelector(`[data-reel-step="${i}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.animate?.([{ opacity: 0.4 }, { opacity: 1 }], { duration: 600 });
};
