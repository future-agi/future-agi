/*
  Decoupled versioning — PRD v7 §4.10. The shared model every screen reads.

  · The agent is only a reference. Every agent change is a new AGENT version
    and never cascades: the environment (E) and the scenario set (S) change
    only when the user edits them or approves a NUDGE.
  · E versions are plain v1, v2 … with a change summary. Each SCENARIO carries
    its own major.minor.patch, which decides what happens to past results:
      patch → reused · minor → re-graded · major → re-run (until bridged).
  · Nothing is retired. A dropped capability marks the scenarios that need it
    STALE for agent versions without it (RSI PRD) — they still run, but count
    toward no decision until the user updates or archives them. Updating one
    so it no longer needs the tool clears it; so does the tool coming back.
  · The harness informs; the user decides. It never gates, promotes, blocks
    or ships an agent version.

  State this module reads and writes (all on envState, all optional):
    nudges         [{ id, kind, agentVersion, prevVersion, at, status, title,
                      ask, detail, capability?, ruleId?, scenarioIds, likely? }]
                    status: "open" | "approved" | "dismissed"
    applicability  [{ id, capability, scenarioIds, reason, at, nudgeId }]
                    (stale rules from an acknowledged Remove)
    scenario rows  .version ("1.0.0"), .versionLog [{ v, level, why, at,
                    snapshot: { ...the replaced version's row, v } }],
                    .archived { at, by }
*/
import { agentToolsFor, knownToolsFor, requiredToolsOf } from "./toolFit";
import { agentVersions } from "./versions";
import { ruleImpactOf } from "./envModel";
import { scenarioVersionOf, scenarioVersionsPin } from "./scenarioVersion";


/* ── capabilities ─────────────────────────────────────────────────────────── */

/** What an agent version can do: its tools and channels (the capability manifest). */
export const manifestOf = (env, envState, agentLabel) => ({
  agentVersion: agentLabel || null,
  tools: agentToolsFor(env, envState, agentLabel).map((t) => t.name),
  channels: [env?.surface || "chat"],
});

/** The capabilities a scenario is tagged as needing — the tools it exercises. */
export const requiredCapsOf = (row, env, envState) => {
  const known = knownToolsFor(env, envState);
  return requiredToolsOf(row, known).map((t) => (typeof t === "string" ? t : t.name)).filter(Boolean);
};

/**
 * Whether a scenario is stale for an agent version (RSI PRD): an agent change
 * removed a tool the scenario still needs. Read from the Remove nudges a new
 * agent version raised (whatever their answer) and the rules an acknowledged
 * one recorded. It clears once the scenario is updated so it no longer needs
 * the tool, or once the version has the tool again.
 * { stale, capability?, reason? }
 */
export const staleForAgentOf = (row, agentLabel, env, envState) => {
  if (!row?.id || !agentLabel) return { stale: false };
  const rules = [
    ...(envState?.applicability || []),
    ...(envState?.nudges || []).filter((n) => n.kind === "remove" && n.capability)
      .map((n) => ({ capability: n.capability, scenarioIds: n.scenarioIds, reason: `Needs ${n.capability}, which ${n.agentVersion} doesn't have` })),
  ];
  const rule = rules.find((a) => (a.scenarioIds || []).includes(row.id));
  if (!rule) return { stale: false };
  if (manifestOf(env, envState, agentLabel).tools.includes(rule.capability)) return { stale: false };
  /* Updated so it no longer calls the tool: not stale any more. */
  if (!requiredCapsOf(row, env, envState).includes(rule.capability)) return { stale: false };
  return { stale: true, capability: rule.capability, reason: rule.reason || `Needs ${rule.capability}` };
};

/* ── scenario versions: major.minor.patch ─────────────────────────────────── */

export const SCENARIO_BUMPS = {
  patch: { label: "patch", past: "reused", note: "Cosmetic fields only — past results still compare." },
  minor: { label: "minor", past: "re-graded", note: "A grader fix on one of its sub-goals — past runs are re-graded from stored traces." },
  major: { label: "major", past: "re-run", note: "What the caller does changed — past results don't compare until re-run or bridged." },
};
/* The same three, as people read them — the UI never shows the numbers. */
export const EDIT_EFFECT = {
  patch: "Past results still compare",
  minor: "Past runs are re-graded",
  major: "Past results need a re-run",
};

export { scenarioVersionOf, scenarioVersionsPin };

const parse = (v) => String(v || "1.0.0").split(".").map((n) => parseInt(n, 10) || 0);

/* What a version's snapshot keeps: the whole row as it stood — task, persona,
   situation, passes-when, sub-goals and marks, tools, path, setup, fixture,
   tier and critical flags — minus its own log (the entries before it). */
const contentOf = (row) => {
  const { versionLog, ...rest } = row || {};
  return { ...rest, v: scenarioVersionOf(row) };
};

/**
 * The row with its version bumped and the bump logged — never edited in place.
 * The log entry keeps a snapshot of the version it replaced (§4.10.8), so a
 * run that pinned that version still replays its text. `prior` is that
 * version's row when `row` already carries the edit; without it, `row` is
 * the version being replaced.
 */
export const bumpScenario = (row, level, why, prior) => {
  const was = prior || row;
  const [a, b, c] = parse(scenarioVersionOf(was));
  const v = level === "major" ? `${a + 1}.0.0` : level === "minor" ? `${a}.${b + 1}.0` : `${a}.${b}.${c + 1}`;
  return {
    ...row,
    version: v,
    versionLog: [...(was.versionLog || []), { v, level, why: why || null, at: new Date().toISOString(), snapshot: contentOf(was) }],
  };
};

const newer = (va, vb) => {
  const [a1, b1, c1] = parse(va);
  const [a2, b2, c2] = parse(vb);
  return a1 !== a2 ? a1 > a2 : b1 !== b2 ? b1 > b2 : c1 > c2;
};

/**
 * The row as it was at `version` — what a run that pinned it played. The
 * current row when that is its version, or when no snapshot is older-or-equal
 * to go on. A version from before snapshots were kept replays the earliest
 * snapshot newer than it: the text runs showed until the first recorded edit.
 * Set-level state (archived) stays as it is now.
 */
export const scenarioAtVersion = (row, version) => {
  if (!row || !version || version === scenarioVersionOf(row)) return row;
  const log = row.versionLog || [];
  const exact = log.findIndex((l) => l.snapshot?.v === version);
  const at = exact >= 0 ? exact : log.findIndex((l) => l.snapshot && newer(l.snapshot.v, version));
  if (at < 0) return row;
  const { v, archived, ...content } = log[at].snapshot;
  return {
    ...content,
    id: row.id,
    ...(at > 0 && { versionLog: log.slice(0, at) }),
    ...(row.archived !== undefined && { archived: row.archived }),
  };
};

/** A run's scenarios as it played them: each mapped to the version it pinned (unpinned runs as they are). */
export const pinnedScenarios = (rows = [], run) => (run?.scenarioVersions
  ? rows.map((r) => scenarioAtVersion(r, run.scenarioVersions[r.id]))
  : rows);

/** Which part moved between two scenario versions: null (same), patch, minor or major. */
export const bumpBetween = (va, vb) => {
  const [a1, b1, c1] = parse(va);
  const [a2, b2, c2] = parse(vb);
  if (a1 !== a2) return "major";
  if (b1 !== b2) return "minor";
  if (c1 !== c2) return "patch";
  return null;
};

/** The version a scenario had when a run played it (runs pin `scenarioVersions`). */
export const playedVersionOf = (run, row) => run?.scenarioVersions?.[row?.id] || (
  /* Runs from before versions existed played whatever the log held then. */
  [...(row?.versionLog || [])].reverse().find((l) => run?.finishedAt && Date.parse(l.at) <= Date.parse(run.finishedAt))?.v
  || (row?.versionLog?.length ? "1.0.0" : scenarioVersionOf(row))
);

/* ── verdict causes (§4.10.10) ────────────────────────────────────────────── */

/*
  Outcomes (updated RSI PRD). Every conversation ends one of five ways; the
  v7 cause stays underneath as the detail ("Test invalid · simulator").
    passed · failed — the agent's result, counted for or against it
    inconclusive    — not enough evidence to grade; never counts as a pass
    test invalid    — the test broke, not the agent: the simulated user left
                      its goal, the starting state didn't load, or a judge failed
    error           — our infrastructure or the connection failed
  Inconclusive, test invalid and error count neither for nor against the agent.
*/
export const OUTCOMES = {
  passed: { label: "Passed", counted: true, note: "Every grader cleared its bar." },
  failed: { label: "Failed", counted: true, note: "The agent had what it needed and didn't do it. Counted against it." },
  inconclusive: { label: "Inconclusive", counted: false, note: "Not enough evidence to grade. Never counted as a pass." },
  "test-invalid": { label: "Test invalid", counted: false, note: "The test broke, not the agent — the simulated user left its goal, the starting state didn't load, or a judge failed. Counts neither for nor against the agent." },
  error: { label: "Error", counted: false, note: "Our infrastructure or the connection failed. Counts neither for nor against the agent." },
};
export const OUTCOME_ORDER = ["passed", "failed", "inconclusive", "test-invalid", "error"];

export const VERDICT_CAUSES = {
  pass: { label: "Passed", outcome: "passed", scored: true },
  "fail-agent": { label: "Failed · agent", outcome: "failed", scored: true, note: "The agent had the capability and failed." },
  "fail-required": { label: "Failed · required evaluation", outcome: "failed", scored: true, note: "An evaluation marked required failed, so the conversation failed whatever the others scored." },
  "inc-evidence": { label: "Inconclusive · evidence", outcome: "inconclusive", scored: false, note: "Not enough evidence to grade — opaque tools, no ground truth, or coverage skipped. Never counted as a pass." },
  "inc-simulator": { label: "Test invalid · simulator", outcome: "test-invalid", scored: false, note: "The simulated user left its goal. Counts neither for nor against the agent — above 10% of a run the result is flagged unreliable." },
  "inc-setup": { label: "Test invalid · setup", outcome: "test-invalid", scored: false, note: "The starting state didn't load, so the conversation tested nothing. Counts neither for nor against the agent." },
  "inc-judge": { label: "Test invalid · judge", outcome: "test-invalid", scored: false, note: "The judge failed to return a verdict. Counts neither for nor against the agent; flagged for a person." },
  "inc-infra": { label: "Error · infrastructure", outcome: "error", scored: false, note: "Our sandbox, a provider or tool infrastructure broke. Counts neither for nor against the agent." },
  "inc-transport": { label: "Error · connection", outcome: "error", scored: false, note: "The connection to the agent dropped before it finished. Counts neither for nor against the agent." },
};

/** The outcome a cause rolls up to: passed · failed · inconclusive · test-invalid · error. */
export const outcomeOfCause = (cause) => VERDICT_CAUSES[cause]?.outcome
  || (cause === "pass" ? "passed" : String(cause || "").startsWith("fail-") ? "failed" : "inconclusive");

/**
 * One task's verdict and cause, from the fields runs already carry
 * (status, fault.{environment,transport,simulator,grading}, a failed required
 * evaluation).
 */
export const causeOf = (task) => {
  const f = task?.fault || {};
  if (task?.status === "unmeasured" || f.environment || f.transport || f.simulator || f.grading) {
    if (f.simulator) return "inc-simulator";
    if (f.grading) return /no evaluations|no ground truth|not enough evidence|coverage skipped/i.test(f.grading) ? "inc-evidence" : "inc-judge";
    if (f.environment && !f.toolGap && /quarantin/i.test(f.environment)) return "inc-evidence";
    if (f.environment && !f.toolGap && /never passed its readiness|no longer stages|could not be staged/i.test(f.environment)) return "inc-setup";
    if (f.transport && !f.environment) return "inc-transport";
    return "inc-infra";
  }
  if (task?.status === "passed") return "pass";
  if (task?.requiredFail) return "fail-required";
  return "fail-agent";
};

/** Counts by cause, plus whether simulator failures make the run unreliable (>10%). */
export const causeTally = (tasks = []) => {
  const counts = Object.fromEntries(Object.keys(VERDICT_CAUSES).map((k) => [k, 0]));
  tasks.forEach((t) => { counts[causeOf(t)] += 1; });
  const total = tasks.length || 1;
  return { counts, simulatorShare: counts["inc-simulator"] / total, unreliable: counts["inc-simulator"] / total > 0.1 };
};

/* ── nudges (§4.10.5, §4.10.6) ────────────────────────────────────────────── */

export const NUDGE_KINDS = {
  add: { label: "Capability added", short: "Add" },
  remove: { label: "Capability removed", short: "Remove" },
  core: { label: "Core change", short: "Fork" },
  rule: { label: "Fix or policy change?", short: "Question" },
  behaviour: { label: "Tool behaves differently", short: "Behaviour" },
};

export const openNudgesOf = (envState) => (envState?.nudges || []).filter((n) => n.status === "open");

/** Drifted while a new agent version has raised a nudge nobody has answered. */
export const isDrifted = (envState) => openNudgesOf(envState).length > 0;

/* Words that make a rule change looser — the ones a compliant agent could
   use to fail a sub-goal written for the old rule (the §4.10.6 auto-check). */
const LOOSER = /\b(no longer|not needed|not required|without|can be|may|optional|unless|except|skip|relax|removed?|allow(ed)?)\b/i;

const nid = (kind, ver, key) => `ndg-${kind}-${ver}-${String(key).replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;

/**
 * What a new agent version would ask of this environment — proposals only,
 * never applied. Compares its manifest with the previous version's (Add /
 * Remove) and checks changed rules (the fix-or-policy question; a stricter
 * rule is labelled "likely fix" and asks nothing).
 * Returns { nudges, likelyFixes }.
 */
export const detectNudges = (env, envState, newLabel, prevLabel) => {
  const versions = agentVersions(envState);
  const prev = prevLabel || versions[versions.findIndex((v) => v.label === newLabel) - 1]?.label || null;
  const now = new Date().toISOString();
  const rows = (envState?.scenarios || []).filter((r) => !r.archived && !r.retired);
  const out = [];
  const likelyFixes = [];
  if (prev) {
    const a = new Set(manifestOf(env, envState, prev).tools);
    const b = new Set(manifestOf(env, envState, newLabel).tools);
    [...b].filter((t) => !a.has(t)).forEach((tool) => {
      const using = rows.filter((r) => requiredCapsOf(r, env, envState).includes(tool));
      out.push({
        id: nid("add", newLabel, tool), kind: "add", agentVersion: newLabel, prevVersion: prev, at: now, status: "open",
        capability: tool, scenarioIds: using.map((r) => r.id),
        title: `You added ${tool}`,
        ask: `${using.length} scenario${using.length === 1 ? "" : "s"} exercise it. Add a ${tool} handler to the environment and generate 3 scenarios for it?`,
        detail: "On approval the environment and scenario set each get a new version; the new scenarios are shown apart until they have run on both versions.",
      });
    });
    [...a].filter((t) => !b.has(t)).forEach((tool) => {
      const need = rows.filter((r) => requiredCapsOf(r, env, envState).includes(tool));
      if (!need.length) return;
      out.push({
        id: nid("remove", newLabel, tool), kind: "remove", agentVersion: newLabel, prevVersion: prev, at: now, status: "open",
        capability: tool, scenarioIds: need.map((r) => r.id),
        title: `${tool} is gone in ${newLabel}`,
        ask: `${need.length} scenario${need.length === 1 ? "" : "s"} need ${tool}, which ${newLabel} doesn't have, so ${need.length === 1 ? "it is" : "they are"} stale on ${newLabel}.`,
        detail: "Stale scenarios still run, but count toward no decision until you update or archive them. Nothing is retired.",
      });
    });
  }
  const version = versions.find((v) => v.label === newLabel);
  const impacts = ruleImpactOf(version, env, envState);
  impacts.forEach((impact) => {
    const change = (version?.changes || []).map((c) => `${c.title} ${c.detail || ""} ${c.addendum || ""}`).join(" ");
    if (!LOOSER.test(change)) {
      likelyFixes.push({ ruleId: impact.rule.id, rule: impact.rule.text, subGoals: impact.subGoals.map((g) => g.id) });
      return;
    }
    out.push({
      id: nid("rule", newLabel, impact.rule.id), kind: "rule", agentVersion: newLabel, prevVersion: prev, at: now, status: "open",
      ruleId: impact.rule.id, scenarioIds: impact.scenarios,
      title: `${newLabel} changed how the agent handles ${impact.rule.id}`,
      ask: `"${impact.rule.text}" — a policy change, or not intended?`,
      detail: `${impact.subGoals.length} sub-goal${impact.subGoals.length === 1 ? "" : "s"} and ${impact.scenarios.length} scenario${impact.scenarios.length === 1 ? "" : "s"} test it.`,
    });
  });
  /* A rule the new version added that no tested rule covers. Nothing checks
     it, so a run can't catch it if it's wrong. One that lets the agent do
     more (approve without a supervisor, skip a check) gets the same question
     as a changed rule — that's exactly what an unintended change looks like.
     One that only adds a restriction is noted, untested, without asking. */
  const untested = [];
  if (!impacts.length) {
    addedRulesOf(version).forEach((text) => {
      if (!LOOSER.test(text)) { untested.push(text); return; }
      out.push({
        id: nid("rule", newLabel, `new-${text.slice(0, 40)}`), kind: "rule", newRule: true, agentVersion: newLabel, prevVersion: prev, at: now, status: "open",
        ruleId: "New rule", rule: "", to: text, scenarioIds: [], callerIds: [],
        change: { title: "Instructions edited", detail: "Added to the prompt" },
        title: `${newLabel} added a rule that lets the agent do more`,
        ask: `"${text}" — a policy change, or not intended?`,
        detail: "Nothing in this environment tests it yet.",
      });
    });
  }
  return { nudges: out, likelyFixes, untested };
};

/* The rules a version's instruction changes add — "Added: …" lines. */
const addedRulesOf = (version) => [...new Set((version?.changes || [])
  .filter((c) => c.kind === "instructions" || /instruction|prompt/i.test(c.title || ""))
  .flatMap((c) => [
    ...String(c.addendum || "").split("\n").map((l) => l.replace(/^-\s*/, "").trim()),
    String(c.detail || "").match(/^Added:\s*"(.+)"$/)?.[1] || "",
  ])
  .map((t) => t.replace(/\.$/, "").trim())
  .filter((t) => t.split(/\s+/).length >= 4))];

/** Add newly detected nudges to state without duplicating ones already raised. */
export const withNudges = (envState, nudges = []) => {
  const have = new Set((envState?.nudges || []).map((n) => n.id));
  const fresh = nudges.filter((n) => !have.has(n.id));
  if (!fresh.length) return envState;
  /* A newer version folds in what an earlier card skipped: its changes are
     still between the environment and the agent, so they come back open in
     the newest card instead of being lost. */
  const kept = (envState?.nudges || []).map((n) => (n.status === "dismissed" && (n.skippedAs || n.scenarioSkipped)
    ? { ...n, status: "open", skippedAs: undefined, scenarioSkipped: undefined, foldedInto: fresh[0]?.agentVersion || null }
    : n));
  /* The scenario step a card skipped comes back with it. */
  const update = envState?.scenarioUpdate;
  return {
    ...envState,
    nudges: [...fresh, ...kept],
    ...(update?.status === "skipped" && { scenarioUpdate: { ...update, status: "open" } }),
  };
};

/** Patch that answers a nudge: approved or dismissed (dismiss keeps E + S pinned, with a drift badge). */
export const resolveNudgePatch = (envState, id, status, extra = {}) => ({
  nudges: (envState?.nudges || []).map((n) => (n.id === id ? { ...n, status, resolvedAt: new Date().toISOString(), ...extra } : n)),
});

/** Patch for an acknowledged Remove: the stale rule is recorded. */
export const staleRulePatch = (envState, nudge) => ({
  applicability: [
    ...(envState?.applicability || []),
    {
      id: `app-${nudge.capability}`, capability: nudge.capability, scenarioIds: nudge.scenarioIds,
      reason: `Needs ${nudge.capability}, which ${nudge.agentVersion} doesn't have`, at: new Date().toISOString(), nudgeId: nudge.id,
    },
  ],
  ...resolveNudgePatch(envState, nudge.id, "approved"),
});

/* ── drift (additive, v1 builder) ─────────────────────────────────────────── */

/** Nudges someone dismissed — E + S stayed pinned, the badge stays. */
export const dismissedNudgesOf = (envState) => (envState?.nudges || []).filter((n) => n.status === "dismissed");

/**
 * The drift badge: open nudges make the environment Drifted; dismissed ones
 * keep a quieter badge so nothing is hidden (AC-13.12).
 */
export const driftOf = (envState) => {
  const open = openNudgesOf(envState).length;
  const dismissed = dismissedNudgesOf(envState).length;
  return { open, dismissed, drifted: open > 0, badge: open > 0 || dismissed > 0 };
};
