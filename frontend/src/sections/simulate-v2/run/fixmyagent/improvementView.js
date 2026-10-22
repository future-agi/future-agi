/*
  Small readings the improvement page needs that aren't the engine's: the
  prompt methods in plain words, what a coding agent's brief may carry (only
  what the search could see, with real excerpts), and approving a
  contract-changing candidate — a new environment version, the baseline run
  again on it, and only then the comparison.
*/
import { hashSeed } from "../../_mock/runStream";
import { environmentVersions, nextEnvVersion } from "../../_mock/versions";
import { currentEnvSnapshot, nextSnapshotLabel } from "../../_mock/envSnapshot";
import { passShareOf } from "../../_mock/optimize";
import { improvementTotalSpend } from "../../_mock/optimizationRuns";
import { issueFixRecord } from "../../errorFeed/issueFixModel";
import { mockCluster } from "src/api/errorFeed/mockAdapter";
import { scenarioTitle } from "../../_mock/scenarios";

/* The prompt methods, plain words first; the optimiser's name only in the
   advanced panel. */
export const METHODS = [
  { id: "gepa", label: "Reflective", note: "Reads the failed conversations and keeps the best change for each scenario" },
  { id: "protegi", label: "Gradient", note: "Reads what failed and edits toward it, a few causes at a time" },
  { id: "metaprompt", label: "Rewrite", note: "A stronger model rewrites the prompt each round — the cheapest" },
  { id: "promptwizard", label: "Explore", note: "Mutate, critique, refine — strays furthest from today's prompt" },
  { id: "bayesian", label: "Few-shot", note: "Searches examples and phrasing — needs more runs to win" },
  { id: "random_search", label: "Random baseline", note: "Simple variations — the baseline the others have to beat" },
];
export const DEFAULT_METHOD = "gepa";
export const methodLabelOf = (id) => METHODS.find((m) => m.id === id)?.label || "Reflective";

/**
 * The scenarios a coding agent's brief lists as still failing — the visible
 * (training) ones only. The held-back set never leaves Future AGI.
 */
export const visibleFailingOf = (result, winner, tasks = []) => {
  const byId = new Map((tasks || []).map((t) => [t.id, t]));
  return (result?.trainMeasuredTasks || []).filter((t) => {
    if (winner) return ["still-failing", "flaky", "inconclusive"].includes(winner.perScenario?.[t.id]);
    const x = byId.get(t.id);
    return x ? x.status !== "unmeasured" && passShareOf(x) < 1 : !!t.before && t.before !== "passed";
  });
};

const clip = (s, n = 140) => {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
};

/* The turn a failing scenario went wrong on, with what the caller said before it. */
const failingTurnOf = (task) => {
  const steps = Array.isArray(task?.steps) ? task.steps : [];
  const i = steps.findIndex((s) => s.failedHere || s.crux);
  if (i < 0) return null;
  const agent = steps[i];
  const caller = [...steps.slice(0, i)].reverse().find((s) => s.role !== "agent");
  return `${caller ? `Caller: “${clip(caller.text, 110)}” — ` : ""}Agent: “${clip(agent.failText || agent.text, 120)}”`;
};

/**
 * Evidence for the brief: three to five real excerpts — the issue's root
 * cause and its traces when it came from the Error Feed, then the failing
 * turns of the visible scenarios that still fail. Never a proposal's reason.
 */
export const briefEvidenceOf = ({ record, result, winner, tasks = [] }) => {
  const out = [];
  const snap = record?.snapshot;
  if (snap?.rootCause) out.push({ source: `Error Feed · ${snap.issueId}`, text: clip(snap.rootCause, 220) });
  const byId = new Map([...(tasks || []), ...(record?.issueScenarios || [])].map((t) => [t.id, t]));
  visibleFailingOf(result, winner, tasks).forEach((t) => {
    if (out.length >= 5) return;
    const turn = failingTurnOf(byId.get(t.id));
    if (turn) out.push({ source: scenarioTitle(t.title), text: turn });
  });
  (snap?.traceIds || []).forEach((id) => {
    if (out.length >= 3) return;
    out.push({ source: `Trace ${id}`, text: clip(snap.title || snap.rootCause || "from the issue's evidence", 160) });
  });
  return out.slice(0, 5);
};

/* ── a contract change ──────────────────────────────────────────────────── */

/** The environment version a contract approval would mint, as people read it ("v8"). */
export const nextContractVersion = (env, envState) => nextSnapshotLabel(env, envState);

const clamp = (n) => Math.max(0, Math.min(100, Math.round(n)));

/**
 * Approve the new contract: mint the environment version where the tool
 * takes its new parameter, run the baseline again on it — the prototype
 * draws those numbers by hash on the improvement and the version — and only
 * then compare the candidate there. Never against the old version.
 */
export const contractApprovalPatch = ({ env, envState, record, from }) => {
  const r = record?.result;
  const c = r?.contract;
  if (!c) return {};
  const minted = nextEnvVersion(env, envState, { changed: ["contract"], note: `${c.tool} takes ${c.param} — approved from ${record.id}` });
  const baseTools = minted.tools || env?.tools || null;
  const version = baseTools
    ? { ...minted, tools: baseTools.map((t) => (t.name === c.tool ? { ...t, params: [...new Set([...(t.params || []), c.param])] } : t)) }
    : minted;
  const list = envState?.envVersions?.length ? envState.envVersions : [...environmentVersions(env, envState)].reverse();
  const h = hashSeed(`${record.id}:${minted.label}:rebaseline`);
  const baseline = { train: clamp(r.base + (h % 5) - 2), held: clamp(r.heldBase + ((h >> 3) % 5) - 2) };
  const lift = 3 + ((h >> 5) % 6);
  const candidate = { train: clamp(baseline.train + lift), held: clamp(baseline.held + Math.max(0, lift - 2 - ((h >> 7) % 3))) };
  const at = new Date().toISOString();
  return {
    envVersions: [...list, version],
    activeEnvVersion: minted.label,
    unsavedEnvChanges: [],
    optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id
      ? { ...o, contractApproval: { at, from, to: minted.label, fromSnapshot: currentEnvSnapshot(env, envState)?.label || null, toSnapshot: nextSnapshotLabel(env, envState), tool: c.tool, param: c.param, baseline, candidate, rebaselinedAt: at } }
      : o)),
  };
};

/* The scenarios a fix built from its issue — what Edit scenarios edits. The
   held-back set's fresh variations are written by the search, not edited. */
export const issueDraftsOf = (record) => (record?.issueScenarios || []).filter((d) => !d.freshVariation && d.source === "error-feed");

/**
 * Could not reproduce → Reproduce again: the same improvement reproduces
 * once more on its edited scenarios (titles changed, some left out). It
 * keeps its id, name and what it already spent; everything a reproduction
 * decides — the scenarios, what reproduced, whether it searches — is new.
 * Null when nothing is left to reproduce.
 */
export const reproduceAgainPatch = ({ record, env, envState, state, edits = {} }) => {
  const issueId = record?.trigger?.issueId;
  if (!issueId || !env) return null;
  const issue = mockCluster(issueId) || { cluster_id: issueId, error: { name: record.trigger?.issueTitle }, severity: record.trigger?.severity };
  const drafts = issueDraftsOf(record)
    .filter((d) => edits[d.id]?.include !== false)
    .map(({ target: _t, source: _s, ...d }) => ({ ...d, title: String(edits[d.id]?.title ?? d.title ?? "").trim() || d.title }));
  if (!drafts.length) return null;
  const others = (envState?.optimizations || []).filter((o) => o.id !== record.id);
  const runs = (record.reproduceRuns || 0) + 1;
  const rebuilt = issueFixRecord({
    issue, env, es: { ...envState, optimizations: others }, drafts,
    limits: record.limits, budget: record.budget, regressionEnvIds: record.regressionEnvIds,
    seed: `${record.id}-again-${runs}`, state, targetMember: record.targetMember,
  });
  const next = {
    ...rebuilt,
    id: record.id,
    name: record.name,
    createdAt: record.createdAt,
    startedAt: new Date().toISOString(),
    reproduceRuns: runs,
    /* What the earlier reproduction spent stays spent. */
    spentFirst: improvementTotalSpend(record, env.surface) + (rebuilt.spentFirst || 0),
    /* The issue as it was when Fix was pressed; only what must start
       passing follows the edited scenarios. */
    snapshot: { ...(record.snapshot || rebuilt.snapshot), mustStartPassing: rebuilt.snapshot?.mustStartPassing || [], inconclusive: rebuilt.snapshot?.inconclusive || [] },
  };
  return { optimizations: (envState?.optimizations || []).map((o) => (o.id === record.id ? next : o)) };
};
