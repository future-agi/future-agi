/**
 * Which agent versions can be tested on which environment versions.
 *
 * Tools belong to the agent version; the world is what answers them. The
 * environment is built from v1, so its world starts out answering exactly v1's
 * tools. A later version can bring a tool the world has never seen — and until
 * the world learns it, every call to that tool goes unanswered and the run
 * scores the agent for the environment's gap.
 *
 * One rule, used everywhere:
 *
 *   an agent version fits an environment version when the world can answer
 *   every tool the agent calls.
 *
 * It is one-directional on purpose. An older agent that calls fewer tools fits
 * a newer world (it simply never calls the extra ones), so re-running v1 on the
 * rebuilt environment is how v1 and v2 get compared on the same test. A newer
 * agent does not fit the older world.
 */
import { getRows } from "./scenarios";
import { actorsOf, getActor } from "./actors";
import { inferTools, sandboxScenario } from "./toolInference";
import { agentVersions, environmentVersions, currentEnvVersion, currentAgentVersion, nextEnvVersion, versionNumber } from "./versions";
import { toolFaultsOf } from "./toolEnv";

const pick = (t) => ({ ...t, desc: t.desc || "" });

/** The tools the environment was first built with — v1's tools. */
export const baseTools = (env) => (env?.firstBuildTools || env?.tools || []).map(pick);

/**
 * The environment as the pinned version sees it: `tools` is what that world
 * answers. Panels that read `env.tools` then describe the world a run would
 * actually use, not the first build's. The first build's list is kept on
 * `firstBuildTools`, which is what "v1's tools" still means.
 */
export const withPinnedWorld = (env, envState) => (env
  ? { ...env, firstBuildTools: env.firstBuildTools || env.tools || [], tools: worldToolsFor(env, envState) }
  : env);

/** Tools the world answers at an environment version (the pinned one by default). */
export const worldToolsFor = (env, envState, envLabel) => {
  const list = environmentVersions(env, envState);
  const version = envLabel
    ? list.find((v) => v.label === envLabel)
    : currentEnvVersion(env, envState);
  return version?.tools || baseTools(env);
};

/**
 * Tools an agent version calls. A version that declares none inherits the
 * tools of the version it was built on (`basedOnVersion`, else the one before
 * it in the list) — most versions change a prompt, not the toolset. Following
 * the parent rather than list order keeps a version branched off v1 after a
 * rollback from picking up v3's tools.
 */
const parentOf = (list, label) => {
  const idx = list.findIndex((v) => v.label === label);
  if (idx < 0) return null;
  const own = list[idx].basedOnVersion;
  if (own && own !== label && list.some((v) => v.label === own)) return own;
  return idx > 0 ? list[idx - 1].label : null;
};

export const agentToolsFor = (env, envState, agentLabel) => {
  const list = agentVersions(envState);
  let label = agentLabel || currentAgentVersion(envState)?.label;
  for (let guard = 0; label && guard < 50; guard += 1) {
    const v = list.find((x) => x.label === label);
    if (!v) break;
    if (v.tools) return v.tools;
    label = parentOf(list, label);
  }
  return baseTools(env);
};

/** Tools this version added over the one it was built on. */
export const newToolsIn = (env, envState, agentLabel) => {
  const parent = parentOf(agentVersions(envState), agentLabel);
  if (!parent) return [];
  const before = new Set(agentToolsFor(env, envState, parent).map((t) => t.name));
  return agentToolsFor(env, envState, agentLabel).filter((t) => !before.has(t.name));
};

/** Does this agent version fit this environment version? */
export const toolFit = (env, envState, { agent, envVersion } = {}) => {
  const agentLabel = agent || currentAgentVersion(envState)?.label;
  const envLabel = envVersion || currentEnvVersion(env, envState)?.label;
  const world = worldToolsFor(env, envState, envLabel);
  const answers = new Set(world.map((t) => t.name));
  const calls = agentToolsFor(env, envState, agentLabel);
  const missing = calls.filter((t) => !answers.has(t.name));
  return { agent: agentLabel, envVersion: envLabel, fits: !missing.length, missing, calls, world };
};

/** The newest environment version, and whether a given agent fits it. */
export const latestEnvVersion = (env, envState) => {
  const list = environmentVersions(env, envState);
  return [...list].sort((a, b) => versionNumber(b.label) - versionNumber(a.label))[0];
};

/** The environment version where each tool first became answerable. */
export const toolSince = (env, envState) => {
  const list = [...environmentVersions(env, envState)].sort((a, b) => versionNumber(a.label) - versionNumber(b.label));
  const since = {};
  const base = baseTools(env);
  list.forEach((v) => {
    (v.tools || base).forEach((t) => { if (!since[t.name]) since[t.name] = v.label; });
  });
  return since;
};

/** Which agent versions call each tool — for the World tab. */
export const toolCallers = (env, envState) => {
  const out = {};
  agentVersions(envState).forEach((v) => {
    agentToolsFor(env, envState, v.label).forEach((t) => {
      (out[t.name] = out[t.name] || []).push(v.label);
    });
  });
  return out;
};

/**
 * Rebuild the environment for an agent version: the world learns the tools
 * that version calls, the existing scenarios are re-derived against the new
 * world, and a batch of scenarios is added for each new tool so it is actually
 * exercised. Older environment versions are left exactly as they were — runs
 * pinned to them stay readable.
 */
export const rebuildForAgent = (env, envState, agentLabel) => {
  /* Always from the newest world — rebuilding from an older pin would
     re-learn tools a later version already answers. */
  const latest = latestEnvVersion(env, envState);
  const { missing, world } = toolFit(env, envState, { agent: agentLabel, envVersion: latest?.label });
  const list = envState?.envVersions?.length
    ? envState.envVersions
    : [...environmentVersions(env, envState)].reverse();
  const tools = [...world, ...missing.map(pick)];
  const names = missing.map((t) => t.name);
  const version = {
    ...nextEnvVersion(env, envState, {
      from: latest?.label,
      changed: ["contract", "seed"],
      note: `World learned ${names.join(", ")} for agent ${agentLabel}`,
    }),
    tools,
    builtFor: agentLabel,
  };

  /* One small batch per new tool, from the same generator the core pack uses,
     with ids that cannot collide with the originals — and never a second batch
     for a tool an earlier rebuild already covered. Proved against the version
     they were written for, so they read as fresh rather than stale.
     Proposed, not added: "Nothing is added to an environment automatically
     after it is built. Every source proposes scenarios" (RSI PRD) — they wait
     in Needs input until someone adds them. */
  const covered = new Set((envState?.scenarios || []).map((sc) => sc.newTool).filter(Boolean));
  const now = new Date().toISOString();
  const added = missing.filter((tool) => !covered.has(tool.name))
    .flatMap((tool) => getRows(`${env.id}::core`, { ...env, tools: [tool] })
      .map((row) => ({
        ...row,
        id: `${row.id}-${tool.name}-${version.label}`,
        addedInEnv: version.label,
        newTool: tool.name,
        requiredTools: [tool.name],
        /* Its own short list, like the core pack's ordinary rows — derived from
           the id, the rebuild's suffixed ids landed on the 8–15-step "deep"
           list and read as 13–15 sub-goals beside siblings' 4–6. */
        subGoals: [
          "Understand what the caller is trying to get done",
          ...(row.persona?.name ? ["Verify who is asking, before touching any account state"] : []),
          `Call ${tool.name} with the arguments the task actually needs`,
          "Report the result to the caller and close the turn cleanly",
        ],
        provedAgainst: version.label,
        provedAt: now,
        /* Its own batch: the environment added these, and says why. */
        addedAt: now,
        addedBy: { kind: "system", id: "sys", name: "System" },
        source: "rebuild",
        batchId: `batch_rebuild_${version.label}`,
        batchNote: `when the environment learned ${names.join(", ")} for agent ${agentLabel}`,
      }))
      /* scenarioSources.asProposal's shape, inline — importing it here
         would loop back through agentRegistry to this file. */
      .map((row) => ({ ...row, proposed: row.proposed || { at: now, source: "rebuild" } })));

  /* Proposals aren't part of the version until someone adds them. */
  version.scenarios = (envState?.scenarios || []).filter((r) => !r.proposed).length;

  return {
    version,
    added,
    patch: {
      envVersions: [...list, version],
      activeEnvVersion: version.label,
      unsavedEnvChanges: [],
      ...(added.length && { scenarios: [...(envState?.scenarios || []), ...added] }),
    },
  };
};

/* ── reading a new version's tools from its source ───────────────────────── */

/*
  Tools a later version plausibly brings. The prototype has no real source to
  read, so the "read" returns the previous version's tools plus, for most
  sources, one of these — chosen by the source location so the same link
  always reads the same way.
*/
const CANDIDATE_TOOLS = [
  { name: "lookup_refund_status", desc: "Where an in-flight refund has got to." },
  { name: "schedule_callback", desc: "Book a call back at a time the caller picks." },
  { name: "send_sms_update", desc: "Text the caller a status update." },
  { name: "check_loyalty_tier", desc: "Read the caller's loyalty tier and perks." },
  { name: "transfer_to_billing", desc: "Hand the call to the billing queue." },
  { name: "apply_discount_code", desc: "Apply a discount code to an open order." },
];

const hashKey = (s = "") => [...String(s)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 100003, 7);

/** The tools found when reading a new version's source. */
export const readToolsFromSource = (previous = [], sourceKey = "") => {
  const h = hashKey(sourceKey);
  const have = new Set(previous.map((t) => t.name));
  const pool = CANDIDATE_TOOLS.filter((t) => !have.has(t.name));
  const added = h % 5 === 0 || !pool.length ? [] : [pool[h % pool.length]];
  return [...previous, ...added];
};

/* ── what a run is made of ───────────────────────────────────────────────── */

/**
 * How well an agent version does. A property of the version — never of how
 * many runs came before it — so re-running v1 today lands where v1 landed
 * yesterday, and v2 beating v1 is something v2 did. Later versions are the
 * improved ones in this prototype.
 */
export const agentFailRate = (agentLabel) => Math.max(0.05, 0.22 - (versionNumber(agentLabel) - 1) * 0.04);

/** The environment version a stored run was taken on. */
export const runEnvVersion = (env, envState, run) => {
  if (run?.envVersion) return run.envVersion;
  /* Runs recorded before the field existed predate every version minted in the
     app — the newest one that was not minted here. */
  const list = environmentVersions(env, envState);
  const seeded = list.filter((v) => !v.minted);
  return (seeded[0] || list[list.length - 1] || {}).label || "v1";
};

/**
 * The scenarios that exist in an environment version. A scenario added by a
 * later rebuild (for a tool this world never learned) is not part of it.
 */
export const scenariosInEnvVersion = (envState, envLabel) => (envState?.scenarios || [])
  .filter((sc) => !sc.addedInEnv || !envLabel || versionNumber(sc.addedInEnv) <= versionNumber(envLabel));

/**
 * Everything a run's outcome depends on, from the pairing it pins. The live
 * run and every later replay of it read the same inputs, so the numbers you
 * watched are the numbers the run keeps.
 */
export const runInputs = (env, envState, { agent, envVersion, actors, actorEdits, toolFaults, admissionGates = 3, outcomes = 0, requiredEvals = 0, gradingRule = 0, turnLimits = 0, turnLimit = undefined }) => ({
  admissionGates,
  /* "Environments, defined" — sub-goals decide (gradingRule 2) and the turn
     limit (turnLimits 1, at the environment limit the run pinned). Only runs
     that pinned them; missing → 0, and the run replays as it always did. */
  gradingRule,
  turnLimits,
  ...(turnLimit != null && { turnLimit }),
  /* The updated RSI PRD's run rules — retry once, pull a scenario test invalid
     twice, required evaluations. Only runs that pinned them; missing → 0. */
  outcomes,
  requiredEvals,
  /* The world the run is on — a scenario's "broken" is about one world. */
  envVersion: envVersion || currentEnvVersion(env, envState)?.label,
  tools: worldToolsFor(env, envState, envVersion),
  agentTools: agentToolsFor(env, envState, agent),
  failRate: agentFailRate(agent),
  phrasing: versionNumber(agent),
  /* The cast the run had — a stored run carries its own, so a later edit to
     the actors doesn't replay history differently. */
  actors: (actors || actorsOf(env, envState))
    /* …and the actor edits it pinned, not today's. */
    .map((id) => getActor(id, actorEdits !== undefined ? { ...envState, actorEdits: actorEdits || {} } : envState))
    .filter(Boolean),
  /* Injected faults: a stored run replays the ones it pinned (none, for runs
     from before the dial); a new run takes the current dial. */
  toolFaults: toolFaults !== undefined ? toolFaults : toolFaultsOf(env, envState),
});

/**
 * The tools a scenario needs: what it records, else the known tools its text
 * names. "Known" means every tool any agent version calls plus every tool the
 * world answers — so an imported scenario that needs a new version's tool is
 * caught even though the world has never heard of it.
 */
export const requiredToolsOf = (row, knownTools = []) => {
  if (Array.isArray(row?.requiredTools) && row.requiredTools.length) return row.requiredTools;
  /* A SaaS sandbox scenario is about one app (Gmail, Slack…), and the sandbox
     answers that app's own API — it needs no named tool. Guessing from words
     here matched Gmail "labels" to the agent's generate_return_label and
     marked a brand-new sandbox's scenarios Outdated. */
  if (sandboxScenario(row)) return [];
  return inferTools(row, knownTools);
};

/** Every tool this environment has heard of: the world's and every agent version's. */
export const knownToolsFor = (env, envState) => {
  const seen = new Map();
  worldToolsFor(env, envState).forEach((t) => seen.set(t.name, t));
  agentVersions(envState).forEach((v) => agentToolsFor(env, envState, v.label).forEach((t) => {
    if (!seen.has(t.name)) seen.set(t.name, t);
  }));
  return [...seen.values()];
};
