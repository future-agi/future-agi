/**
 * One environment version, the way people read it (RSI PRD: "a run is always
 * agent version × environment version, shown the same way on every screen").
 *
 * Underneath, PRD v7 keeps separate versions for each part of what is tested
 * and how it is judged — the world and its contract (E vN), the scenario set
 * (sN, each scenario major.minor.patch), the sub-goal catalogue (cN), the
 * evaluations (eN) and the simulated caller. Those stay exactly as they are:
 * runs pin them, Compare pairs scenarios by them.
 *
 * On the surface there is one number. A snapshot is the state of all those
 * parts at once, and any change to any of them mints the next one — like a
 * lockfile. People only ever see "environment v7" and, in words, what changed
 * in it — never the part numbers underneath.
 *
 * Derived from the logs each part already keeps, oldest first; nothing is
 * stored or migrated:
 *   - environment versions (versions.js)           world / contract changes
 *   - the scenario-set log (suiteLog.js)           scenarios added, edited …
 *   - eval changes (envState.evalChanges)          evals
 *   - sub-goal publishes (envState.subGoalEdits)   sub-goal catalogue
 *   - caller changes (envState.callerChanges)      simulated caller
 *   - credential changes (envState.credentialSets) secrets the world uses
 * Credentials are versioned with the environment, as the agent's are with
 * the agent: a version that changes only them is a minor one — v7 → v7.1 —
 * because the world behaves the same with a new key.
 * Changes written in the same moment (an approved nudge adds a handler and
 * three scenarios at once) are one snapshot. Agent versions never are.
 */
import { environmentVersions, currentEnvVersion, envChangeLabel } from "./versions";
import { suiteVersionLogOf, currentSuiteLabel, largestBumpLine } from "./suiteLog";
import { catalogVersionOf, evalVersionOf, simulatorOf } from "./envModel";
import { runEnvVersion } from "./toolFit";

const ms = (iso) => Date.parse(iso || "") || 0;
const num = (label) => parseInt(String(label || "").replace(/\D/g, ""), 10) || 0;
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/* Changes this close together came from one action. */
export const SAME_ACTION_MS = 2000;

/* World kinds that are the contract (tools and rules) rather than the world. */
const CONTRACT_KINDS = ["contract", "rules"];
const WORLD_KINDS = ["contract", "rules", "seed", "toolbehaviour", "actors"];

/* Which kind names a snapshot when one action changed several parts. */
const KIND_ORDER = ["recorded", "build", "world", "scenarios", "sub-goals", "evals", "caller", "credentials"];

export const SNAPSHOT_KIND_LABEL = {
  build: "First build",
  recorded: "Recorded",
  world: "World + contract",
  scenarios: "Scenarios",
  "sub-goals": "Sub-goals",
  evals: "Evaluations",
  caller: "Simulated caller",
  credentials: "Credentials",
};

const firstSentence = (text) => {
  const t = String(text || "").trim();
  const cut = t.search(/[.;](\s|$)/);
  return cut > 0 ? t.slice(0, cut) : t;
};

const callerModel = (sim) => (typeof sim === "string" ? sim.split(" · ")[0] : sim?.model || null);

/* ── the events, oldest first ────────────────────────────────────────────── */

const eventsOf = (env, envState) => {
  const out = [];
  const eOldest = [...environmentVersions(env, envState)].reverse();
  eOldest.forEach((v, i) => out.push({ type: "env", at: v.createdAt, v, first: i === 0, order: i }));
  suiteVersionLogOf(envState).forEach((l, i) => out.push({ type: "set", at: l.at, l, order: i }));
  (envState?.evalChanges || []).forEach((c, i) => out.push({ type: "eval", at: c.at, c, order: i }));
  (envState?.subGoalEdits || []).forEach((g, i) => out.push({ type: "catalog", at: g.at, g, order: i }));
  (envState?.callerChanges || []).forEach((c, i) => out.push({ type: "caller", at: c.at, c, order: i }));
  /* The first credential set is what the environment started with — only a
     change after it is an event. */
  (envState?.credentialSets || []).forEach((c, i) => { if (i > 0 && c.at) out.push({ type: "creds", at: c.at, c, order: i }); });
  const buildAt = ms(eOldest[0]?.createdAt);
  const TYPE_RANK = { env: 0, set: 1, catalog: 2, eval: 3, caller: 4, creds: 5 };
  /* Nothing comes before the first build; within one instant, the world first. */
  return out
    .map((e) => ({ ...e, t: e.first ? buildAt : Math.max(ms(e.at), buildAt) }))
    .sort((a, b) => (a.first ? -1 : b.first ? 1 : 0) || a.t - b.t || TYPE_RANK[a.type] - TYPE_RANK[b.type] || a.order - b.order);
};

/* ── folding events into snapshots ───────────────────────────────────────── */

/* What one event changes, and how it reads. */
const apply = (parts, e, counters) => {
  if (e.type === "env") {
    const changed = e.v.changed || [];
    const world = changed.filter((k) => WORLD_KINDS.includes(k));
    parts.env = e.v.label;
    const pieces = [];
    const details = [];
    if (e.first && e.v.origin === "pre-versioning") {
      pieces.push({ kind: "recorded", text: firstSentence(e.v.note) });
    } else if (e.first) {
      pieces.push({ kind: "build", text: firstSentence(e.v.note) || "First build" });
    } else if (world.length || !changed.length) {
      const head = world.some((k) => CONTRACT_KINDS.includes(k)) ? "Contract" : "World";
      pieces.push({ kind: "world", text: `${head}: ${firstSentence(e.v.note) || world.map(envChangeLabel).join(", ")}` });
    }
    details.push({ part: "env", label: e.v.label, text: [world.map(envChangeLabel).join(", "), e.v.note].filter(Boolean).join(" — ") });
    if (changed.includes("checks")) {
      pieces.push({ kind: "sub-goals", text: "Scenario checks rewritten" });
    }
    if (changed.includes("scoring")) {
      counters.eval += 1;
      parts.eval = `e${counters.eval}`;
      pieces.push({ kind: "evals", text: "Evaluations changed" });
      details.push({ part: "eval", label: parts.eval, text: "Evaluations changed with this world version" });
    }
    return { pieces, details };
  }
  if (e.type === "set") {
    parts.set = e.l.label || parts.set;
    return {
      pieces: [{ kind: "scenarios", text: e.l.summary || "Scenario set changed" }],
      details: [{ part: "set", label: parts.set, text: [e.l.summary, largestBumpLine(e.l.largestBump)].filter(Boolean).join(" · ") }],
    };
  }
  if (e.type === "eval") {
    counters.eval += 1;
    parts.eval = `e${counters.eval}`;
    return {
      pieces: [{ kind: "evals", text: `Evaluations changed${e.c.detail ? `: ${e.c.detail}` : ""}` }],
      details: [{ part: "eval", label: parts.eval, text: e.c.detail || "Evaluations changed" }],
    };
  }
  if (e.type === "catalog") {
    counters.catalog += 1;
    parts.catalog = `c${counters.catalog}`;
    const goal = e.g.name || String(e.g.id || "").replace(/^SG-(tool-|rule-)?/, "");
    const how = e.g.kind === "policy" || e.g.policy ? "policy change" : e.g.kind === "revert" || e.g.revertOf ? "reverted" : "grader fix";
    return {
      pieces: [{ kind: "sub-goals", text: `Sub-goal ${goal} updated` }],
      details: [{ part: "catalog", label: parts.catalog, text: `${goal} · ${how}${e.g.note ? ` — ${e.g.note}` : ""}` }],
    };
  }
  if (e.type === "creds") {
    parts.creds = e.c.label;
    const text = (e.c.changes || []).join(", ") || "References changed";
    return {
      pieces: [{ kind: "credentials", text: `Credentials: ${text}` }],
      details: [{ part: "creds", label: e.c.label, text: `${text} — by reference; the world behaves the same` }],
    };
  }
  /* caller */
  parts.caller = e.c.to || parts.caller;
  return {
    pieces: [{ kind: "caller", text: `Simulated caller: ${e.c.from} → ${e.c.to}` }],
    details: [{ part: "caller", label: e.c.to, text: `${e.c.from} → ${e.c.to}` }],
  };
};

/* "3 scenarios added; Evaluations e2" — the pieces, grouped by kind. */
const summarise = (pieces) => KIND_ORDER
  .map((k) => pieces.filter((p) => p.kind === k).map((p) => p.text))
  .filter((texts) => texts.length)
  .map((texts) => [...new Set(texts)].join("; "))
  .join(" · ");

const memo = new WeakMap();

/**
 * Every snapshot of one environment, oldest first:
 *   { label: "v7", n: 7, at, parts: { env, set, catalog, eval, caller },
 *     kind, kinds, summary, details: [{ part, label, text }] }
 */
export const envSnapshotsOf = (env, envState) => {
  if (envState && typeof envState === "object") {
    const byEnv = memo.get(envState);
    const hit = byEnv?.get(env?.id || "");
    if (hit) return hit;
  }
  const events = eventsOf(env, envState);
  const initialCaller = envState?.callerChanges?.[0]?.from || simulatorOf(env, envState).model;
  const parts = { env: events[0]?.v?.label || "v1", set: "s1", catalog: "c1", eval: "e1", caller: initialCaller, creds: envState?.credentialSets?.[0]?.label || null };
  const counters = { eval: 1, catalog: 1 };
  const groups = [];
  events.forEach((e) => {
    const last = groups[groups.length - 1];
    const joins = last && !e.first && e.t - last.lastT <= SAME_ACTION_MS;
    const { pieces, details } = apply(parts, e, counters);
    if (joins) {
      last.pieces.push(...pieces);
      last.details.push(...details);
      last.lastT = e.t;
      last.parts = { ...parts };
      return;
    }
    groups.push({ at: e.at || null, t: e.t, lastT: e.t, pieces, details, parts: { ...parts } });
  });
  if (!groups.length) {
    groups.push({ at: env?.adoptedAt || null, t: ms(env?.adoptedAt), lastT: 0, pieces: [{ kind: "build", text: "First build" }], details: [], parts: { ...parts } });
  }

  /* Parts that moved without a log entry (an add the set log never saw) are
     one more snapshot, so the newest always matches what a new run pins. */
  const now = {
    set: currentSuiteLabel(envState),
    catalog: catalogVersionOf(envState),
    eval: evalVersionOf(envState),
    caller: simulatorOf(env, envState).model,
  };
  const tail = groups[groups.length - 1];
  const drift = Object.keys(now).filter((k) => now[k] && now[k] !== tail.parts[k]
    && !(k !== "caller" && num(now[k]) < num(tail.parts[k])));
  if (drift.length) {
    const rows = (envState?.scenarios || []).filter((r) => !r.proposed && ms(r.addedAt || r.editedAt) > tail.lastT);
    const latest = rows.reduce((m, r) => Math.max(m, ms(r.addedAt || r.editedAt)), tail.lastT);
    const pieces = drift.map((k) => ({
      kind: { set: "scenarios", catalog: "sub-goals", eval: "evals", caller: "caller" }[k],
      text: k === "set"
        ? (rows.length ? `${plural(rows.length, "scenario")} added or edited` : "Scenario set changed")
        : k === "caller" ? `Simulated caller: ${tail.parts.caller} → ${now.caller}`
          : k === "eval" ? "Evaluations changed" : "Sub-goals changed",
    }));
    groups.push({
      at: latest ? new Date(latest).toISOString() : tail.at,
      t: latest,
      lastT: latest,
      pieces,
      details: pieces.map((p, i) => ({ part: drift[i], label: now[drift[i]], text: p.text })),
      parts: { ...tail.parts, ...Object.fromEntries(drift.map((k) => [k, now[k]])) },
    });
  }

  /* A version that only moved credentials is a minor of the one before it. */
  let major = 0;
  let minor = 0;
  const list = groups.map((g, i) => {
    const kinds = KIND_ORDER.filter((k) => g.pieces.some((p) => p.kind === k));
    const credentialsOnly = i > 0 && kinds.length === 1 && kinds[0] === "credentials";
    if (credentialsOnly) minor += 1;
    else { major += 1; minor = 0; }
    return {
      label: minor ? `v${major}.${minor}` : `v${major}`,
      n: i + 1,
      credentialsOnly,
      at: g.at,
      /* When the snapshot's changes were made — what a rollback reads the
         scenario set, sub-goals and evaluations at. */
      startMs: g.t,
      endMs: g.lastT,
      parts: g.parts,
      kind: kinds[0] || "world",
      kinds,
      summary: summarise(g.pieces) || "Changed",
      details: g.details,
    };
  });
  if (envState && typeof envState === "object") {
    const byEnv = memo.get(envState) || new Map();
    byEnv.set(env?.id || "", list);
    memo.set(envState, byEnv);
  }
  return list;
};

/** "v7" → the snapshot, or null. */
export const snapshotByLabel = (env, envState, label) => envSnapshotsOf(env, envState).find((s) => s.label === label) || null;

/**
 * The newest snapshot holding one world version — how an E label reads on
 * the surface ("E v3" → "v7" when v7 is the newest snapshot on that world).
 */
export const snapshotOfWorld = (env, envState, envLabel) => {
  if (!envLabel) return null;
  const list = envSnapshotsOf(env, envState);
  for (let i = list.length - 1; i >= 0; i -= 1) if (list[i].parts.env === envLabel) return list[i];
  return null;
};

/** The snapshot a world version was introduced in — for timelines of world changes. */
export const firstSnapshotOfWorld = (env, envState, envLabel) => (envLabel
  ? envSnapshotsOf(env, envState).find((s) => s.parts.env === envLabel) || null
  : null);

/** Whether a snapshot is the newest on its world — the one a world is named by. */
export const isWorldHead = (env, envState, snap) => !!snap && snapshotOfWorld(env, envState, snap.parts.env)?.label === snap.label;

/**
 * The snapshot new runs use. The newest, unless an older world is pinned —
 * then the newest snapshot on that world.
 */
export const currentEnvSnapshot = (env, envState) => {
  const list = envSnapshotsOf(env, envState);
  const newest = list[list.length - 1];
  const active = currentEnvVersion(env, envState)?.label;
  if (!active || newest?.parts.env === active) return newest;
  return snapshotOfWorld(env, envState, active) || newest;
};

/** The label the next change will mint — "v8". */
export const nextSnapshotLabel = (env, envState) => `v${Math.max(0, ...envSnapshotsOf(env, envState).map((s) => num(s.label.split(".")[0]))) + 1}`;

/* The parts a run pinned — only those it actually recorded. */
const runParts = (run, env, envState) => ({
  env: run?.envVersion || (run ? runEnvVersion(env, envState, run) : null),
  set: run?.suiteVersion || null,
  catalog: run?.catalogVersion || null,
  eval: run?.evalVersion || null,
  caller: callerModel(run?.simulator),
  creds: (typeof run?.credentialSet === "string" ? run.credentialSet : run?.credentialSet?.label) || null,
});

const runTime = (run) => ms(run?.startedAt || run?.createdAt || run?.finishedAt);

/**
 * The snapshot a run was taken on: the one whose parts match every pin the
 * run recorded (env version, scenario set, catalogue, evals, caller). When
 * several match — a run that recorded only its env version — the newest at or
 * before the run started. When none match, the newest on the run's world at
 * or before it started, then simply the newest at or before it started.
 */
export const snapshotOfRun = (run, env, envState) => {
  const list = envSnapshotsOf(env, envState);
  if (!list.length) return null;
  if (run?.envSnapshot) {
    const named = list.find((s) => s.label === run.envSnapshot);
    if (named) return named;
  }
  const pins = runParts(run, env, envState);
  const t = runTime(run);
  const pick = (cands) => {
    if (!cands.length) return null;
    if (!t) return cands[cands.length - 1];
    const before = cands.filter((s) => ms(s.at) <= t);
    return before.length ? before[before.length - 1] : cands[0];
  };
  const keys = Object.keys(pins).filter((k) => pins[k]);
  return pick(list.filter((s) => keys.every((k) => s.parts[k] === pins[k])))
    || pick(list.filter((s) => s.parts.env === pins.env))
    || pick(list)
    || list[list.length - 1];
};

/** "v7" for a run, or null. */
export const snapshotLabelOf = (run, env, envState) => snapshotOfRun(run, env, envState)?.label || null;

/**
 * The snapshot an improvement was frozen on. New records carry
 * `frozen.snapshot`; older ones are read from the pins they froze.
 */
export const snapshotOfImprovement = (record, env, envState) => {
  if (!record) return null;
  const frozen = record.frozen || {};
  const stored = frozen.snapshot?.label ? snapshotByLabel(env, envState, frozen.snapshot.label) : null;
  if (stored) return stored;
  if (frozen.snapshot?.label) return { label: frozen.snapshot.label, parts: frozen.snapshot.parts || {}, summary: "", details: [] };
  const fromRun = (envState?.runs || []).find((r) => r.id === record.fromRunId) || null;
  return snapshotOfRun({
    envVersion: fromRun?.envVersion || frozen.compat?.envVersion || null,
    suiteVersion: frozen.suiteVersion || fromRun?.suiteVersion || null,
    catalogVersion: frozen.catalogVersion || null,
    evalVersion: frozen.evalVersion || null,
    simulator: frozen.simulator || null,
    createdAt: fromRun?.startedAt || fromRun?.finishedAt || record.createdAt,
  }, env, envState);
};

/** What an improvement freezes: `{ label, parts }` of the snapshot it starts on. */
export const frozenSnapshotOf = (env, envState) => {
  const s = currentEnvSnapshot(env, envState);
  return s ? { label: s.label, parts: { ...s.parts } } : null;
};

/* What one part's move reads as — in words, never its internal number. */
const PART_MOVED = {
  env: () => "world and contract changed",
  set: () => "scenario set changed",
  catalog: () => "sub-goals changed",
  eval: () => "evaluations changed",
  caller: (a, b) => `simulated caller → ${b.parts.caller}`,
};

/** Which parts differ between two snapshots, in words — "evaluations changed", "simulated caller → gpt-4o-mini". */
export const snapshotDiff = (a, b) => {
  if (!a?.parts || !b?.parts) return [];
  return Object.keys(PART_MOVED)
    .filter((k) => a.parts[k] !== b.parts[k])
    .map((k) => ({ part: k, label: PART_MOVED[k](a, b) }));
};

/**
 * The version history as one list, newest first — each snapshot with how
 * many runs used it, whether runs use it now, and whether it is the newest on
 * its world (only those can be switched to or viewed: an older snapshot on
 * the same world is the same world with an older scenario set or evals).
 */
export const snapshotHistoryOf = (env, envState) => {
  const list = envSnapshotsOf(env, envState);
  const current = currentEnvSnapshot(env, envState)?.label;
  const counts = new Map();
  (envState?.runs || []).filter((r) => !r.synthetic).forEach((r) => {
    const l = snapshotLabelOf(r, env, envState);
    if (l) counts.set(l, (counts.get(l) || 0) + 1);
  });
  const heads = new Set(list.map((s) => snapshotOfWorld(env, envState, s.parts.env)?.label));
  return [...list].reverse().map((s, i) => ({
    ...s,
    runs: counts.get(s.label) || 0,
    current: s.label === current,
    latest: i === 0,
    worldHead: heads.has(s.label),
  }));
};

/**
 * Compare's headline in environment-version terms. Same snapshot → "Same
 * environment (v7)". Different → "Environment v6 → v7 · 182 of 190 scenarios
 * unchanged, compared · 8 changed, shown apart" — the pair classes
 * (pairs.js) still decide every scenario; this only names the two versions.
 */
export const compareEnvHeadline = (a, b, counts, total) => {
  if (!a?.label || !b?.label || !counts) return null;
  if (a.label === b.label) return { same: true, from: a.label, to: b.label, text: `Same environment (${a.label})`, diff: [] };
  const compared = (counts.exact || 0) + (counts.regraded || 0) + (counts.rechecked || 0);
  const moved = (counts.regraded || 0) + (counts.rechecked || 0);
  /* Changed between the versions vs simply not in both runs — said apart. */
  const changed = (counts.changed || 0) + (counts["not-applicable"] || 0);
  const notInBoth = (counts.new || 0) + (counts.removed || 0) + (counts.archived || 0);
  const apart = Math.max(0, (total || 0) - compared - changed - notInBoth);
  return {
    same: false,
    from: a.label,
    to: b.label,
    text: [
      `Environment ${a.label} → ${b.label}`,
      moved
        ? `${compared} of ${total} scenarios compared (${counts.exact || 0} unchanged, ${moved} re-graded or rechecked)`
        : `${compared} of ${total} scenarios unchanged, compared`,
      changed + apart ? `${changed + apart} changed, shown apart` : null,
      notInBoth ? `${notInBoth} in only one run` : null,
    ].filter(Boolean).join(" · "),
    diff: snapshotDiff(a, b),
  };
};
