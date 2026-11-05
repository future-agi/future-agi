/**
 * The full build pipeline.
 *
 * The header's four milestones — Agent, Contract, Environment, Scenarios — are
 * the coarse view. Underneath, the builder runs twelve steps against the
 * source, then the first run has four more. That detail used to live only in
 * the streaming chat and vanished with each new message, so "what happened
 * behind Environment being ticked green" had no answer once the chat scrolled.
 *
 * This is the same list rendered as data, grouped by milestone. The status
 * of each step is derived from the `done` set the builder already publishes —
 * one source of truth, so the popover and the header cannot drift.
 */

import {
  agentNeedsRuntime,
  definitionOf,
  sourceKindOf,
} from "./agentRegistry";
import { checksConnection, connectionStateOf } from "./connectionCheck";
import { credentialCheck } from "../agents/connect/preflight";

export const BUILD_PIPELINE = [
  {
    id: "understand",
    phase: "setup",
    milestone: "understand",
    label: "Understanding agent",
    detail: "Reading tools, prompts, guardrails and data from the source",
  },
  {
    id: "generate-env",
    phase: "setup",
    milestone: "build",
    label: "Generating environment",
    detail: "Deriving the world the tools act on",
  },
  {
    id: "build-env",
    phase: "setup",
    milestone: "build",
    label: "Building environment",
    detail: "Writing handlers so every tool call hits real state",
  },
  {
    id: "validate-env",
    phase: "setup",
    milestone: "build",
    label: "Validating environment",
    detail: "Every tool answered truthfully, including a truthful refusal",
  },
  {
    id: "generate-data",
    phase: "setup",
    milestone: "build",
    label: "Generating data",
    detail: "Seeding the awkward rows the use cases actually need",
  },
  {
    id: "generate-scenarios",
    phase: "setup",
    milestone: "scenarios",
    label: "Generating scenarios",
    detail: "Drafting one scenario per real use case, with sub-goals",
  },
  {
    id: "validate-scenarios",
    phase: "setup",
    milestone: "scenarios",
    label: "Validating scenarios",
    detail: "Three gates: ready, solvable, not vacuous — kept only if all three pass",
  },
  {
    id: "connect-agent",
    phase: "run",
    milestone: "agent",
    label: "Connecting agent",
    detail: "Wiring the shadow agent to the sandbox for the first run",
  },
  {
    id: "run",
    phase: "run",
    milestone: "agent",
    label: "Running",
    detail: "Playing the scenarios against the agent, one conversation at a time",
  },
  {
    id: "grade",
    phase: "run",
    milestone: "agent",
    label: "Grading",
    detail: "Settling every check from world state and tool logs",
  },
  {
    id: "upload",
    phase: "run",
    milestone: "agent",
    label: "Uploading artifacts",
    detail: "Persisting traces, transcripts, tool logs and eval results",
  },
  {
    id: "completed",
    phase: "run",
    milestone: "agent",
    label: "Completed",
    detail: "The first run is on record and the environment is ready to iterate",
  },
];

/**
 * Compute the pipeline's live status.
 *
 * `done` is the builder's own set of milestone ids, so the mapping is direct:
 * a milestone being complete implies every step under it is complete. The
 * first pending step becomes the running one only while `running` is true;
 * otherwise it stays "pending" so a paused pipeline does not look like a
 * failed one.
 */
/**
 * Live pipeline status.
 *
 * `failure` is optional: `{ stepId, title, detail, retryable }`. When present,
 * that step lands as "failed" and every step after it stays pending — a
 * failure halts the pipeline, so pretending the next step is "up next" would
 * be misleading. The row can be expanded in the popover to see what went
 * wrong and to retry.
 */
export const pipelineStatus = (done = [], running = false, currentPhase = "setup", failure = null) => {
  const doneSet = new Set(done);
  let seenPending = false;
  let halted = false;

  return BUILD_PIPELINE.map((step) => {
    if (failure && failure.stepId === step.id) {
      halted = true;
      return { ...step, status: "failed", failure };
    }
    /* Steps in the "run" phase only start once we actually kick off a run;
       until then they stay pending regardless of milestone completion. */
    const skip = step.phase === "run" && currentPhase !== "run";
    const complete = !skip && doneSet.has(step.milestone);
    let status = complete ? "done" : "pending";

    if (halted) return { ...step, status: "pending" };
    if (!complete && !seenPending) {
      status = running ? "running" : "pending";
      seenPending = true;
    }
    return { ...step, status };
  });
};

/*
  Validating the environment (builder.md step 4): a failed check keeps the
  design in Needs repair — the draft stays, the failure and the scenarios it
  touches are shown, and the person chooses to repair or remove the
  requirement. Two ways to land here: a seeded table whose `refs` name a
  table the world doesn't have, or the demo switch `?build=fk`.
*/
const DEMO_PARENT = "accounts";

const stemOf = (name) => String(name).replace(/ies$/, "y").replace(/s$/, "");

/**
 * The first foreign-key failure in the world's seed, else null:
 * { table, column, missing, check, orphanRows, demo? }.
 */
export const validateWorld = (env, demo = null) => {
  const tables = env?.seed?.tables || [];
  const names = new Set(tables.map((t) => t.name));
  let bad = null;
  tables.forEach((t) =>
    (t.refs || []).forEach((r) => {
      if (!bad && !names.has(r)) bad = { table: t, missing: r };
    }),
  );
  /* The demo switch: the busiest table points at a table nobody seeded. */
  if (!bad && demo === "fk" && tables.length && !names.has(DEMO_PARENT)) {
    const child = [...tables].sort((a, b) => (b.rows || 0) - (a.rows || 0))[0];
    bad = { table: child, missing: DEMO_PARENT, demo: true };
  }
  if (!bad) return null;
  const column = `${stemOf(bad.missing)}_id`;
  return {
    table: bad.table.name,
    column,
    missing: bad.missing,
    check: `${bad.table.name}.${column} → ${bad.missing}.id`,
    orphanRows: Math.max(1, Math.round((bad.table.rows || 0) * 0.12)),
    demo: !!bad.demo,
  };
};

/** The scenarios that read the failing table: [{ id, title }], up to `max`. */
export const affectedScenarios = (failure, rows = [], max = 40) => {
  if (!failure) return [];
  const re = new RegExp(`\\b${stemOf(failure.table)}`, "i");
  const text = (r) =>
    [r.title, r.name, r.summary, r.situation, r.task].filter(Boolean).join(" ");
  const hit = rows.filter((r) => re.test(text(r)));
  /* None names it outright: the first few cases read it through a tool. */
  return (hit.length ? hit : rows.slice(0, 3)).slice(0, max).map((r, i) => ({
    id: r.id || `s${i}`,
    title: r.title || r.name || r.summary || r.situation || "Untitled scenario",
  }));
};

/*
  What stops a build from this agent version (builder.md, build from an
  existing Agent version, step 1: "Missing archive, unavailable secrets or a
  version not ready for use: show that blocker before starting a build").
  Each blocker names the problem and its fix. "blocked" stops the build; a
  "note" is worth knowing but doesn't — an untested connection isn't needed
  to read the stored source, only to run. `?blocked=secret|test|archive`
  shows each one in the demo.
*/
export const buildBlockersOf = (agent) => {
  if (!agent) return [];
  const v = agent.values || {};
  const d = definitionOf(agent);
  const kind = sourceKindOf(agent);
  const demo =
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("blocked");
  const out = [];
  const add = (id, severity, title, fix) =>
    out.push({ id, severity, title, fix });
  if (demo === "archive" || (kind === "upload" && !d.bundle))
    add(
      "archive",
      "blocked",
      "The source archive is missing",
      "Upload it again on the agent's Connections tab; the build reads that exact archive.",
    );
  else if (kind === "repo" && !d.repoUrl)
    add(
      "archive",
      "blocked",
      "No repository is recorded for this version",
      "Add the repository on the agent's Connections tab.",
    );
  if (agentNeedsRuntime(agent))
    add(
      "runtime",
      "blocked",
      "Nowhere to reach it yet",
      "Add where the agent runs on its Connections tab.",
    );
  const key = v.apiKeyRef ? credentialCheck({ ref: v.apiKeyRef }) : null;
  const token = v.tokenRef ? credentialCheck({ ref: v.tokenRef }) : null;
  const bad = [key, token].find((c) => c && !c.ok);
  if (demo === "secret" || bad)
    add(
      "secret",
      "blocked",
      demo === "secret" || bad.state === "failed"
        ? "A secret it uses is unavailable"
        : "A secret it needs isn't set",
      "Point the reference at a current key on the agent's Connections tab.",
    );
  else if (kind === "platform" && !v.hasApiKey && !v.apiKeyRef)
    add(
      "secret",
      "blocked",
      "Its API key isn't set",
      "Add the key on the agent's Connections tab.",
    );
  if (checksConnection(agent) || demo === "test") {
    const c = connectionStateOf(agent);
    if (demo === "test" || c.state === "attention")
      add(
        "test",
        "blocked",
        "Its connection test failed",
        "Fix what the test found and test again on the Connections tab.",
      );
    else if (c.label === "Test failed")
      add(
        "test",
        "blocked",
        "Its last connection test failed",
        "Fix what it found and test again on the Connections tab.",
      );
    else if (c.state === "draft")
      add(
        "test",
        "note",
        "The connection isn't tested yet",
        "Building reads the stored source, so it can start. Test the connection before the first run.",
      );
  }
  return out;
};
export const pipelineSummary = (steps) => {
  const done = steps.filter((s) => s.status === "done").length;
  const running = steps.some((s) => s.status === "running");
  const failed = steps.find((s) => s.status === "failed") || null;
  return {
    done,
    total: steps.length,
    running,
    failed,
    label: failed
      ? failed.failure?.needsRepair
        ? `Needs repair at ${failed.label.toLowerCase()}`
        : `Failed at ${failed.label.toLowerCase()}`
      : running
        ? `Running · ${done} of ${steps.length}`
        : `${done} of ${steps.length} complete`,
  };
};
