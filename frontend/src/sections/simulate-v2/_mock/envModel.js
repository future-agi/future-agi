/*
  The environment, as the RL environment team's versioning model reads it.

  Only the agent varies between two runs that are compared. Everything else is
  held fixed, and each part changes for its own reason, so each keeps its own
  version:

    Agent version       a prompt edit, a commit, a provider-side edit
    Environment version the contract (tools, arguments, rules, use cases) and
                        the world (schema, seed data, tool behaviour, actors)
    Suite version       scenarios added, edited, retired
    Sub-goal catalogue  a shared, named check edited
    Eval version        an evaluation or the judge changed
    Simulated caller    the model and voice stack that place the call

  Decisions this prototype takes (see the versioning doc's S1–S8, V1–V8):
    S1  intended behaviour comes from the agent's prompt; when a new agent
        version touches a rule a sub-goal tests, someone says whether it's a
        fix (the suite stays) or a spec change (the contract and the scenarios
        testing that rule change)
    V1  an environment version is the contract and the world only
    V3  a new one when tools, arguments, schema, seed or tool behaviour change,
        or when a prompt edit is confirmed as a spec change
    V7  the world is data and tools; the agent is the conversational part
    S6  sub-goals are a shared, named catalogue, versioned, each recording the
        rule, use case or tool it tests
    S7  one suite; each change is a new suite version; comparisons run over the
        scenarios both runs share
    S8  live account credentials are agent configuration, supplied per run

  Everything here is derived from what an environment already stores, so
  saved data from before renders without migration.
*/
import { subTasksFor } from "./contract";
import { hashSeed, envTurnLimitOf } from "./runStream";
import { toolEnvPin, toolEnvSummary, toolFaultsOf } from "./toolEnv";
import { scenarioVersionsPin } from "./scenarioVersion";
import { envLineage, holdsInEnv, savedWorldEnv } from "./versions";
import { replicationOf } from "./replication";
import { proofKindOf } from "./rsiModel";
import { secretsUsedOf } from "./envConfig";

const short = (s) => hashSeed(String(s || "")).toString(36).slice(0, 4);
const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 40);

/* ── contract: rules with stable ids ─────────────────────────────────────── */

/**
 * The rules the contract holds, each with an id that survives rewording —
 * the contract is written by a model, so its text differs between builds;
 * the id is what a sub-goal points at. A spec change keeps the id and records
 * the new wording.
 */
export const rulesOf = (env, envState) => {
  /* Only changes made in this version's line hold — a rollback's parent is
     the version it rolled back to, so what was changed in between doesn't. */
  const lineage = envLineage(env, envState);
  const changes = (envState?.ruleChanges || []).filter((c) => holdsInEnv(lineage, c.envVersion));
  const retired = new Set(changes.filter((c) => c.kind === "removed").map((c) => c.id));
  const base = (env?.rules || []).map((text, i) => {
    const id = `R${i + 1}`;
    const last = [...changes].reverse().find((c) => c.id === id && c.kind === "changed");
    return { id, text: last?.to || text, original: text, source: "prompt", changedIn: last?.envVersion || null };
  });
  const added = changes.filter((c) => c.kind === "added").map((c) => ({ id: c.id, text: c.to, original: null, source: "prompt", changedIn: c.envVersion }));
  return [...base, ...added].filter((r) => !retired.has(r.id));
};

/** Which rule a scenario's expectation quotes, if any. */
const quotedRule = (row) => (row?.expected || row?.outcome || "").match(/rule: "(.+?)"/)?.[1] || null;

export const ruleOfScenario = (row, rules) => {
  const q = quotedRule(row);
  if (!q) return null;
  const key = q.toLowerCase().slice(0, 24);
  return rules.find((r) => r.original?.toLowerCase().startsWith(key) || r.text.toLowerCase().startsWith(key)) || null;
};

/* ── the sub-goal catalogue ──────────────────────────────────────────────── */

/**
 * Whether an environment is connect-only: a phone or hosted agent with no
 * world — no tables, no tool handlers. Its tools live only in the contract,
 * every sub-goal is judged by an LLM, and a scenario is proved against a
 * reference transcript rather than a reference solution of tool calls.
 */
export const isConnectOnly = (env) => env?.worldMode === "connect-only" || ((env?.seed?.tables || []).length === 0 && env?.surface === "voice");

/**
 * The shared, named checks the suite grades with. Derived from the
 * scenarios' own sub-goals, deduplicated by meaning: "Call lookup_order …"
 * is one entry however many scenarios name it. Each records what it tests —
 * a rule, a tool or a use case — so a change to that rule can point at it.
 */
export const subGoalCatalogOf = (env, envState) => {
  const rules = rulesOf(env, envState);
  const connect = isConnectOnly(env);
  const edits = envState?.subGoalEdits || [];
  const byId = new Map();
  const add = (id, entry, scenarioId) => {
    const e = byId.get(id) || { ...entry, id, usedBy: [] };
    if (scenarioId && !e.usedBy.includes(scenarioId)) e.usedBy.push(scenarioId);
    byId.set(id, e);
  };
  (envState?.scenarios || []).forEach((row) => {
    if (row.retired || row.archived) return;
    const goals = Array.isArray(row.subGoals) && row.subGoals.length ? row.subGoals : subTasksFor(row, env);
    goals.forEach((g) => {
      const text = typeof g === "string" ? g : g?.text || g?.label || "";
      const call = text.match(/^Call ([a-z0-9_]+)/i)?.[1];
      if (call) {
        add(`SG-tool-${call}`, { name: `Calls ${call} with the right arguments`, kind: connect ? "judged" : "code", tests: { kind: "tool", ref: call }, reads: connect ? "transcript" : "world + calls" }, row.id);
      } else {
        const id = `SG-${slug(text)}`;
        add(id, { name: text, kind: connect || !/verify|confirm|check/i.test(text) ? "judged" : "code", tests: { kind: "use case", ref: row.useCase || null, refs: [] }, reads: connect ? "transcript" : "calls" }, row.id);
        /* A shared check runs in many use cases. `ref` stays the first, for
           what already reads it; `refs` is every use case it runs in. */
        const refs = byId.get(id).tests.refs;
        if (row.useCase && !refs.includes(row.useCase)) refs.push(row.useCase);
      }
    });
    const rule = ruleOfScenario(row, rules);
    if (rule) add(`SG-rule-${rule.id}`, { name: `Holds the line: ${rule.text.replace(/\.$/, "")}`, kind: connect ? "judged" : "code", tests: { kind: "rule", ref: rule.id }, reads: connect ? "transcript" : "world + calls" }, row.id);
  });
  return [...byId.values()].map((e) => {
    const edit = [...edits].reverse().find((x) => x.id === e.id);
    return { ...e, ...(edit && { name: edit.name || e.name, criterion: edit.criterion, editedIn: edit.version }) };
  }).sort((a, b) => b.usedBy.length - a.usedBy.length);
};

/* ── the versions a run holds fixed ──────────────────────────────────────── */

const count = (n) => Math.max(1, n);

/** The suite's version: one per batch of scenarios added, edited or retired. */
export const suiteVersionOf = (envState) => {
  /* A proposal isn't in the set until someone adds it — it moves nothing. */
  const rows = (envState?.scenarios || []).filter((r) => !r.proposed);
  const batches = new Set(rows.map((r) => r.batchId).filter(Boolean)).size;
  /* Each retire and each restore moves the suite on, so undoing one never
     hands back an old suite number. */
  const edits = rows.reduce((n, r) => n + (r.editedAt || r.outdatedBy ? 1 : 0) + (r.retired || r.archived ? 1 : 0) + 2 * (r.restores || 0), 0);
  /* The S version log (suiteLog.js) records edits the row fields can't see —
     a second edit to one scenario, tier moves, sub-goal publishes — so the
     suite reads whichever is further along. Read directly: suiteLog imports
     this module. */
  const logged = (envState?.suiteVersions || [])
    .filter((l) => !/waiting to be added$/.test(String(l.summary || "")))
    .reduce((m, l) => Math.max(m, parseInt(String(l.label || "").replace(/\D/g, ""), 10) || 0), 0);
  return `s${Math.max(count(batches + edits), logged)}`;
};

export const catalogVersionOf = (envState) => `c${count(1 + (envState?.subGoalEdits || []).length)}`;

/** Evaluations change their own version — never the environment's. */
export const evalVersionOf = (envState) => `e${count(1 + (envState?.evalChanges || []).length + (envState?.envVersions || []).filter((v) => (v.changed || []).includes("scoring")).length)}`;

const VOICE_STACK = "ElevenLabs turbo v2.5 · Deepgram nova-3";

/** The simulated caller for one model — voice keeps the stack it speaks through. */
export const simulatorForModel = (model, voice) => (voice
  ? { model, stack: VOICE_STACK, label: `${model} · ElevenLabs + Deepgram` }
  : { model, stack: null, label: model });

/**
 * The simulated caller: the model, and for voice the stack it speaks through.
 * The simulated user's model is part of what a run holds fixed (v7), so the
 * one chosen in Settings (`runDefaults.simulatorModel`, recorded with a
 * caller change) is the one new runs pin. Unset, the surface's default.
 */
export const simulatorOf = (env, envState) => envState?.simulator
  || (envState?.runDefaults?.simulatorModel
    ? simulatorForModel(envState.runDefaults.simulatorModel, env?.surface === "voice")
    : env?.surface === "voice"
      ? { model: "gpt-4o-realtime", stack: VOICE_STACK, label: "gpt-4o-realtime · ElevenLabs + Deepgram" }
      : { model: "claude-sonnet-4", stack: null, label: "claude-sonnet-4" });

/**
 * Changing the simulated user's model is a caller-pin change: runs before and
 * after it don't compare. Recorded as its own history row (`callerChanges`),
 * beside — never inside — the environment version.
 */
export const callerChangePatch = (env, envState, model) => {
  const from = simulatorOf(env, envState);
  const to = simulatorForModel(model, env?.surface === "voice");
  if (from.model === model) return { runDefaults: { ...(envState?.runDefaults || {}), simulatorModel: model } };
  const n = (envState?.callerChanges || []).length + 2;
  return {
    runDefaults: { ...(envState?.runDefaults || {}), simulatorModel: model },
    simulator: to,
    callerChanges: [...(envState?.callerChanges || []), { label: `caller ${n}`, from: from.model, to: model, at: new Date().toISOString() }],
  };
};

/*
  The tool dial a run held (PRD v6 Story 5): each tool's tier and fault
  profile. Read as "5 stateful · 3 replay · t4821" — the summary for people,
  the fingerprint so two dials with the same counts still tell apart. A
  connect-only environment has no dial; its pin is "—".
*/
const toolPinLabel = (summary, pin) => (summary || pin ? [summary, pin].filter(Boolean).join(" · ") : null);
const currentToolPin = (env, envState) => (isConnectOnly(env) ? "—" : toolPinLabel(toolEnvSummary(savedWorldEnv(env, envState), envState), toolEnvPin(savedWorldEnv(env, envState), envState)));

/* How faithfully the run reproduced the agent (replication.js) — fidelities
   never mix in one comparison (PRD v7 §4.10.3, D2). */
const runFidelityOf = (env, envState) => (envState?.agent ? replicationOf(envState.agent, env)?.fidelity || null : null);

/**
 * What a run held fixed. Older runs carry less; they fall back to what was current.
 * Every value is a plain string or number, so two runs' pins compare key by key.
 */
export const runPinsOf = (run, env, envState) => ({
  agent: run?.agentVersion || null,
  env: run?.envVersion || null,
  suite: run?.suiteVersion || suiteVersionOf(envState),
  catalog: run?.catalogVersion || catalogVersionOf(envState),
  eval: run?.evalVersion || evalVersionOf(envState),
  simulator: run?.simulator?.label || simulatorOf(env, envState).label,
  toolEnv: run?.toolEnv ? toolPinLabel(run.toolEnvSummary, run.toolEnv) : currentToolPin(env, envState),
  /* The rest of the comparability key (§4.10.3). */
  surface: run?.surface || env?.surface || null,
  fidelity: run?.runFidelity || runFidelityOf(env, envState),
  trials: run?.trials || run?.repeats || 1,
  /* Recorded, not compared: a new key isn't a new test. */
  secrets: run?.secrets ? Object.keys(run.secrets).length : null,
});

/**
 * The pins a run started now records. `trials` and `seed` are the run's own
 * (passed by the caller that knows them); the per-scenario versions it plays
 * are read off the set as it stands (§4.10.3).
 *
 * `rules` — a run started from a Run button takes the updated RSI PRD's run
 * rules: outcomes (retry once, pull a scenario test invalid twice, cost and
 * response times only when reported), required evaluations, and the proof
 * kind it ran under. Seeded history and previews call without it, so they
 * replay exactly as they always did (missing → old behaviour).
 */
export const pinsForNewRun = (env, envState, { trials, seed, rules = false } = {}) => ({
  ...(rules && {
    outcomes: 1,
    requiredEvals: 1,
    proofKind: (({ kind, tier }) => ({ kind, tier }))(proofKindOf(envState?.agent)),
    /* "Environments, defined": sub-goals decide pass or fail, the turn limit
       is enforced at the environment's limit as it stands now, and the run
       took its size and pace from the Settings defaults. */
    gradingRule: 2,
    turnLimits: 1,
    turnLimit: envTurnLimitOf(envState),
    runSettings: 1,
  }),
  /* Judged by every admission gate there is now (coverage.js). */
  admissionGates: 3,
  scenarioVersions: scenarioVersionsPin(envState),
  /* The secret references it resolved — references only. */
  secrets: secretsUsedOf(envState),
  surface: env?.surface || null,
  runFidelity: runFidelityOf(env, envState),
  ...(trials != null && { trials }),
  ...(seed != null && { seed }),
  /* The actors as edited when it ran — a later edit doesn't rewrite history. */
  actorEdits: envState?.actorEdits || null,
  suiteVersion: suiteVersionOf(envState),
  catalogVersion: catalogVersionOf(envState),
  evalVersion: evalVersionOf(envState),
  simulator: simulatorOf(env, envState),
  /* The dial, and the fault profiles themselves — a replay of this run draws
     the same faults however the dial is set later. */
  ...(isConnectOnly(env) ? { toolEnv: "—", toolEnvSummary: null, toolFaults: null } : {
    /* The tools of the version the run is on — tools added since aren't in it until saved. */
    toolEnv: toolEnvPin(savedWorldEnv(env, envState), envState),
    toolEnvSummary: toolEnvSummary(savedWorldEnv(env, envState), envState),
    toolFaults: toolFaultsOf(savedWorldEnv(env, envState), envState),
  }),
});

/**
 * Held fixed between two runs — the dashed box. Comparable only when
 * everything but the agent matches; the suite may differ as long as the
 * comparison stays on the scenarios both share.
 */
export const heldFixed = (a, b, env, envState) => {
  const pa = runPinsOf(a, env, envState);
  const pb = runPinsOf(b, env, envState);
  const rows = [
    { id: "env", label: "World + contract", a: pa.env, b: pb.env, hard: true },
    { id: "suite", label: "Scenario suite", a: pa.suite, b: pb.suite, hard: false, note: "compared over the scenarios both runs share" },
    { id: "catalog", label: "Sub-goal definitions", a: pa.catalog, b: pb.catalog, hard: true },
    { id: "eval", label: "Eval version", a: pa.eval, b: pb.eval, hard: true },
    { id: "simulator", label: "Simulated caller", a: pa.simulator, b: pb.simulator, hard: true },
    { id: "toolEnv", label: "Tool fidelity", a: pa.toolEnv, b: pb.toolEnv, hard: true },
  ].map((r) => ({ ...r, same: r.a === r.b }));
  const broken = rows.filter((r) => !r.same && r.hard);
  return { rows, comparable: !broken.length, broken, agent: { a: pa.agent, b: pb.agent } };
};

/* ── scenario states ─────────────────────────────────────────────────────── */

/**
 * The versioning doc's states, from two questions. "Can it still run?" —
 * every tool, table and record it names still exists. "Is it still correct?"
 * — the rule it tests is still the intended behaviour.
 */
export const SCENARIO_STATES = {
  valid: { label: "Valid", note: "reuse as is" },
  recheck: { label: "Re-check", note: "re-run its proof" },
  outdated: { label: "Outdated", note: "rewrite it" },
  retired: { label: "Retired", note: "no longer applies" },
  new: { label: "New", note: "added since this version" },
  blocked: { label: "Blocked", note: "fix what failed, then validate" },
};

export const scenarioStateOf = (row, status, versions = []) => {
  /* Failed a check on add or on Validate: kept as a draft, never run. */
  if (row?.blocked) return { id: "blocked", canRun: false, correct: null, ...SCENARIO_STATES.blocked, why: row.blocked.reason };
  if (row?.retired) return { id: "retired", canRun: null, correct: false, ...SCENARIO_STATES.retired, why: row.retired.why || "Its capability is gone." };
  if (row?.outdatedBy) return { id: "outdated", canRun: true, correct: false, ...SCENARIO_STATES.outdated, why: `Tests ${row.outdatedBy.ruleId}, which changed in ${/^agent /.test(String(row.outdatedBy.version)) ? row.outdatedBy.version : displayEnvLabel(row.outdatedBy.version, versions)} as a policy change.` };
  const s = status?.id;
  if (s === "broken" || s === "no-tool") return { id: "outdated", canRun: false, correct: null, ...SCENARIO_STATES.outdated, why: status.detail };
  if (s === "stale" || s === "checking" || s === "quarantined") return { id: "recheck", canRun: null, correct: true, ...SCENARIO_STATES.recheck, why: status.detail };
  if (s === "needs-env") return { id: "new", canRun: false, correct: true, ...SCENARIO_STATES.new, why: status.detail };
  return { id: "valid", canRun: true, correct: true, ...SCENARIO_STATES.valid, why: status?.detail || null };
};

/* ── fix or spec change ──────────────────────────────────────────────────── */

/**
 * What a new agent version did to the rules its prompt states, read against
 * the sub-goals that test them. A version that touches no tested rule is a
 * fix without asking; one that does needs a person to say which it is.
 */
const STOP = new Set(["never", "always", "with", "from", "that", "this", "than", "into", "only", "must", "should", "before", "after", "about", "your", "their", "them", "they", "when", "what", "which", "will", "have", "does", "each", "every", "other", "more", "less", "same"]);

export const ruleImpactOf = (version, env, envState) => {
  const rules = rulesOf(env, envState);
  const catalog = subGoalCatalogOf(env, envState);
  const changes = (version?.changes || []).filter((c) => c.kind === "instructions" || /rule|instruction|prompt/i.test(`${c.kind} ${c.title}`));
  /* A change touches a rule when it names it, or shares most of what makes
     the rule distinctive — two common words in passing isn't enough. */
  const touched = rules.filter((r) => changes.some((c) => {
    const text = `${c.title} ${c.detail || ""} ${c.addendum || ""}`.toLowerCase();
    if (new RegExp(`\\b${r.id.toLowerCase()}\\b`).test(text)) return true;
    const words = [...new Set(r.text.toLowerCase().match(/\$?[a-z0-9]{4,}/g) || [])].filter((w) => !STOP.has(w));
    const hits = words.filter((w) => new RegExp(`(^|[^a-z0-9])${w.replace("$", "\\$")}`).test(text)).length;
    return hits >= Math.max(3, Math.ceil(words.length * 0.5));
  }));
  return touched.map((r) => {
    const goals = catalog.filter((g) => g.tests.kind === "rule" && g.tests.ref === r.id);
    const scenarios = [...new Set(goals.flatMap((g) => g.usedBy))];
    return { rule: r, subGoals: goals, scenarios };
  }).filter((x) => x.subGoals.length);
};

/** A stable id for a use case — grouping survives the contract being re-written.
    Hashes the whole text: use cases sharing their first words ("Refuse the
    request that would break: …") each get their own id. */
export const useCaseId = (text) => `UC-${short(String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""))}`;
/** The id older stored corrections used (first 40 characters only). */
export const legacyUseCaseId = (text) => `UC-${short(slug(text))}`;

/* ── sub-goals on the scenario, critical or supporting ───────────────────── */

/*
  PRD v6 (Story 18) keeps sub-goals on the scenario: 2–7 each, every one
  marked critical or supporting. The versioning model shares their
  definitions: each sub-goal on a scenario points at a catalogue entry, so
  editing the definition re-grades every scenario that uses it. Both hold —
  the scenario owns which sub-goals it has and how much each matters; the
  catalogue owns what each one checks.
*/
const catalogIdOf = (text) => {
  const call = String(text).match(/^Call ([a-z0-9_]+)/i)?.[1];
  if (call) return `SG-tool-${call}`;
  return `SG-${slug(text)}`;
};

/* Critical by default: anything that acts (a tool call), verifies, or holds a rule. */
const defaultMark = (text) => (/^Call |verify|confirm|holds the line|refuse|recognise that the request is asking to break/i.test(text) ? "critical" : "supporting");

export const subGoalsOfScenario = (row, env, envState) => {
  if (!row) return [];
  const rules = rulesOf(env, envState);
  const stored = Array.isArray(row.subGoals) && row.subGoals.length;
  const goals = stored ? row.subGoals : subTasksFor(row, env);
  /* PRD v7: a sub-goal someone adds is critical unless they mark it
     supporting, so "every critical sub-goal holds" is the normal pass. The
     ones derived for a scenario keep the mark they always had. */
  const derived = stored ? new Set(subTasksFor(row, env).map((g) => (typeof g === "string" ? g : g?.text || g?.label || ""))) : null;
  const items = goals.map((g, i) => {
    const text = typeof g === "string" ? g : g?.text || g?.label || "";
    const id = catalogIdOf(text);
    return { key: `${row.id}:${i}`, index: i, id, text, mark: row.subGoalMarks?.[id] || (derived && !derived.has(text) ? "critical" : defaultMark(text)) };
  });
  const rule = ruleOfScenario(row, rules);
  if (rule && !items.some((x) => x.id === `SG-rule-${rule.id}`)) {
    items.push({ key: `${row.id}:rule`, index: items.length, id: `SG-rule-${rule.id}`, text: `Holds the line: ${rule.text.replace(/\.$/, "")}`, mark: row.subGoalMarks?.[`SG-rule-${rule.id}`] || "critical" });
  }
  /* At least one critical — a scenario with none can't fail. */
  if (items.length && !items.some((x) => x.mark === "critical")) items[0] = { ...items[0], mark: "critical" };
  return items;
};

/**
 * One failed critical sub-goal fails the scenario; an inconclusive critical
 * one makes it inconclusive (never a pass); a supporting one is reported —
 * shown, and it lowers the score — but never fails it.
 */
export const rollupOf = (items = []) => {
  const crit = items.filter((x) => x.mark === "critical");
  const sup = items.filter((x) => x.mark !== "critical");
  const criticalFailed = crit.filter((x) => x.status === "failed").length;
  const criticalUnsure = crit.filter((x) => x.status === "inconclusive").length;
  const supportingFailed = sup.filter((x) => x.status === "failed").length;
  const verdict = criticalFailed ? "failed" : criticalUnsure ? "inconclusive" : "passed";
  const met = items.filter((x) => x.status === "met").length;
  return { verdict, criticalFailed, criticalUnsure, supportingFailed, score: items.length ? Math.round((met / items.length) * 100) : 0 };
};

/* ── environment version: plain vN (PRD v7) ──────────────────────────────── */

export const envVersionDisplay = (versions = []) => {
  /* PRD v7 §4.10.2: environment versions are plain E v1, v2, … — each with a
     change summary. major.minor.patch belongs to each scenario, not to E. */
  const map = new Map();
  versions.forEach((v) => map.set(v.label, v.label));
  return map;
};

/** One stored label as it reads now; unknown labels pass through. */
export const displayEnvLabel = (label, versions) => (label ? envVersionDisplay(versions).get(label) || label : label);
