// W6 C6: the Optimization detail's "Trials are runs" panel, pure. Reads the
// detail read's `w6` block (handlers/w6-runs.js w6DetailOf in prototype
// mode): the experiment the trials ran in, the baseline run and one run per
// trial with its score and the best score so far.

/** Chart points: Baseline then every trial (score and best so far). */
export function chartPoints(w6) {
  if (!w6?.baseline) return [];
  const pct = (v) => (v == null ? null : Math.round(v * 1000) / 10);
  return [
    {
      name: "Baseline",
      label: w6.baseline.label,
      score: pct(w6.baseline.score),
      best: pct(w6.baseline.score),
    },
    ...(w6.trials ?? []).map((t) => ({
      name: `Trial ${t.trial}`,
      label: t.label,
      score: pct(t.score),
      best: pct(t.best_so_far),
    })),
  ];
}

/** The best trial ({trial, run_id, …}) or null when none beats the baseline. */
export function bestTrialOf(w6) {
  const trials = w6?.trials ?? [];
  const best = trials.find((t) => t.is_best) ?? null;
  if (!best || best.score <= (w6?.baseline?.score ?? -Infinity)) return null;
  return best;
}

/** "5 of 5 trials" / "3 of 11 trials · stopped: …". */
export function budgetLine(w6) {
  const n = w6?.trials?.length ?? 0;
  const head = `${n} of ${w6?.budget ?? n} trial${(w6?.budget ?? n) === 1 ? "" : "s"} on ${w6?.rows ?? 0} rows`;
  return w6?.stop_reason ? `${head} · stopped: ${w6.stop_reason}` : head;
}

/** "Derived from 7 failing rows (row 12: “…”)" for one lesson. */
export function lessonLine(lesson) {
  const ex = lesson?.example;
  const rows = `${lesson?.rows ?? 0} failing row${lesson?.rows === 1 ? "" : "s"}`;
  return ex?.reason
    ? `${lesson.text} · from ${rows} (row ${ex.row}: “${ex.reason}”)`
    : `${lesson?.text ?? ""} · from ${rows}`;
}

/**
 * The publish-and-pin requests of "Promote best": a draft of the prompt
 * with the trial's text (add-new-draft), its commit (a new Prompts
 * version) and the dataset baseline pin on the trial's run.
 */
export function promoteBodies(w6, trial, trialPrompt, optimizationName) {
  const p = w6?.prompt ?? {};
  return {
    draft: {
      new_prompts: [
        {
          prompt_config: [
            {
              messages: [
                { role: "system", content: trialPrompt },
                ...(p.user ? [{ role: "user", content: p.user }] : []),
              ],
              configuration: { model: p.model },
            },
          ],
          variable_names: {},
        },
      ],
    },
    commit: (versionName) => ({
      version_name: versionName,
      message: `${optimizationName} · Trial ${trial.trial} (${trial.label})`,
    }),
    baseline: { run_id: trial.run_id },
  };
}

/** The new version's name from the add-new-draft response. */
export const draftVersionOf = (response) => {
  const list = response?.data?.result ?? response?.data ?? [];
  const first = Array.isArray(list) ? list[0] : list;
  return first?.template_version ?? null;
};
