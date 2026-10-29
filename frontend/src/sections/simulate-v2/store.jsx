/**
 * Prototype-local state for the simulation flow.
 *
 * Deliberately not react-query / not the API layer: this whole section is a
 * frontend prototype for stakeholder review, so the "backend" is this reducer
 * plus browser storage (IndexedDB — see _mock/storeStorage.js). Swapping it for real endpoints later means replacing this
 * one file's actions, not the screens.
 */
import PropTypes from "prop-types";
import {
  createContext,
  useContext,
  useMemo,
  useReducer,
  useEffect,
  useCallback,
} from "react";
import { seededState } from "./_mock/seedState";
import { SEEDED_PROJECTS } from "./_mock/projectLinks";
import { runReadiness } from "./_mock/setupGaps";
import { getEnvironment } from "./_mock/environments";
import { generatedPool } from "./_mock/scenarios";
import { agentPinPatch, agentVersions, defaultAgentVersionOf, isPendingDraft, majorOf, productionAgentVersion, versionNumber } from "./_mock/versions";
import { raiseNudgesIn } from "./_mock/nudgeEffects";
/* Which agent this is, across environments: where it lives. Two
   environments connected to the same repo, endpoint or hosted assistant are
   testing one agent. */
import { envAgentKey, connectedAgentsFrom, repairAgentRecord } from "./_mock/agentRegistry";
/* Secrets are held as secret-manager references, never values (AC-6.12). */
import { scrubAgentSecrets } from "./_mock/secretRefs";
import { recordCredentialSets } from "./_mock/envConfig";
/* Topping a stored copy up with what the seed gained since it was saved. */
import { SEED_VERSION, topUpSeed, scrubEnvState, scrubPatch, withRsiSeed } from "./_mock/seedTopUp";
import { withCommitHistory, withCommitTies, withFixPr } from "./_mock/commitHistorySeed";
import { withProjectLinkRepair } from "./_mock/projectLinkRepair";
import { readStored, writeStored, updateStored } from "./_mock/storeStorage";
/* A search that played out while nobody watched has finished. */
import { settleImprovements } from "./_mock/optimizationRuns";
import { settleAbandonedRuns } from "./_mock/runGroup";
/* Scheduled improvements start on their own, once per store. */
import { useScheduledImprovements } from "./_mock/scheduledImprovements";
import useFixFinishedNotices from "./errorFeed/useFixFinishedNotices";
import { uniqueEnvName } from "./_mock/envNames";
import { usePreviewRole } from "./_mock/previewRole";

/*
  Versioned: the seeded first-run environments only reach anyone whose browser
  already holds an older (empty) cache if the key changes with them. Bump this
  whenever the seeded state or the state shape changes.
*/
const STORAGE_KEY = "fagi.simulate.studio.v18";

const emptyEnvState = () => ({
  agent: null, // source agent { typeId, values, connectedAt } — this env's contract was read from
  /*
    Additional agents the user has attached for testing. Each has the
    same shape as `agent` plus an `id` and an optional `note`. The
    source agent stays in `agent`; adding another does NOT replace the
    source. Users can promote an additional agent to "source" (which
    re-derives the env) via a confirmation dialog.
  */
  additionalAgents: [], // Array<{ id, typeId, values, via, connectedAt, note }>
  /*
    Which agent the next simulation run will target. `null` (the
    default) means the source agent; otherwise the id of one of
    `additionalAgents`. Runs stamp whichever agent was active on
    themselves so run history stays attributable.
  */
  activeAgentId: null,
  scenarios: [], // scenario rows chosen or generated
  scenarioSource: null, // 'templates' | 'chat' | 'agent'
  evals: [], // eval ids
  runs: [], // completed run summaries
  /* Versions of the agent under test, oldest first. Minted when the agent
     changes, never by running — see _mock/versions.js. */
  agentVersions: [],
  /* Versions of the world. Empty until someone changes it; the seeded history
     stands in until then. */
  envVersions: [],
  /*
    What was actually shipped.

    The gate decides whether a version *may* ship and then the decision
    evaporates — so "how does this compare to what is live" cannot be asked,
    and the baseline gets re-picked from memory every time. A release is one
    line: which version, off which run, when, and whether the gate was clear or
    overridden.
  */
  releases: [],
  /*
    Twin backing — when the environment's world is a live sandbox for
    third-party services (Slack, Notion, Salesforce, etc.) instead of
    the default seeded generic tables. `null` when this env has no
    twin backing; otherwise the sandbox we provisioned for it.
    Shape: { services, seedPrompt, seed, endpoints, activity, provisionedAt }.
    Scenarios can override the `seed` per-scenario without touching
    the env-level default (follow-up work).
  */
  twinBacking: null,
  /* The scenario set's own version log ([{ label, at, summary, largestBump }],
     _mock/suiteLog.js). */
  suiteVersions: [],
});

const initialState = {
  myEnvironments: [], // environments the user created / adopted
  byEnv: {}, // envId -> emptyEnvState()
  /* Agents connected from the Agents page — not yet (or not only) tied to an
     environment. Environment agents join them in connectedAgentsFrom(). */
  connectedAgents: [],
  /* Error Feed project → the agent whose traffic lands in it, for projects
     the feed couldn't match to an agent on its own. Linking one issue links
     every issue from that project, today's and future ones. */
  projectLinks: {},
  /* Agent key → what improvements may change and must not move. */
  agentLimits: {},
  /* Workspace feature flags. Unset reads as the default (see _mock/featureFlags). */
  flags: {},
  activeRun: null,
  /* Workspace-level settings a workspace admin owns (RSI PRD). Today one:
     whether scenarios may be built from production — the Error Feed and
     production conversations replayed word for word. */
  workspaceSettings: { productionScenarios: true },
};

/* The signed-in user's workspace role — mock: an admin, who can preview the
   product as a member or viewer from the workspace menu (previewRole.js). */

/*
  A first visit lands on an empty "My environments", which hides the workspace,
  runs and eval mapping behind setup nobody has done yet. Seed a few that look
  like work already in progress — and only when nothing is stored, so it never
  overwrites what someone actually did.
*/
const firstRunState = () => ({ ...initialState, ...withFixPr(withProjectLinkRepair(withCommitTies(withCommitHistory(withRsiSeed(seededState()))))), seedVersion: SEED_VERSION });

/* ── agent versions: one way in ──────────────────────────────────────────────
   A version belongs to the agent. Every environment testing that agent, and
   its Agents-page entry, hold the same list — so a version is minted in one
   place, numbered past every label known anywhere, and never dropped when an
   environment goes away or moves to another agent. */

const unionVersions = (a = [], b = []) => {
  const byLabel = new Map(a.map((v) => [v.label, v]));
  b.forEach((v) => { if (!byLabel.has(v.label)) byLabel.set(v.label, v); });
  return [...byLabel.values()].sort((x, y) => versionNumber(x.label) - versionNumber(y.label));
};

const knownVersions = (state, key) => [
  ...Object.entries(state.byEnv || {}).filter(([id, es]) => envAgentKey(es?.agent, id) === key).flatMap(([, es]) => agentVersions(es)),
  ...(state.connectedAgents || []).filter((a) => a.key === key).flatMap((a) => a.versions || []),
];

const mintVersion = (state, key, version, activateEnvId) => {
  const known = knownVersions(state, key);
  /* A label already taken — another environment or the Agents page got there
     first — takes the next free number instead of being silently dropped. */
  const v = known.some((k) => k.label === version.label)
    ? (() => { const n = Math.max(0, ...known.map((k) => majorOf(k.label))) + 1; return { ...version, id: `agent-v${n}`, label: `v${n}` }; })()
    : version;
  const byEnv = { ...state.byEnv };
  Object.entries(state.byEnv).forEach(([id, es]) => {
    if (envAgentKey(es?.agent, id) !== key) return;
    const next = { ...es, agentVersions: unionVersions(agentVersions(es), [v]) };
    /* Environments that follow production move with it on their own; a
       pinned one stays put. The environment it was minted in (when asked)
       tests it straight away — unless it's a draft: a draft is never what
       an environment tests; its runs name it (?agent=) until it's released. */
    byEnv[id] = id === activateEnvId && v.status !== "draft" ? { ...next, ...agentPinPatch(next, v.label) } : next;
  });
  const connectedAgents = (state.connectedAgents || []).map((a) => (a.key === key ? { ...a, versions: unionVersions(a.versions, [v]) } : a));
  return { ...state, byEnv, connectedAgents };
};

/* Keep an agent — and every version it had — on the Agents page. Fields one
   copy is missing (an assistant ID, a definition) are filled from the other. */
const keepInRegistry = (connectedAgents, key, agent, versions, extra = {}) => {
  const i = connectedAgents.findIndex((a) => a.key === key);
  if (i < 0) return [{ ...agent, key, versions, ...extra }, ...connectedAgents];
  const existing = connectedAgents[i];
  const merged = {
    ...existing,
    values: { ...(agent?.values || {}), ...(existing.values || {}) },
    definition: existing.definition || agent?.definition,
    versions: unionVersions(existing.versions, versions),
  };
  return connectedAgents.map((a, j) => (j === i ? merged : a));
};

function reducer(state, action) {
  switch (action.type) {
    case "hydrate": {
      /*
        Backfill the synthetic "build & fit-check" run #1 for any env
        already in localStorage that predates the seed-on-adopt change.
        Without this, existing envs still show 0 runs while newly-built
        ones show 1 — the two surfaces would keep disagreeing until the
        user resets state. Only backfills when the env's runs list is
        empty; envs with any real runs are left untouched.
      */
      const payload = action.payload || {};
      /*
        Refresh adopted template-based envs from their template so
        prototype-time edits to tools/rules land on already-adopted
        copies (otherwise adding a tool to env-voice-support in
        environments.js is invisible on a locally-adopted copy until
        localStorage is cleared). Custom-built envs have no matching
        template — getEnvironment returns undefined — so we skip them
        and their world stays exactly as the user built it. User-
        edited fields on the adopted env (name, description, adopted
        state) are preserved by merging template shape *under* the
        stored env.
      */
      const rawEnvs = payload.myEnvironments || [];
      const envs = rawEnvs.map((env) => {
        const tpl = getEnvironment(env.id);
        if (!tpl) return env;
        return {
          ...tpl,
          ...env,
          tools: tpl.tools || env.tools,
          rules: tpl.rules || env.rules,
        };
      });
      const nextByEnv = { ...(payload.byEnv || {}) };
      /*
        Template-based envs get their scenario pool filled in on load when a
        cached state has none. Existing lists — and the runs that ran them —
        are never rewritten.
      */
      envs.forEach((env) => {
        const es = nextByEnv[env.id];
        if (!es) return;
        /*
           Refresh scenarios from the derivation pool for template-sourced
           envs — both the seeded demo templates (getEnvironment matches an
           exact template id) AND instances adopted from a template
           (scenarioSource "templates", e.g. "env-voice-support-4zx5uf").
           Without the second case, an adopted env kept whatever reduced set
           it was seeded with at adopt time and never picked up the full
           suite (core + rules + traps + edge + adversarial). Custom-built
           envs (built from agent code) carry their own scenarios and are
           left untouched.
        */
        const fromTemplate = !!getEnvironment(env.id) || es.scenarioSource === "templates";
        if (!fromTemplate) return;
        /*
           Fill a template env's scenarios only when it has none — a cache from
           before the scenario pool existed. Never refresh a list that exists:
           that brought deleted rows back, dropped proof stamps and edits, and
           (worse) rewrote every past run's scenario set, so a four-scenario
           re-run read as a full sweep after a reload. A run keeps what it ran.
        */
        if ((es.scenarios || []).length) return;
        try {
          const pool = generatedPool(env);
          if (!Array.isArray(pool) || !pool.length) return;
          nextByEnv[env.id] = { ...es, scenarios: pool };
        } catch (err) {
          /* Prototype cache mismatch — leave this env alone. */
          // eslint-disable-next-line no-console
          console.warn("[store] scenario refresh skipped for", env.id, err);
        }
      });
      /* A built env has no runs until the user clicks "Run simulation" — we no
         longer backfill a synthetic "build & fit-check" run here. Any such run
         left in an older cache is stripped on hydrate so counts read 0. */
      envs.forEach((env) => {
        const es = nextByEnv[env.id];
        if (!es || !(es.runs || []).some((r) => r.synthetic)) return;
        nextByEnv[env.id] = { ...es, runs: es.runs.filter((r) => !r.synthetic) };
      });
      /* `hydrated` marks that the store has loaded from the cache (or the
         seed). Screens that would otherwise render "Environment not found"
         while myEnvironments is still the empty initialState wait on this,
         so a custom-built env deep-link doesn't 404 for a frame on load. */
      /* Agents that lost their assistant ID to an older version-save get it
         back, wherever a copy is stored — keyed the same way in each copy so
         the registry and the environment still agree on who it is. */
      Object.keys(nextByEnv).forEach((id) => {
        /* An improvement whose search finished while nobody watched is done —
           and so is a run whose page was closed before it finished. */
        const es = settleAbandonedRuns({ ...state, ...payload, myEnvironments: envs }, id, settleImprovements(scrubEnvState(nextByEnv[id])));
        nextByEnv[id] = es?.agent ? { ...es, agent: repairAgentRecord(es.agent, `env:${id}`) } : es;
      });
      const connectedAgents = (payload.connectedAgents || state.connectedAgents || []).map((a) => repairAgentRecord(scrubAgentSecrets(a)));
      return { ...state, ...payload, myEnvironments: envs, byEnv: nextByEnv, connectedAgents, hydrated: true };
    }

    case "adoptEnvironment": {
      const { env } = action;
      if (state.myEnvironments.some((e) => e.id === env.id)) return state;
      /*
        A freshly-adopted env starts with NO runs. Building the world proves
        the scenarios, but that isn't a simulation run — the first run is only
        created when the user clicks "Run simulation". (We used to seed a
        synthetic "build & fit-check" run here, which made a just-built env
        show 1 run before the user had run anything.)
      */
      const existing = state.byEnv[env.id];
      const seededEnvState = existing || { ...emptyEnvState() };
      /* A name already taken gets the next number, so a second build of the
         same agent isn't indistinguishable from the first. */
      const name = uniqueEnvName(env.name, state.myEnvironments, env.id);
      return {
        ...state,
        myEnvironments: [
          { ...env, ...(name && { name }), adoptedAt: action.now, custom: !!env.custom },
          ...state.myEnvironments,
        ],
        byEnv: { ...state.byEnv, [env.id]: seededEnvState },
      };
    }

    /*
      Update fields on an already-adopted environment — the buildStatus flag
      that tracks how far derivation has got, a rename, a difficulty change.
      A no-op if the id is not in myEnvironments; adoption is a separate
      action on purpose.
    */
    case "patchEnvironment": {
      const { envId, patch } = action;
      const idx = state.myEnvironments.findIndex((e) => e.id === envId);
      if (idx === -1) return state;
      const next = [...state.myEnvironments];
      next[idx] = { ...next[idx], ...patch };
      return { ...state, myEnvironments: next };
    }

    case "removeEnvironment": {
      const byEnv = { ...state.byEnv };
      const es = byEnv[action.envId];
      delete byEnv[action.envId];
      /*
        Versions belong to the agent, not to the environment that tested it.
        Deleting an environment must not delete an agent's history: if no
        other environment (and no Agents-page entry) holds this agent, keep
        it on the Agents page with every version it had.
      */
      let connectedAgents = state.connectedAgents || [];
      const key = envAgentKey(es?.agent, action.envId);
      if (key) {
        const env = state.myEnvironments.find((e) => e.id === action.envId);
        connectedAgents = keepInRegistry(connectedAgents, key, es.agent, agentVersions(es), {
          name: es.agent.name || `${env?.name || "Environment"} agent`, origin: "environment", firstEnvName: env?.name,
        });
        /* The agent remembers it was tested there — "Not tested yet" would be false. */
        const gone = { envId: action.envId, envName: env?.name || "An environment", removedAt: new Date().toISOString(), runs: (es?.runs || []).length };
        connectedAgents = connectedAgents.map((a) => (a.key === key ? { ...a, deletedIn: [...(a.deletedIn || []), gone] } : a));
      }
      return {
        ...state,
        myEnvironments: state.myEnvironments.filter((e) => e.id !== action.envId),
        byEnv,
        connectedAgents,
      };
    }

    /* Test `label` here — pinned to it, or following production when it is production. */
    case "pinAgentVersion": {
      const prev = state.byEnv[action.envId];
      if (!prev) return state;
      /* A draft is never what an environment tests — refused, nothing moves. */
      if (isPendingDraft(prev, action.label)) return state;
      const next = { ...prev, ...agentPinPatch(prev, action.label) };
      /* The environment's default version, as its history records it: who
         moved it, from what to what — going back to an older version is a
         change of default, never a silent one. */
      const before = defaultAgentVersionOf(prev);
      const after = defaultAgentVersionOf(next);
      const changes = before === after ? prev.agentDefaultChanges : [
        ...(prev.agentDefaultChanges || []),
        { from: before, to: after, production: productionAgentVersion(prev)?.label || null, at: new Date().toISOString(), by: action.by || "You" },
      ];
      return { ...state, byEnv: { ...state.byEnv, [action.envId]: { ...next, ...(changes && { agentDefaultChanges: changes }) } } };
    }

    case "patchEnvState": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      const byEnv = { ...state.byEnv, [action.envId]: { ...prev, ...scrubPatch(action.patch) } };
      /* A version released here is released for the agent: every environment
         testing it learns so, and those following production move to it. */
      const before = new Set((prev.releases || []).map((r) => r.version));
      const fresh = (action.patch?.releases || []).map((r) => r.version).filter((l) => l && !before.has(l));
      const key = fresh.length ? envAgentKey(prev.agent, action.envId) : null;
      if (!key) return { ...state, byEnv };
      /* Released is production: whatever an admin made active before gives
         way to it, and a pin held on it (someone trying the draft) is now
         just following production — so every environment that follows
         production, the one that shipped it included, tests it next. */
      const now = new Date().toISOString();
      const shipped = [...fresh].sort((a, b) => versionNumber(a) - versionNumber(b)).pop();
      const mark = (list) => (list || []).map((v) => (fresh.includes(v.label) && v.status === "draft" ? { ...v, status: "released", releasedAt: v.releasedAt || now } : v));
      const madeActive = (values) => (values?.activeVersion?.label && values.activeVersion.label !== shipped
        ? { ...values, activeVersion: { label: shipped, at: now, by: "release" } }
        : values);
      const others = [];
      Object.entries(byEnv).forEach(([id, es]) => {
        if (envAgentKey(es?.agent, id) !== key) return;
        let next = { ...es, agentVersions: mark(agentVersions(es)), ...(es.agent?.values && { agent: { ...es.agent, values: madeActive(es.agent.values) } }) };
        if (next.agentVersionPinned && fresh.includes(next.activeAgentVersion)) next = { ...next, ...agentPinPatch(next, next.activeAgentVersion) };
        byEnv[id] = next;
        if (id !== action.envId) others.push(id);
      });
      const connectedAgents = (state.connectedAgents || []).map((a) => (a.key === key ? { ...a, versions: mark(a.versions), values: madeActive(a.values) } : a));
      /* A released fix is an agent change everywhere else the agent is
         tested: those environments get its one card — a changed rule still
         gets its question. The environment that verified it doesn't. */
      const released = { ...state, byEnv, connectedAgents };
      const version = agentVersions(byEnv[action.envId]).find((v) => v.label === shipped);
      const envs = others.map((id) => state.myEnvironments.find((e) => e.id === id)).filter(Boolean);
      if (version && envs.length) {
        raiseNudgesIn(released, envs, version).forEach((r) => {
          byEnv[r.envId] = { ...byEnv[r.envId], ...r.patch };
        });
      }
      return { ...state, byEnv, connectedAgents };
    }

    /*
      Upsert-by-id. A run gets recorded twice: once when it starts (so
      it shows up in Simulated Runs / the workspace's Run history the
      moment the user hits Start simulation, even if they navigate away
      before it finishes) and once again when it completes (merged into
      the same row with the final stats). Prepending twice would leave
      a duplicate — so if an entry with this run id already exists,
      merge into it in place and keep its original position.
    */
    case "recordRun": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      const runs = prev.runs || [];
      const idx = runs.findIndex((r) => r.id === action.run.id);
      /* A run records which agent it tested, not just the version label —
         after the environment changes agent, "v3" means a different thing. */
      const keyed = action.run.agentKey ? action.run : { ...action.run, agentKey: envAgentKey(prev.agent, action.envId) };
      /* And which credential set it used — once, when it's first recorded;
         the set is written down so its label never moves. */
      const needsSet = !keyed.credentialSet && !(idx >= 0 && runs[idx].credentialSet);
      const sets = needsSet ? recordCredentialSets(prev) : null;
      const stamped = needsSet ? { ...keyed, credentialSet: sets.credentialSets.slice(-1)[0]?.label } : keyed;
      const nextRuns = idx >= 0
        ? runs.map((r, i) => (i === idx ? { ...r, ...stamped } : r))
        : [stamped, ...runs];
      return {
        ...state,
        byEnv: {
          ...state.byEnv,
          [action.envId]: { ...prev, runs: nextRuns, ...sets },
        },
      };
    }

    /* A new agent version. Runs pin whichever one is current when they start,
       so minting is the only thing that moves the pairing forward. */
    case "addAgentVersion": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      /* Start from the list every reader sees —
         or the first new version would replace v1 instead of following it. */
      const list = agentVersions(prev);
      const byEnv = {
        ...state.byEnv,
        /* A new live version is production, so this environment tests it
           next; a draft changes nothing until it's released. */
        [action.envId]: (() => {
          const next = { ...prev, agentVersions: [...list, action.version] };
          return action.version.status === "draft" ? next : { ...next, ...agentPinPatch(next, action.version.label) };
        })(),
      };
      /*
        An agent version belongs to the agent, not to one environment. Every
        other environment testing the same agent learns that the version
        exists, so "v2" means the same thing everywhere — and those
        following production test it next; pinned ones stay put.
      */
      const key = envAgentKey(prev.agent, action.envId);
      /* With an agent attached, it's the same mint as everywhere else. */
      if (key) return mintVersion(state, key, action.version, action.envId);
      return { ...state, byEnv };
    }

    /*
      The environment starts testing a different agent. Not a version — a
      different agent, with its own history. The world, scenarios and
      evaluations stay; past runs keep the agent they actually tested; the
      previous agent stays on the Agents page with every version it had.
    */
    case "changeEnvAgent": {
      const { envId, versions, activeLabel } = action;
      const agent = scrubAgentSecrets(action.agent);
      const prev = state.byEnv[envId] || emptyEnvState();
      const env = state.myEnvironments.find((e) => e.id === envId);
      const oldKey = envAgentKey(prev.agent, envId);
      const oldName = prev.agent?.name || `${env?.name || "Environment"} agent`;
      let connectedAgents = state.connectedAgents || [];
      if (oldKey && prev.agent) {
        connectedAgents = keepInRegistry(connectedAgents, oldKey, prev.agent, agentVersions(prev), {
          name: oldName, origin: "environment", firstEnvName: env?.name,
        });
      }
      const runs = (prev.runs || []).map((r) => (r.agentKey ? r : { ...r, agentKey: oldKey }))
        .map((r) => (r.agentKey === oldKey ? { ...r, agentName: r.agentName || oldName } : r));
      return {
        ...state,
        connectedAgents,
        byEnv: {
          ...state.byEnv,
          [envId]: {
            ...prev,
            agent,
            agentVersions: versions,
            ...agentPinPatch({ agent, agentVersions: versions }, activeLabel || null),
            runs,
            previousAgents: [
              ...(prev.previousAgents || []),
              ...(prev.agent ? [{ key: oldKey, name: oldName, replacedAt: new Date().toISOString() }] : []),
            ],
          },
        },
      };
    }

    /* An agent connected from the Agents page. Re-connecting the same source
       is a no-op — it is already in the list. */
    case "connectAgent": {
      const list = state.connectedAgents || [];
      const incoming = scrubAgentSecrets(action.agent);
      const i = list.findIndex((a) => a.key === incoming.key);
      if (i < 0) return { ...state, connectedAgents: [incoming, ...list] };
      /* Connecting the same agent again fills in what it was missing — an
         endpoint, a name, a linked repository, contact settings. Never a
         version: it's the same agent. Filled-in values win over blanks only. */
      /* A deleted agent connected again comes back, with the history it kept. */
      const { deleted: _deleted, ...prev } = list[i];
      const next = incoming;
      const filled = Object.fromEntries(Object.entries(next.values || {}).filter(([, val]) => val !== undefined && val !== ""));
      const merged = {
        ...prev,
        name: prev.name || next.name,
        values: { ...(prev.values || {}), ...filled },
        definition: prev.definition?.repoUrl || !next.definition?.repoUrl ? prev.definition : { ...(prev.definition || {}), ...next.definition },
      };
      return { ...state, connectedAgents: list.map((a, j) => (j === i ? merged : a)) };
    }

    case "setAgentLimits": {
      return { ...state, agentLimits: { ...(state.agentLimits || {}), [action.agentKey]: action.limits } };
    }

    /* Say which agent an Error Feed project's traffic comes from, so Fix
       can find it for every issue in that project. */
    /* An Observe project's agent. agentKey null unlinks it. */
    case "linkProjectAgent": {
      /* Unlinking leaves "no agent" in place, so the project doesn't fall
         back to the agent its seed or name once suggested. */
      if (!action.agentKey) return { ...state, projectLinks: { ...(state.projectLinks || {}), [action.project]: "__not-an-agent__" } };
      return { ...state, projectLinks: { ...(state.projectLinks || {}), [action.project]: action.agentKey } };
    }

    /*
      Delete an agent. Environments are the work — scenarios, evaluations,
      runs — so they're never deleted with it: each one testing the agent
      is unlinked and keeps everything else, showing "Connect agent" until
      one is connected. Observe projects linked to it are unlinked too, so
      their issues read "Connect agent to fix". The agent itself stays as a
      tombstone (`deleted: { at }`) with its versions — off the list and
      the pickers, but the runs and improvements that name it still open
      its history (RSI PRD: "its history is kept in each case").
    */
    case "removeAgent": {
      const { key } = action;
      /* RSI PRD: "Deleted: possible only once the agent has no environments
         left." With one still testing it, nothing changes. */
      /* Only environments that exist count — a build left part-way can leave
         its state behind with no environment, and it mustn't block a delete. */
      const live = new Set((state.myEnvironments || []).map((e) => e.id));
      if (Object.entries(state.byEnv || {}).some(([envId, es]) => live.has(envId) && es?.agent && envAgentKey(es.agent, envId) === key)) return state;
      const at = new Date().toISOString();
      const byEnv = Object.fromEntries(Object.entries(state.byEnv || {}).map(([envId, es]) => (
        es?.agent && envAgentKey(es.agent, envId) === key
          ? [envId, { ...es, agent: null, removedAgent: { key, name: es.agent.name || null, at } }]
          : [envId, es]
      )));
      /* Its projects — linked by hand or seeded — read "no agent" now, so
         their issues say "Connect agent to fix" instead of naming a deleted agent. */
      const projectLinks = Object.fromEntries(Object.entries(state.projectLinks || {}).map(([p, k]) => [p, k === key ? "__not-an-agent__" : k]));
      Object.entries(SEEDED_PROJECTS).forEach(([p, k]) => { if (k === key && !projectLinks[p]) projectLinks[p] = "__not-an-agent__"; });
      return {
        ...state,
        connectedAgents: (state.connectedAgents || []).map((a) => (a.key === key ? { ...a, deleted: { at } } : a)),
        byEnv,
        projectLinks,
      };
    }

    /*
      Where an agent runs (its endpoint) changed. Not a new version — versions
      track what the agent does, not where it lives — so it updates the agent
      everywhere it's recorded: the registry and every environment testing it.
    */
    /* Fill in connection details an agent is missing (an assistant ID). Like
       the runtime, not a version — same agent, same history. */
    case "setAgentValues": {
      const { key } = action;
      const patch = scrubAgentSecrets({ values: action.patch }).values;
      const withValues = (a) => ({ ...a, key, values: { ...(a.values || {}), ...patch } });
      const connectedAgents = (state.connectedAgents || []).map((a) => (a.key === key ? withValues(a) : a));
      const byEnv = Object.fromEntries(Object.entries(state.byEnv || {}).map(([envId, es]) => (
        es?.agent && envAgentKey(es.agent, envId) === key ? [envId, { ...es, agent: withValues(es.agent) }] : [envId, es]
      )));
      return { ...state, connectedAgents, byEnv };
    }

    case "setAgentRuntime": {
      const { key, endpoint } = action;
      /* Freeze the key first — for an agent identified by how it's reached,
         a new endpoint would otherwise read as a different agent. */
      const withEndpoint = (a) => ({ ...a, key, values: { ...(a.values || {}), endpoint } });
      const connectedAgents = (state.connectedAgents || []).map((a) => (a.key === key ? withEndpoint(a) : a));
      const byEnv = Object.fromEntries(Object.entries(state.byEnv || {}).map(([envId, es]) => (
        es?.agent && envAgentKey(es.agent, envId) === key ? [envId, { ...es, agent: withEndpoint(es.agent) }] : [envId, es]
      )));
      return { ...state, connectedAgents, byEnv };
    }

    /*
      A version recorded on the agent itself (a Sync, or a version added from
      the Agents page). Every environment testing the agent learns it exists;
      only `activateEnvId`, if given, switches to it — the rest keep running
      what they ran, so their results stay attributable.
    */
    case "recordAgentVersion": {
      const { key, version, activateEnvId } = action;
      const next = mintVersion(state, key, version, activateEnvId);
      if (!version?.outsideChange) return next;
      /* The waiting outside edit is now a version — it isn't found again. */
      const byEnv = { ...next.byEnv };
      Object.keys(byEnv).forEach((id) => {
        const a = byEnv[id]?.agent;
        if (a?.values?.pendingOutsideChange && (a.key === key || envAgentKey(a, id) === key)) {
          const { pendingOutsideChange, ...values } = a.values;
          byEnv[id] = { ...byEnv[id], agent: { ...a, values } };
        }
      });
      return { ...next, byEnv };
    }

    /*
      An existing version changes state — a Draft recorded when a proposal was
      made (a draft PR, a platform draft, a patch exported) becomes the version
      that landed. Updated in place by label everywhere the agent is recorded,
      never re-minted, so landing a draft can't create a second version.
      `activateEnvId`, if given, makes that environment test it (pinned, or
      following production when it is production).
    */
    case "updateAgentVersion": {
      const { key, label, patch: vPatch, activateEnvId } = action;
      if (!key || !label) return state;
      const apply = (list) => (list || []).map((v) => (v.label === label ? { ...v, ...vPatch } : v));
      const byEnv = { ...state.byEnv };
      Object.entries(state.byEnv).forEach(([id, es]) => {
        if (envAgentKey(es?.agent, id) !== key) return;
        const next = { ...es, agentVersions: apply(agentVersions(es)) };
        byEnv[id] = id === activateEnvId ? { ...next, ...agentPinPatch(next, label) } : next;
      });
      const connectedAgents = (state.connectedAgents || []).map((a) => (a.key === key ? { ...a, versions: apply(a.versions) } : a));
      return { ...state, byEnv, connectedAgents };
    }

    /* Workspace feature flags — internal, never shown: `agentImprovement` (RSI rollout). */
    case "setFlag": {
      return { ...state, flags: { ...(state.flags || {}), [action.flag]: action.on } };
    }

    /* A workspace admin's setting — applies to every environment. */
    case "setWorkspaceSetting": {
      return { ...state, workspaceSettings: { ...(state.workspaceSettings || {}), [action.key]: action.value } };
    }

    case "reset":
      return initialState;

    default:
      return state;
  }
}

const SimStoreContext = createContext(null);

/*
  One store per page, however many providers wrap it. The Simulate area
  shares a layout-level provider, and some pages (Improvements) wrap their own
  too. Two live stores meant a run started from the inner one never reached
  the outer one the run page reads — and each persisted its own copy over the
  other's. A provider inside another now just uses the outer one.
*/
export function SimStoreProvider({ children }) {
  const outer = useContext(SimStoreContext);
  if (outer) return children;
  return <SimStoreRoot>{children}</SimStoreRoot>;
}
SimStoreProvider.propTypes = { children: PropTypes.node };

function SimStoreRoot({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  // Hydrate once on mount. The `hydrated: true` marker the reducer stamps is
  // what gates persistence below — set on committed state, so it can't race.
  useEffect(() => {
    readStored(STORAGE_KEY).then((raw) => {
      try {
        dispatch({
          type: "hydrate",
          payload: raw ? topUpSeed(JSON.parse(raw)) : firstRunState(),
        });
      } catch (err) {
        /* a corrupt prototype cache should never block the screen — fall back
           to the seed so the app still renders rather than staying blank. But
           never save that seed over what's stored: a top-up that throws for a
           moment (a half-saved edit under HMR) once replaced a whole workspace
           with a fresh seed. The stored copy stays as it was until it loads. */
        // eslint-disable-next-line no-console
        console.error("[store] stored state didn't load — showing the seed, not saving over it", err);
        dispatch({ type: "hydrate", payload: raw ? { ...firstRunState(), storedUnreadable: true } : firstRunState() });
      }
    });
  }, []);

  /*
    Persist on every change — but only once the store is genuinely hydrated.

    The bug this guards against: two effects fire on mount, and the previous
    guard (a `hydrated` ref flipped inside the hydrate effect) turned true
    *before* the hydrated state committed. So on that first pass this effect
    ran with `state` still the empty `initialState` and wrote it over a cache
    holding real environments. Vite HMR remounts this provider on every edit,
    so the race fired constantly — which is why environments "randomly"
    vanished mid-edit. A hydrate reducer that throws leaves state at
    initialState and hits the same path.

    `state.hydrated` is set to true only by the hydrate reducer, on committed
    state — never on the pre-hydration initialState and never on a crashed
    hydrate. Gating on it means we never persist an un-hydrated empty state,
    while a legitimately-empty state after hydration (the user deleted their
    last environment) still has the flag and persists correctly.
  */
  useEffect(() => {
    if (!state.hydrated || state.storedUnreadable) return;
    /* storeStorage falls back quietly — the prototype still works in-memory */
    writeStored(STORAGE_KEY, JSON.stringify(state));
  }, [state]);

  /*
    Dispatch and save in the same breath. Each page area mounts its own
    provider and they share state only through storage — a change made right
    before navigating to another area (Start fix on the Error Feed, New
    improvement) would otherwise be lost when this provider unmounts before
    its persist effect runs. Applies the same reducer to what's stored.
  */
  const commit = useCallback((action) => {
    dispatch(action);
    /* in-memory only if this fails — the dispatch above still applies here */
    updateStored(STORAGE_KEY, (saved) => reducer(saved, action));
  }, []);

  useScheduledImprovements(state, commit);
  /* A fix finishing in the background is said once, wherever you are. */
  useFixFinishedNotices(state, commit);

  const value = useMemo(() => ({ state, dispatch, commit }), [state, commit]);
  return (
    <SimStoreContext.Provider value={value}>{children}</SimStoreContext.Provider>
  );
}

SimStoreRoot.propTypes = { children: PropTypes.node };

export function useSimStore() {
  const ctx = useContext(SimStoreContext);
  if (!ctx) throw new Error("useSimStore must be used inside <SimStoreProvider>");
  return ctx;
}

/** The store when there is one — null outside a provider, for components also used elsewhere. */
export function useOptionalSimStore() {
  return useContext(SimStoreContext);
}

/** Convenience hook for one environment's slice of flow state. */
/* One empty state for every environment the store hasn't loaded yet. A fresh
   one per render gave the run player new scenario and eval arrays each time,
   and it reset itself in a loop until IndexedDB answered. */
const EMPTY_ENV_STATE = emptyEnvState();

export function useEnvState(envId) {
  const { state, dispatch } = useSimStore();
  const envState = state.byEnv[envId] || EMPTY_ENV_STATE;

  const patch = useCallback(
    (p) => dispatch({ type: "patchEnvState", envId, patch: p }),
    [dispatch, envId],
  );

  const recordRun = useCallback(
    (run) => dispatch({ type: "recordRun", envId, run }),
    [dispatch, envId],
  );

  const addAgentVersion = useCallback(
    (version) => dispatch({ type: "addAgentVersion", envId, version }),
    [dispatch, envId],
  );

  /**
   * Setup progress drives the rail on the workspace and gates the Run button.
   * Evaluations are optional (PRD v7) — sub-goals decide pass or fail, so a
   * run doesn't need one; they score on top.
   */
  const steps = useMemo(
    () => [
      { id: "agent", label: "Connect agent", done: !!envState.agent },
      { id: "scenarios", label: "Add scenarios", done: envState.scenarios.length > 0 },
      { id: "evals", label: "Select evals", done: envState.evals.length > 0, optional: true },
      { id: "run", label: "Run simulation", done: envState.runs.length > 0 },
    ],
    [envState],
  );

  /* Env-level readiness — the same check every way to start a run uses. */
  const canRun = runReadiness(envState).ready;
  const nextStep = steps.find((s) => !s.done && !s.optional) || steps[3];

  return { envState, patch, recordRun, addAgentVersion, steps, canRun, nextStep };
}

/**
 * Workspace settings a workspace admin owns, and whether the signed-in user
 * may change them. Outside a store it reads the defaults, read-only.
 */
export function useWorkspaceSettings() {
  const store = useContext(SimStoreContext);
  const settings = store?.state?.workspaceSettings || initialState.workspaceSettings;
  const dispatch = store?.dispatch;
  const role = usePreviewRole((s) => s.role);
  const isAdmin = role === "admin";
  const set = useCallback(
    (key, value) => { if (dispatch && isAdmin) dispatch({ type: "setWorkspaceSetting", key, value }); },
    [dispatch, isAdmin],
  );
  return {
    settings,
    set,
    isAdmin,
    role,
    readOnlyReason: isAdmin ? null : "Only a workspace admin can change this.",
  };
}

/** Every connected agent in the workspace — environment-connected and Agents-page-connected, merged by identity. */
export function useConnectedAgents() {
  const { state, dispatch, commit } = useSimStore();
  const agents = useMemo(() => connectedAgentsFrom(state), [state]);
  /* Saved at once: connecting often navigates straight on (back to an Error
     Feed issue), and that page reads the store from storage. */
  const connectAgent = useCallback((agent) => commit({ type: "connectAgent", agent }), [commit]);
  const removeAgent = useCallback((key) => dispatch({ type: "removeAgent", key }), [dispatch]);
  const setAgentRuntime = useCallback((key, endpoint) => dispatch({ type: "setAgentRuntime", key, endpoint }), [dispatch]);
  const setAgentValues = useCallback((key, patch) => dispatch({ type: "setAgentValues", key, patch }), [dispatch]);
  const recordAgentVersion = useCallback(
    (key, version, activateEnvId) => dispatch({ type: "recordAgentVersion", key, version, activateEnvId }),
    [dispatch],
  );
  const changeEnvAgent = useCallback(
    (envId, { agent, versions, activeLabel }) => dispatch({ type: "changeEnvAgent", envId, agent, versions, activeLabel }),
    [dispatch],
  );
  const runVersionIn = useCallback(
    (envId, label) => dispatch({ type: "pinAgentVersion", envId, label }),
    [dispatch],
  );
  return { agents, connectAgent, removeAgent, setAgentRuntime, setAgentValues, recordAgentVersion, runVersionIn, changeEnvAgent, hydrated: !!state.hydrated, state };
}

export { emptyEnvState };
