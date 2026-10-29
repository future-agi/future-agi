/*
  Long jobs (roadmap runs.md, Week 3 "Durability for package and training
  jobs"): package validation and training, each with its own stages and a
  stable job ID. Candidate experiments are improvements — their stages, ID
  and resume live on the Improvements page, not here. Cancel and resume happen at a
  supported boundary, and the plan says exactly what restarts: an unfinished
  rollout restarts from its seed — a restart, never a restore of memory that
  wasn't recorded. Completed trajectories, actual checkpoints and held-out
  evaluations are kept. Submitting the same job again returns the same
  handle. Retained evidence traces back to its package, model and reward.
*/
import { hashSeed } from "./runStream";
import { envOf } from "./runGroup";

export const JOB_KINDS = {
  package: {
    label: "Package validation",
    stages: [
      "Unpack",
      "Start and reset",
      "Reference run",
      "Negative controls",
      "Grader isolation",
      "Resources",
    ],
  },
  training: {
    label: "Training",
    stages: [
      "Preflight",
      "Rollouts",
      "Train",
      "Held-out evaluation",
      "Save checkpoint",
    ],
  },
};

export const JOB_STATUS = {
  queued: "Queued",
  running: "Running",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
  succeeded: "Succeeded",
  failed: "Failed",
};

/* Job status → the design system's run-status vocabulary (StatusDot). */
export const JOB_DOT = {
  queued: "queued",
  running: "running",
  cancelling: "running",
  cancelled: "cancelled",
  interrupted: "needs_input",
  succeeded: "passed",
  failed: "failed",
};

const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const jobId = (kind, key) =>
  `job_${{ package: "pkg", training: "trn" }[kind]}_${(hashSeed(key) >>> 0).toString(16).padStart(8, "0").slice(0, 8)}`;

/* Stages up to `at` done, `at` in `state`, the rest pending. */
const stagesOf = (kind, at, state = "running", details = {}) =>
  JOB_KINDS[kind].stages.map((label, i) => ({
    id: label,
    label,
    status: i < at ? "done" : i === at ? state : "pending",
    detail: details[label] || null,
  }));

const ENV_PREFERENCE = [
  "env-chat-travel",
  "env-voice-support",
  "env-clinic-line",
  "env-guest-booking",
];

/* The seeded package and training jobs, on the environments that exist. */
const seededJobs = (state) => {
  const envs = ENV_PREFERENCE.map((id) => envOf(state, id)).filter(Boolean);
  const fallback = Object.keys(state?.byEnv || {})
    .map((id) => envOf(state, id))
    .filter(Boolean);
  const [a, b] = envs.length >= 2 ? envs : [...envs, ...fallback].slice(0, 2);
  if (!a) return [];
  const second = b || a;
  const release = (e) => e?.release || "2026.10.1";
  return [
    {
      id: jobId("package", `${a.id}:pkg`),
      kind: "package",
      name: `Package ${release(a)}`,
      envId: a.id,
      status: "succeeded",
      submittedAt: ago(60 * 26),
      finishedAt: ago(60 * 25),
      by: "Vel",
      client: "CLI",
      idempotencyKey: `pkg-${a.id}-${release(a)}`,
      stages: stagesOf("package", 6, "done", {
        "Reference run": "Reference agent passed 12 of 12",
        "Negative controls": "4 of 4 broken agents caught",
        "Grader isolation": "Graders not readable from the agent's tools",
        Resources: "2 vCPU · 4 GB · 90 s reset",
      }),
      work: { done: 12, total: 12, unit: "reference trials" },
      checkpoints: [],
      heldOut: null,
      lineage: {
        package: `pkg_${a.id.replace(/^env-/, "")}@${release(a)}`,
        model: "Reference agent (fixed)",
        reward: "Graders v3",
      },
    },
    {
      id: jobId("package", `${second.id}:pkg`),
      kind: "package",
      name: `Package ${release(second)}`,
      envId: second.id,
      status: "failed",
      submittedAt: ago(60 * 5),
      finishedAt: ago(60 * 5 - 6),
      by: "Vel",
      client: "Console",
      idempotencyKey: `pkg-${second.id}-${release(second)}`,
      stages: stagesOf("package", 3, "failed", {
        "Reference run": "Reference agent passed 10 of 10",
        "Negative controls":
          "1 of 4 broken agents passed — the grader accepts a refund with no lookup",
      }),
      work: { done: 13, total: 14, unit: "checks" },
      checkpoints: [],
      heldOut: null,
      lineage: {
        package: `pkg_${second.id.replace(/^env-/, "")}@${release(second)}`,
        model: "Reference agent (fixed)",
        reward: "Graders v2",
      },
    },
    {
      id: jobId("training", `${a.id}:train-1`),
      kind: "training",
      name: `GRPO on qwen2.5-7b-instruct`,
      envId: a.id,
      status: "running",
      submittedAt: ago(140),
      by: "Vel",
      client: "Python",
      idempotencyKey: `train-${a.id}-grpo-1`,
      stages: stagesOf("training", 2, "running", {
        Preflight:
          "Model, interaction mode, data fields and checkpointing supported",
        Rollouts: "400 of 400 rollouts",
        Train: "Step 240 of 400",
      }),
      work: { done: 240, total: 400, unit: "steps" },
      checkpoints: [
        {
          id: "ckpt-120",
          step: 120,
          at: ago(95),
          kind: "complete",
          restorable: true,
        },
        {
          id: "ckpt-240",
          step: 240,
          at: ago(12),
          kind: "complete",
          restorable: true,
        },
      ],
      heldOut: { done: 2, total: 3 },
      lineage: {
        package: `pkg_${a.id.replace(/^env-/, "")}@${release(a)}`,
        model: "qwen2.5-7b-instruct (base)",
        reward: "Reward v3 · task success + policy adherence",
      },
    },
    {
      id: jobId("training", `${a.id}:train-2`),
      kind: "training",
      name: `DPO on qwen2.5-7b-instruct`,
      envId: a.id,
      status: "interrupted",
      submittedAt: ago(70),
      by: "Vel",
      client: "CLI",
      idempotencyKey: `train-${a.id}-dpo-1`,
      /* The watcher was lost; the job itself kept going. */
      recovery: { kind: "worker-lost", at: ago(8), stillRunning: true },
      stages: stagesOf("training", 2, "interrupted", {
        Preflight:
          "Model, interaction mode, data fields and checkpointing supported",
        Rollouts: "400 of 400 rollouts",
        Train: "Step 95 of 200 when the watcher was lost — still training",
      }),
      work: { done: 95, total: 200, unit: "steps" },
      checkpoints: [
        {
          id: "ckpt-80",
          step: 80,
          at: ago(15),
          kind: "complete",
          restorable: true,
        },
      ],
      heldOut: { done: 0, total: 3 },
      lineage: {
        package: `pkg_${a.id.replace(/^env-/, "")}@${release(a)}`,
        model: "qwen2.5-7b-instruct (base)",
        reward: "Reward v3 · task success + policy adherence",
      },
    },
    {
      id: jobId("training", `${second.id}:train-1`),
      kind: "training",
      name: `SFT on llama-3.1-8b-instruct`,
      envId: second.id,
      status: "interrupted",
      submittedAt: ago(60 * 9),
      by: "Vel",
      client: "CLI",
      idempotencyKey: `train-${second.id}-sft-1`,
      recovery: { kind: "worker-lost", at: ago(60 * 7), stillRunning: false },
      stages: stagesOf("training", 1, "interrupted", {
        Preflight:
          "Model, interaction mode, data fields and checkpointing supported",
        Rollouts: "Worker lost at 312 of 400 rollouts",
      }),
      work: { done: 312, total: 400, unit: "rollouts" },
      checkpoints: [
        {
          id: "ckpt-r312",
          step: 312,
          at: ago(60 * 7),
          kind: "partial",
          restorable: false,
          note: "Rollouts 1–312 are kept as trajectories. Nothing was trained yet, so there's no model state to restore.",
        },
      ],
      heldOut: { done: 0, total: 3 },
      lineage: {
        package: `pkg_${second.id.replace(/^env-/, "")}@${release(second)}`,
        model: "llama-3.1-8b-instruct (base)",
        reward: "Reward v2 · task success",
      },
    },
  ];
};

/** Every job, with what this workspace has done to it since (cancel, resume, resubmits). */
export const jobsOf = (state) => {
  const overrides = state?.workspaceSettings?.jobs || {};
  return seededJobs(state).map((j) => {
    const o = overrides[j.id];
    if (!o) return j;
    const stages =
      o.status === "running" && j.status !== "running"
        ? j.stages.map((s) =>
            ["interrupted", "cancelled"].includes(s.status)
              ? { ...s, status: "running" }
              : s,
          )
        : o.status === "cancelled"
          ? j.stages.map((s) =>
              s.status === "running" ? { ...s, status: "cancelled" } : s,
            )
          : j.stages;
    return {
      ...j,
      ...o,
      stages,
      recovery: o.status === "running" ? null : j.recovery,
    };
  });
};

export const currentStageOf = (job) =>
  job.stages.find((s) => !["done", "pending"].includes(s.status)) ||
  [...job.stages].reverse().find((s) => s.status === "done") ||
  job.stages[0];

/** What cancelling keeps, and what a later resume has to restart. */
export const cancelPlanOf = (job) => {
  const last = [...(job.checkpoints || [])]
    .reverse()
    .find((c) => c.kind === "complete");
  return {
    boundary:
      job.kind === "training"
        ? "the end of the current training step"
        : "the end of the current check",
    kept: [
      `${job.work.done} of ${job.work.total} ${job.work.unit} completed`,
      last && `checkpoint ${last.id} (step ${last.step})`,
      job.heldOut?.done
        ? `${job.heldOut.done} of ${job.heldOut.total} held-out evaluations`
        : null,
    ].filter(Boolean),
    lost: "Work in flight at that point isn't kept; a resume restarts it from its seed.",
  };
};

/** Exactly what a resume restores and what it restarts. */
export const resumePlanOf = (job) => {
  const restore = [...(job.checkpoints || [])]
    .reverse()
    .find((c) => c.restorable);
  const remaining = Math.max(0, job.work.total - job.work.done);
  const partial = (job.checkpoints || []).find((c) => c.kind === "partial");
  return {
    restore: restore
      ? `Restores ${restore.id} — the model state saved at step ${restore.step}.`
      : null,
    noRestore: restore
      ? null
      : job.restoreBlocked ||
        partial?.note ||
        "No checkpoint can be restored for this job.",
    restart: remaining
      ? `${remaining} ${job.work.unit} restart from their seed — a restart, not a restore of anything unrecorded.`
      : null,
    kept: `${job.work.done} completed ${job.work.unit} are kept and not repeated.`,
  };
};

export const jobCli = {
  status: (j) =>
    j.kind === "training"
      ? `agent-learn train status ${j.id}`
      : `agent-learn status ${j.id}`,
  cancel: (j) =>
    j.kind === "training"
      ? `agent-learn train cancel ${j.id}`
      : `agent-learn cancel ${j.id} --reason "…"`,
  resume: (j) =>
    j.kind === "training"
      ? `agent-learn train resume ${j.id}`
      : `agent-learn resume ${j.id}`,
  submit: (j) =>
    `${j.kind === "training" ? "agent-learn train start" : "agent-learn world validate ."} --idempotency-key ${j.idempotencyKey}`,
};
