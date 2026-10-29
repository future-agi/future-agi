/**
 * Versions, and what pairs with what.
 *
 * The product goals ask for two things that pull in the same direction: an
 * environment must serve more than one agent version (G1), and environment
 * creation must not be welded to scenario creation (G7). Both fall out of one
 * rule — scenarios belong to the environment, and a run is a pairing:
 *
 *     run = environment version × agent version
 *
 * So the same proved scenarios can be pointed at v3 of an agent and then at
 * v2, and the difference is attributable to the agent rather than to a
 * scenario someone rewrote in between.
 */

const iso = (daysAgo) => new Date(Date.now() - daysAgo * 86400000).toISOString();

/* ── environment versions ────────────────────────────────────────────────── */

/**
 * Why an environment gets a new version: the world changed. Seed data, tool
 * handlers, rules. Scenarios carry the version they were proved against, so a
 * world change that invalidates a proof is visible rather than silent.
 */
export const environmentVersions = (env, envState) => {
  /* Minted versions win. An environment with none recorded has exactly one:
     its first build. (It used to be handed an invented three-version history,
     so an environment created a minute ago claimed a v1 "read from the agent
     source" three weeks old.) Environments with a real history — the seeded
     demo ones — carry it in their state like any other. */
  const stored = envState?.envVersions;
  const list = stored?.length ? [...stored].reverse() : [{
    id: `${env?.id}-v1`,
    label: "v1",
    createdAt: env?.adoptedAt || new Date().toISOString(),
    note: envState?.agent ? "First build, read from the agent." : "First build.",
    scenarios: envState?.scenarios?.length || 0,
    changed: ["contract", "seed"],
    builtFor: "v1",
  }];

  /*
    Stamp `.current` based on the active version pointer, so the version
    picker and the Settings list agree on which one is live. Without a
    pin, the newest is current — same behaviour as before switching
    existed.
  */
  const active = envState?.activeEnvVersion || list[0]?.label;
  return list.map((v) => ({ ...v, current: v.label === active }));
};

/**
 * A history for the seeded demo environments, oldest first — the same three
 * steps the fallback used to invent for every environment, now stated once
 * for the environments that actually have that history.
 */
/*
  A version recorded when versioning started: the agent or environment as it
  stood then. Nothing before it is known, it was never checked, and it reads
  as recorded — not as a first build or a first connect.
*/
export const PRE_VERSIONING_AGENT_NOTE = "Recorded from before versioning — the agent as it stood when versioning started.";
export const PRE_VERSIONING_ENV_NOTE = "Recorded from before versioning — the environment as it stood when versioning started.";
export const recordedBeforeVersioning = (v, note) => ({ ...v, origin: "pre-versioning", note });

export const seededEnvHistory = (env, scenarioCount) => {
  const back = (n) => Math.max(1, Math.round((scenarioCount || 0) * n));
  return [
    {
      id: `${env?.id}-v1`, label: "v1", createdAt: iso(24),
      note: "First build, read from the agent source.",
      scenarios: back(0.55), changed: ["contract", "seed"], builtFor: "v1",
    },
    {
      id: `${env?.id}-v2`, label: "v2", createdAt: iso(11),
      note: "Moved a rule out of the prompt and into the world, so breaking it now fails rather than reads badly.",
      scenarios: back(0.78), changed: ["rules"], builtFor: "v1",
    },
    {
      id: `${env?.id}-v3`, label: "v3", createdAt: iso(2),
      note: "Seeded the refusal paths so the scenarios that should be declined have something to be declined against.",
      scenarios: scenarioCount || 0, changed: ["seed", "checks"], builtFor: "v1",
    },
  ];
};

/**
 * What changing the world can consist of.
 *
 * Named rather than free-text because the *kind* of change decides which
 * proofs survive it: reseeding can strip the state a scenario presumes, and
 * rewriting checks can turn a check that used to fail on an empty run into one
 * that passes. A rules change does neither — it changes how a run is judged,
 * not whether it can be staged.
 */
/* `contract` is the tools — the id stays because stored versions carry it. */
export const ENV_CHANGES = [
  { id: "seed", label: "Seed data changed", invalidates: true, blurb: "New or regenerated data — the state a scenario presumes may be gone." },
  { id: "checks", label: "Scenario checks changed", invalidates: true, blurb: "A scenario's proof checks were rewritten — one proved to fail on an empty run may now pass." },
  { id: "contract", label: "Tools changed", invalidates: true, blurb: "The reference solution may no longer run." },
  /* What a tool does inside the world — reads or writes, sandbox or stub. Its
     interface is unchanged, so it's a world edit (a minor version), not a
     contract change. */
  { id: "toolbehaviour", label: "Tool behaviour changed", invalidates: true, blurb: "What a tool reads or writes changed — the reference solution may play out differently." },
  { id: "rules", label: "Rules changed", invalidates: false, blurb: "Graded differently; every scenario can still be staged." },
  { id: "scoring", label: "Evaluations changed", invalidates: false, blurb: "Evaluations or thresholds changed — runs before and after were graded differently." },
  { id: "actors", label: "Actors changed", invalidates: false, blurb: "Who else acts in the world changed — the episodes play out differently." },
  /* Tools added from an MCP server: nothing a scenario ran on changed, so no proof goes stale. */
  { id: "tools-added", label: "Tools added", invalidates: false, blurb: "The world answers more tools — every scenario still runs as it was proved." },
  /* Removed: a scenario that calls one can no longer play out as proved. */
  { id: "tools-removed", label: "Tools removed", invalidates: true, blurb: "The world stops answering some tools — scenarios that call them can't run as proved." },
  /* What the sandbox lets the agent reach: a call that worked may now be blocked, or the other way. */
  { id: "network", label: "Egress changed", invalidates: false, blurb: "Which public hosts the agent may reach while it runs changed — calls to them may now succeed or be blocked." },
];

/** What a change id reads as — never the raw id ("contract" means tools). */
export const envChangeLabel = (id) => ENV_CHANGES.find((c) => c.id === id)?.label || id;

/* ── edits since the pinned version ──────────────────────────────────────── */

/**
 * Edits to the world since the pinned version.
 *
 * A version that no run has used yet is still being set up, so an edit just
 * lands on it. Once a run has pinned it, an edit is a change to a world
 * someone's result depends on — it's listed here until it's saved as the
 * next version.
 */
export const pendingEnvChanges = (envState) => envState?.unsavedEnvChanges || [];

const pinnedByRun = (envState) => {
  const label = currentEnvVersion(null, envState)?.label;
  return (envState?.runs || []).some((r) => !r.envVersion || r.envVersion === label);
};

/** The environment with the tools a run started now uses: the pinned version as saved. */
export const savedWorldEnv = (env, envState) => (env
  ? { ...env, tools: currentEnvVersion(env, envState)?.tools || env.firstBuildTools || env.tools || [] }
  : env);

/** No run has pinned the current version yet, so an edit lands on it in place. */
export const editsLandInPlace = (envState) => !pinnedByRun(envState);

/**
 * Tools added or removed since the pinned version, applied to `tools`: the
 * world as it will be once the edits are saved. Each pending edit may carry
 * `addTools` (world tools) and `removeTools` (names), in the order made.
 */
export const applyPendingTools = (tools, envState) => pendingEnvChanges(envState).reduce((list, c) => {
  const gone = new Set(c.removeTools || []);
  const kept = gone.size ? list.filter((t) => !gone.has(t.name)) : list;
  const have = new Set(kept.map((t) => t.name));
  return [...kept, ...(c.addTools || []).filter((t) => !have.has(t.name))];
}, tools || []);

/**
 * Still a draft: nothing has made a version yet — no run has pinned one and
 * none was saved — so edits land in place. The first run makes it v1.
 */
export const isEnvDraft = (envState) => !(envState?.runs || []).some((r) => !r.synthetic)
  && (envState?.envVersions || []).length <= 1 && !pendingEnvChanges(envState).length;

/** Patch fragment that records one edit — `{}` while no run pins the version.
    The first edit after a save also keeps the world's settings as they were
    (`draftBase`), which is what Discard puts back. */
export const envChangePatch = (envState, kind, detail) => (pinnedByRun(envState)
  ? {
    unsavedEnvChanges: [...pendingEnvChanges(envState), { kind, detail, at: new Date().toISOString() }],
    ...(!pendingEnvChanges(envState).length && { draftBase: worldConfigOf(envState) }),
  }
  : {});

/**
 * Throw the unsaved edits away: the world's settings go back to how they
 * were saved, and tool additions/removals (which live only in the edit list)
 * go with the list. Null when there's nothing to go back to — a draft begun
 * before settings were kept, on a version that didn't keep them either.
 */
export const discardEnvChangesPatch = (env, envState) => {
  const base = envState?.draftBase || currentEnvVersion(env, envState)?.config;
  if (!pendingEnvChanges(envState).length || !base) return null;
  return {
    ...Object.fromEntries(WORLD_CONFIG_KEYS.map((k) => [k, base[k]])),
    unsavedEnvChanges: [],
    draftBase: undefined,
  };
};

/** Save the pending edits as the next version, which runs then use. */
export const saveEnvChangesPatch = (env, envState) => {
  const list = envState?.envVersions?.length
    ? envState.envVersions
    : [...environmentVersions(env, envState)].reverse();
  const minted = nextEnvVersion(env, envState);
  return { envVersions: [...list, minted], activeEnvVersion: minted.label, unsavedEnvChanges: [], draftBase: undefined };
};

/** The next environment version, given what already exists. */
export const nextEnvVersion = (env, envState, { changed = [], note, now, from, revertedTo, tools } = {}) => {
  const list = environmentVersions(env, envState);
  const n = list.length + 1;
  /* A new version starts from the world it replaces — the pinned one unless
     told otherwise — so tools the world learned in a rebuild are not lost to
     a rules change made afterwards. */
  const base = (from && list.find((v) => v.label === from)) || list.find((v) => v.current) || list[0];
  /* One draft per environment: hand edits still unsaved are part of
     whatever version is made next, whoever makes it. The caller clears them
     (`unsavedEnvChanges: []`). */
  const pending = pendingEnvChanges(envState);
  /* Tools added or removed while unsaved join the version like any other edit. */
  const toolEdits = pending.some((c) => c.addTools || c.removeTools);
  const startTools = tools || base?.tools || (toolEdits ? (env?.firstBuildTools || env?.tools || []) : null);
  const nextTools = startTools && toolEdits ? applyPendingTools(startTools, envState) : startTools;
  return {
    ...(nextTools && { tools: nextTools }),
    builtFor: base?.builtFor || "v1",
    id: `${env?.id}-v${n}`,
    label: `v${n}`,
    createdAt: now || new Date().toISOString(),
    note: [note || (pending.length ? null : "World changed."), ...pending.map((c) => c.detail).filter(Boolean)].filter(Boolean).slice(0, 3).join("; "),
    scenarios: envState?.scenarios?.length || 0,
    changed: [...new Set([...changed, ...pending.map((c) => c.kind)])],
    /* Where it came from: the version before it, or the one it rolls back to. */
    ...(base?.label && { from: base.label }),
    ...(revertedTo && { revertedTo }),
    /* The world's settings as they stand — what a rollback to it restores. */
    config: worldConfigOf(envState),
    /* Minted after the scenarios existed. Marked, because a proof cannot be
       retroactively made against a version that did not exist when it was
       proved — see provedAgainst in proofs.js. */
    minted: true,
  };
};

/*
  The world's settings that aren't tools or contract text: how each tool
  answers and fails, which tools share data, the actors and their edits, the
  seed, what a run may never call, whether the sandbox resets. Kept on every
  version, so rolling back restores them too.
*/
export const WORLD_CONFIG_KEYS = ["toolEnv", "toolLinks", "actors", "actorEdits", "twinBacking", "twinResetBetween", "toolArgEdits", "egressDomains"];
export const worldConfigOf = (envState) => Object.fromEntries(WORLD_CONFIG_KEYS
  .filter((k) => envState?.[k] !== undefined)
  .map((k) => [k, envState[k]]));

/**
 * The versions whose contract changes hold in `label` (default: the current
 * one) — itself, then back through each version's parent. A rollback's parent
 * is the version it rolled back to, so what changed in between stops
 * applying, while the history itself stays.
 */
export const envLineage = (env, envState, label) => {
  const list = environmentVersions(env, envState);
  const byLabel = new Map(list.map((v) => [v.label, v]));
  const out = new Set();
  let cur = byLabel.get(label || (list.find((v) => v.current) || list[0])?.label);
  while (cur && !out.has(cur.label)) {
    out.add(cur.label);
    cur = byLabel.get(cur.revertedTo || `v${versionNumber(cur.label) - 1}`);
  }
  return { has: (l) => out.has(l), known: (l) => byLabel.has(l) };
};

/** Does a logged contract change (rule, use case, argument) hold now? Unversioned or unknown entries always do. */
export const holdsInEnv = (lineage, envVersion) => !envVersion || !lineage.known(envVersion) || lineage.has(envVersion);

/**
 * The scenarios in the set at an environment version — what a run takes and
 * every count means: not archived, not a proposal waiting to be added, and
 * added by that version. One selector, so Overview, the tab and the version
 * bar can't disagree.
 */
export const scenariosInSet = (envState, envLabel) => (envState?.scenarios || []).filter((sc) => !sc.proposed
  && !sc.archived && !sc.retired
  && (!sc.addedInEnv || !envLabel || versionNumber(sc.addedInEnv) <= versionNumber(envLabel)));

/* ── agent versions ──────────────────────────────────────────────────────── */

/**
 * Versions of the thing under test. These are the customer's, not ours — they
 * point us at the agent as it runs in production and the platform makes the
 * copy a simulation drives (a sandbox build, a labelled provider clone, an
 * exported definition — see replication.js), so a version here is a label
 * plus how to reach it.
 *
 * A version is minted when the agent changes, and a run pins whichever one was
 * current when it started. It is emphatically *not* derived from the run count:
 * that made a second run of an unchanged agent claim to be a new version, and
 * made re-running the same version to check a flaky scenario impossible to
 * express. Change, then run — those are two events, and only the first one
 * makes a version.
 */
const firstAgentVersion = () => ({
  id: "agent-v1",
  label: "v1",
  note: "First version connected.",
  reach: "endpoint",
  createdAt: new Date().toISOString(),
});

/** The number in a version label — what phrasing, pinning and ordering key
    off. A credentials-only version reads "v5.1" and sorts right after v5. */
export const versionNumber = (label) => {
  const m = /(\d+)(?:\.(\d+))?/.exec(String(label || ""));
  if (!m) return 1;
  return (parseInt(m[1], 10) || 1) + (m[2] ? parseInt(m[2], 10) / 1000 : 0);
};

/** The whole number of a label — "v5.1" → 5. New versions count from it. */
export const majorOf = (label) => Math.floor(versionNumber(label));

/** The next whole version after every label given — "v6" after v5 and v5.1. */
export const nextMajorLabel = (labels) => `v${Math.max(0, ...labels.map(majorOf)) + 1}`;

/*
  Credentials are versioned together with what holds them (team decision):
  replacing a key makes a version. But a key doesn't change how anything
  behaves, so it's a minor one — v5 → v5.1 — that carries nothing to check:
  no card, no rebuild, and it counts as the same behaviour as its base.
*/
export const isCredentialsVersion = (v) => v?.kind === "credentials";

/** "v5.1" when credentials change on v5 (or "v5.2" after v5.1). */
export const nextCredentialsLabel = (labels, baseLabel) => {
  const major = majorOf(baseLabel);
  const minors = labels.filter((l) => majorOf(l) === major).map((l) => Math.round((versionNumber(l) - major) * 1000));
  return `v${major}.${Math.max(0, ...minors) + 1}`;
};

/**
 * The version record a credentials change makes: on top of `baseLabel` (the
 * version in production), numbered as its minor, carrying what moved — by
 * reference, never a value. `what` reads "API key reference replaced".
 */
export const credentialsVersionRecord = (versions, baseLabel, { what, at = new Date().toISOString() } = {}) => {
  const label = nextCredentialsLabel((versions || []).map((v) => v.label), baseLabel);
  return {
    id: `agent-${label}`,
    label,
    kind: "credentials",
    sameAs: behaviourLabelOf(versions, baseLabel),
    note: `Credentials only — ${what || "credentials changed"}. Behaves as ${behaviourLabelOf(versions, baseLabel)}, so there's nothing to check or rebuild.`,
    reach: "credentials",
    origin: "credentials",
    createdAt: at,
    applied: [],
    basedOnVersion: baseLabel,
  };
};

/** The version whose behaviour `label` has — itself, or a credentials version's base. */
export const behaviourLabelOf = (versions, label) => {
  const v = (versions || []).find((x) => x.label === label);
  return isCredentialsVersion(v) ? v.sameAs || `v${majorOf(label)}` : label;
};

/** Every agent version this environment knows about, oldest first. No agent,
    no versions — never an invented v1 for a run to pin. */
export const agentVersions = (envState) => {
  const stored = envState?.agentVersions;
  if (stored?.length) return stored;
  return envState?.agent ? [firstAgentVersion()] : [];
};

/*
  Which agent version an environment tests. By default it follows the agent's
  production version — the newest that isn't an unreleased draft, or the one
  an admin made active — so every push is tested against the same suite with
  no switching. Pinning (an explicit switch to another version) holds it on
  that version until someone follows production again. A draft an
  improvement made is never followed: it isn't live until it's released.
*/
const releasedIn = (envState) => new Set((envState?.releases || []).map((r) => r.version));
const isLive = (v, released) => v.status !== "draft" || released.has(v.label);

/** The environment's production version. Undefined when there's no agent. */
export const productionAgentVersion = (envState) => {
  const list = agentVersions(envState);
  if (!list.length) return undefined;
  const made = envState?.agent?.values?.activeVersion?.label;
  const madeActive = made && list.find((v) => v.label === made);
  if (madeActive) return madeActive;
  const released = releasedIn(envState);
  return [...list].reverse().find((v) => isLive(v, released)) || list[list.length - 1];
};

/** Is the environment held on one version rather than following production? */
export const isAgentPinned = (envState) => !!envState?.agentVersionPinned && !!envState?.activeAgentVersion
  && agentVersions(envState).some((v) => v.label === envState.activeAgentVersion);

/** The environment's default agent version: "production" (follows it) or the label it's held on. */
export const defaultAgentVersionOf = (envState) => (isAgentPinned(envState) ? envState.activeAgentVersion : "production");

/** The one the next run will use — the pin if there is one, else production.
    Undefined when there's no agent. */
export const currentAgentVersion = (envState) => {
  const list = agentVersions(envState);
  if (!list.length) return undefined;
  if (isAgentPinned(envState)) return list.find((v) => v.label === envState.activeAgentVersion);
  return productionAgentVersion(envState);
};

/** Is `label` a draft that hasn't been released? Registry copies carry the
    answer (`pendingDraft`); environment copies carry the status. */
export const isPendingDraft = (envState, label) => {
  const v = agentVersions(envState).find((x) => x.label === label);
  if (!v) return false;
  if (v.pendingDraft !== undefined) return !!v.pendingDraft;
  return !isLive(v, releasedIn(envState));
};

/**
 * The patch for testing `label` here: following production when it is the
 * production version (or none is given), else pinned to it. A draft is never
 * what an environment tests — asked to pin one, it follows production.
 */
export const agentPinPatch = (envState, label) => {
  const prod = productionAgentVersion(envState)?.label || null;
  return !label || label === prod || isPendingDraft(envState, label)
    ? { activeAgentVersion: prod, agentVersionPinned: false }
    : { activeAgentVersion: label, agentVersionPinned: true };
};

/**
 * The next version.
 *
 * Product concept shift: a version isn't just a label attached to whatever
 * happens to be at the endpoint any more. When we mint one from an optimize
 * flow we know *which* changes went into it, and we know which version they
 * were applied on top of. Both travel on the version so a reader six weeks
 * from now can answer "what was actually different about v3" without digging
 * through the run that produced it.
 *
 *   applied         — the code changes bundled into this version, each with
 *                     its filepath and diff. Empty for versions minted by
 *                     "connect to a different endpoint".
 *   basedOnVersion  — the label of the version these changes were applied on
 *                     top of. Usually the previous one, but pinning it makes
 *                     branching possible if a user rolls back to v2 and edits
 *                     from there.
 *   fromRunId       — the run whose diagnosis produced these changes, so the
 *                     evidence lives beside the outcome.
 */
export const nextAgentVersion = (envState, {
  note, reach = "endpoint", now, applied = [], fromRunId = null, basedOnVersion = null, tools = null,
} = {}) => {
  const list = agentVersions(envState);
  /* Numbered past the highest label, not the count — a count can collide. */
  const n = Math.max(0, ...list.map((v) => majorOf(v.label))) + 1;
  return {
    /* Only when this version's tools are known to differ; otherwise it
       inherits the tools of the version it was built on. */
    ...(tools && { tools }),
    id: `agent-v${n}`,
    label: `v${n}`,
    note: note || "Modified between runs.",
    reach,
    createdAt: now || new Date().toISOString(),
    applied,
    /* The version these changes were made on — the one being tested now,
       not simply the newest. */
    basedOnVersion: basedOnVersion || currentAgentVersion(envState)?.label || "v1",
    fromRunId,
  };
};

/** Newest first, with each version's run history attached — for list UIs. */
export const agentVersionsWithRuns = (envState) => {
  const runs = envState?.runs || [];
  /* "current" is the one the next run tests — the pin, or production —
     never simply the newest (which can be an unreleased draft). */
  const next = currentAgentVersion(envState)?.label;
  return [...agentVersions(envState)].reverse().map((v) => ({
    ...v,
    current: v.label === next,
    runs: runs.filter((r) => r.agentVersion === v.label).length,
  }));
};

/**
 * The env version the workspace is currently working off.
 *
 * Version switching is now a first-class action: `envState.activeEnvVersion`
 * pins whichever version the user chose to work from. If nothing is pinned
 * the newest is treated as active — same behaviour as before switching
 * existed, so environments that never invoked the picker keep reading the
 * way they used to.
 *
 * Runs stamp whichever version was active when they started; the version
 * list marks the active one with `.current` so the header pin and the
 * Settings list read the same story.
 */
export const currentEnvVersion = (env, envState) => {
  const list = environmentVersions(env, envState);
  const pinned = envState?.activeEnvVersion;
  const explicit = pinned ? list.find((v) => v.label === pinned) : null;
  return explicit || list.find((v) => v.current) || list[0];
};

