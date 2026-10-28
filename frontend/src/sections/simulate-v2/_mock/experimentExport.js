/*
  An improvement as one file someone can keep, share or reproduce from: the
  baseline it measured against, the environment version and scenarios it was
  frozen on, the changes it could try (with any wording a person edited), its
  trials and finalists, how it ended and what it spent. Nothing secret goes
  in — no credentials, keys or call recordings; scenarios are named by id and
  title, not their transcripts.
*/
import { improvementOutcome, improvementSpendParts } from "./optimizationRuns";
import { creditsForUsd } from "./metering";

const scenario = (t) =>
  t && { id: t.id, title: t.title || null, critical: !!t.critical };

export const experimentExportOf = (record, env, now = new Date()) => {
  const r = record?.result || record?.pendingResult || null;
  const outcome = improvementOutcome(record);
  return {
    format: "future-agi.improvement-experiment/1",
    exportedAt: now.toISOString(),
    improvement: {
      id: record.id,
      name: record.name || null,
      status: outcome?.ending?.label || record.status,
      headline: outcome?.headline || null,
      createdAt: record.createdAt || null,
      completedAt: record.completedAt || null,
      startedFrom: record.trigger || null,
    },
    agent: { version: record.baseAgentVersion || null },
    environment: {
      id: env?.id || null,
      name: env?.name || null,
      version: record.snapshot?.label || record.frozen?.snapshot?.label || null,
    },
    limits: record.limits || null,
    budget: record.budget ?? null,
    baseline: {
      run: record.fromRunId || null,
      measured: r?.base ?? r?.heldBase ?? null,
      calibration: r?.calibration || null,
    },
    scenarios: r
      ? {
          target: (r.trainMeasuredTasks || []).map(scenario),
          heldBack: (r.heldTasks || []).map(scenario),
          mustPass: r.mustPass || null,
        }
      : null,
    changes: (r?.pool || []).map((p) => ({
      id: p.id,
      kind: p.kind,
      title: p.title,
      why: p.why || null,
      diff: p.diff || [],
      ...(p.edited && { editedBy: "person" }),
    })),
    trials: (r?.trials || []).map((t) => ({
      n: t.n,
      changes: t.picks || [],
      score: t.score ?? null,
      delta: t.delta ?? null,
      fixed: t.fixed ?? null,
      broke: t.broke ?? null,
    })),
    finalists: (r?.finalists || []).map((f) => ({
      n: f.n,
      score: f.score ?? null,
      heldBack: f.heldScore ?? null,
      accepted: !!f.accepted,
      why: f.why || null,
    })),
    winner: r?.winner?.n ?? null,
    chosenByPerson: record.preferredTrial ?? null,
    ...(record.stoppedBy && { stopped: record.stoppedBy }),
    spend: improvementSpendParts(record, env?.surface).map((p) => ({
      part: p.label,
      detail: p.detail,
      conversations: p.conversations,
      credits: creditsForUsd(p.usd),
    })),
  };
};

/* Saves it as a .json file in the browser. */
export const downloadExperiment = (record, env) => {
  const data = experimentExportOf(record, env);
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${env?.id || "environment"}-${record.id}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
