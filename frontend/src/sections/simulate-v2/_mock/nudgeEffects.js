/*
  What a nudge does — PRD v7 §4.10.5, §4.10.6, AC-13.5–13.14, AC-16.11.

  decoupled.js detects nudges and holds the shared model; this module is the
  other half: raising them after a new agent version is saved, and what each
  answer or approval changes. Nothing here runs on its own — every patch is
  applied by a click on a nudge card.

    agent change  → a new AGENT version only; nudges raised per environment
    rule nudge    → Policy change / Not intended (the fix-or-policy
                    question). Policy publishes the sub-goal's next version and
                    outdates the scenarios whose caller depends on the old rule
                    (S bumps, never E). Not intended changes nothing.
    add           → E gets a handler (new version), S gets 3 new scenarios;
                    a priced backfill is offered, never started
    remove        → the scenarios needing the tool are stale on versions
                    without it (RSI PRD): they still run, but count toward no
                    decision until updated or archived
    core          → fork a new environment carrying the scenarios whose
                    capabilities still exist, ids kept
    behaviour     → E's handler updated (new version)
    dismiss       → E + S stay pinned; a drift badge stays

  State this module adds to envState (all optional):
    nudges[].answer / .answers [{ answer, at }] / .prefill / .draft / .rewrites
    subGoalEdits[]   { id, name, criterion, version, subGoalVersion, policy, ruleId, appliesFrom, nudgeId, at }
    subGoalShadows[] { id, version, shadow, ruleId, nudgeId, agentVersion, name, criterion, at }
    likelyFixes[]    { ruleId, rule, subGoals, agentVersion, at }
    suiteVersions[]  S versions, written through suiteLog.suiteLogPatch (one S counter)
*/
import { paths } from "src/routes/paths";
import {
  detectNudges,
  withNudges,
  resolveNudgePatch,
  staleRulePatch,
  requiredCapsOf,
  manifestOf,
  bumpScenario,
} from "./decoupled";
import {
  agentPinPatch,
  agentVersions,
  environmentVersions,
  currentEnvVersion,
  nextEnvVersion,
  versionNumber,
} from "./versions";
import { rulesOf, ruleOfScenario, subGoalCatalogOf } from "./envModel";
import {
  nextCatalogVersion,
  subGoalVersionOf as subGoalNumberOf,
} from "./subGoalCatalog";
import {
  suiteLogPatch,
  suiteVersionLogOf,
  currentSuiteLabel,
} from "./suiteLog";
import { currentEnvSnapshot, snapshotOfWorld } from "./envSnapshot";
import { estimateRun } from "./metering";
import { personaFor } from "./scenarios";
import { stampNewBatch } from "./addScenarios";
import { agentToolsFor, worldToolsFor } from "./toolFit";
import { runPlanHref } from "./runPlanLink";
import { SURFACES } from "./surfaces";
import { openGapsInbox } from "./gapsInboxBus";
import { connectedAgentsFrom } from "./agentRegistry";

const nowIso = () => new Date().toISOString();
export const plural = (n, word, many) =>
  `${n} ${n === 1 ? word : many || `${word}s`}`;

/* ── reading a version's change ──────────────────────────────────────────── */

/** The change that touched the rule — the line the new wording is read from. */
export const touchingChangeOf = (version, rule) => {
  const words = (rule?.text?.toLowerCase().match(/[a-z]{5,}/g) || []).slice(
    0,
    6,
  );
  return (
    (version?.changes || []).find((c) => {
      const text =
        `${c.title} ${c.detail || ""} ${c.addendum || ""}`.toLowerCase();
      return words.filter((w) => text.includes(w)).length >= 2;
    }) ||
    (version?.changes || [])[0] ||
    null
  );
};

/** What the rule reads as in the new version — its own words. */
export const proposedWordingOf = (version, rule) => {
  const c = touchingChangeOf(version, rule);
  const quoted = (c?.detail || "").match(/"(.+)"/)?.[1];
  const line = String(c?.addendum || "")
    .split("\n")
    .map((l) => l.replace(/^-\s*/, "").trim())
    .find(Boolean);
  return (quoted || line || c?.detail || rule?.text || "").replace(/\.$/, "");
};

/* Whether a free-text note names this rule: its id, or two of its distinctive words. */
const namesRule = (text, rule) => {
  const t = String(text || "").toLowerCase();
  if (!t || !rule) return false;
  if (new RegExp(`\\b${rule.id.toLowerCase()}\\b`).test(t)) return true;
  const words = [
    ...new Set(rule.text.toLowerCase().match(/\$?[a-z0-9]{5,}/g) || []),
  ];
  return words.filter((w) => t.includes(w)).length >= 2;
};

export const ANSWERS = {
  policy: { label: "Policy change", short: "Policy change" },
  "not-intended": { label: "Not intended", short: "Not intended" },
};

/**
 * The answer an optional change note implies — only when the note names the
 * rule. "R1 is a new policy for loyalty callers" → policy change.
 */
export const prefillOf = (version, rule) => {
  const note =
    version?.changeNote ||
    (version?.origin === "manual" ? version?.note : null) ||
    "";
  if (!namesRule(note, rule)) return null;
  const t = note.toLowerCase();
  if (
    /\b(by mistake|mistaken|accident|unintended|not intended|bug|regress|revert|shouldn'?t)\b/.test(
      t,
    )
  )
    return { answer: "not-intended", from: note };
  if (/\b(not sure|unsure|unclear|to confirm|tbd)\b|\?/.test(t)) return null;
  if (
    /\b(polic(y|ies)|on purpose|intended|intentional|deliberate|now allowed|new rule|allowed now|we now)\b/.test(
      t,
    )
  )
    return { answer: "policy", from: note };
  return null;
};

/* ── sub-goal versions ───────────────────────────────────────────────────── */

export const ruleGoalId = (ruleId) => `SG-rule-${ruleId}`;

/** A sub-goal's own version: @1, then one more per published edit. */
/* Counted the catalogue's way (subGoalCatalog.subGoalVersionOf) — shadows don't count. */
export const subGoalVersionOf = (envState, goalId) =>
  `@${subGoalNumberOf(envState, goalId)}`;
const nextSubGoalVersion = (envState, goalId) =>
  `@${subGoalNumberOf(envState, goalId) + 1}`;

/** The @2 the harness drafts for a policy change — the user reviews and edits it. */
export const draftSubGoalOf = (envState, nudge) => {
  const to = nudge.to || nudge.rule;
  return {
    id: ruleGoalId(nudge.ruleId),
    version: nextSubGoalVersion(envState, ruleGoalId(nudge.ruleId)),
    name: `Holds the line: ${to}`,
    criterion: `Passes when the agent follows the new rule — ${to} — and fails when it still applies the old one ("${nudge.rule}") to a caller the new rule covers.`,
  };
};

/** The rewrite drafted for a scenario whose caller depended on the old rule. */
export const rewriteDraftOf = (row, nudge) => ({
  id: row.id,
  name: row.title || row.name || row.id,
  from: row.task || row.situation || "",
  to: `${String(row.task || row.situation || "").replace(/\s+$/, "")} Rewritten for the new rule: ${nudge.to}.`,
});

/* ── raising nudges after a new agent version ────────────────────────────── */

/* The version a new one is compared with, in this environment's own list. */
const prevIn = (es, version) => {
  const list = agentVersions(es);
  if (
    version?.basedOnVersion &&
    list.some((v) => v.label === version.basedOnVersion)
  )
    return version.basedOnVersion;
  const others = list.filter((v) => v.label !== version?.label);
  return others[others.length - 1]?.label || null;
};

/* Fill in what a rule nudge card shows: the rule, the new wording, the
   sub-goals and the scenarios whose caller depends on it. */
const enrichRule = (n, env, es, version) => {
  const rules = rulesOf(env, es);
  const rule = rules.find((r) => r.id === n.ruleId);
  const change = touchingChangeOf(version, rule);
  const goals = subGoalCatalogOf(env, es).filter(
    (g) => g.tests.kind === "rule" && g.tests.ref === n.ruleId,
  );
  const callers = (es?.scenarios || [])
    .filter(
      (r) =>
        n.scenarioIds.includes(r.id) &&
        ruleOfScenario(r, rules)?.id === n.ruleId,
    )
    .map((r) => r.id);
  const prefill = prefillOf(version, rule);
  return {
    ...n,
    rule: rule?.text || n.ruleId,
    to: proposedWordingOf(version, rule),
    change: change
      ? { title: change.title, detail: change.detail || null }
      : null,
    subGoals: goals.map((g) => ({ id: g.id, name: g.name })),
    callerIds: callers,
    ...(prefill && { prefill }),
  };
};

/**
 * Nudges a saved agent version raises in each environment it's used in.
 * Proposals only. `envs` are environment objects; `state` the store state.
 * Returns [{ env, envId, nudges, likelyFixes, patch }] — `patch` holds only
 * `nudges` and `likelyFixes`, safe to apply after the version is recorded.
 */
export const raiseNudgesIn = (
  state,
  envs,
  version,
  { extra = () => [] } = {},
) =>
  (envs || [])
    .map((env) => {
      const es = state?.byEnv?.[env?.id];
      if (!es || !version) return null;
      const list = agentVersions(es);
      const withV = list.some((v) => v.label === version.label)
        ? es
        : { ...es, agentVersions: [...list, version] };
      const prev = prevIn(withV, version);
      const {
        nudges,
        likelyFixes,
        untested = [],
      } = detectNudges(env, withV, version.label, prev);
      /* A new rule has no old wording or sub-goal to read — it carries its own. */
      const all = [
        ...nudges.map((n) =>
          n.kind === "rule" && !n.newRule ? enrichRule(n, env, es, version) : n,
        ),
        ...extra(env, withV, prev),
      ];
      const fixes = likelyFixes.map((f) => ({
        ...f,
        agentVersion: version.label,
        at: nowIso(),
      }));
      const folded = withNudges(es, all);
      /* A tool the new version no longer has can't be added or followed — an
     open add or behaviour change for it is settled as superseded, so a
     Rebuild never gives the world a tool the agent dropped. */
      const now = new Set(
        agentToolsFor(env, withV, version.label).map((t) => t.name),
      );
      const merged =
        folded === es
          ? es
          : {
              ...folded,
              nudges: folded.nudges.map((n) =>
                (n.kind === "add" || n.kind === "behaviour") &&
                n.status === "open" &&
                n.agentVersion !== version.label &&
                !now.has(n.capability)
                  ? {
                      ...n,
                      status: "dismissed",
                      supersededBy: version.label,
                      resolvedAt: nowIso(),
                    }
                  : n,
              ),
            };
      const have = new Set((es.nudges || []).map((n) => n.id));
      /* What the harness found for this version here — kept for the history,
     including "nothing to change", so a quiet environment still says it
     checked. Tool and schema changes go on the card for one Rebuild; a
     changed rule always waits for a person's answer. */
      const check = {
        version: version.label,
        at: nowIso(),
        result: all.length ? "review" : "none",
        tools: all.filter((n) => n.kind !== "rule").length,
        rules: all.filter((n) => n.kind === "rule").length,
        untested: untested.length,
      };
      return {
        env,
        envId: env.id,
        nudges: all.filter((n) => !have.has(n.id)),
        likelyFixes: fixes,
        patch: {
          ...(merged !== es && { nudges: merged.nudges }),
          ...(merged.scenarioUpdate !== es.scenarioUpdate && {
            scenarioUpdate: merged.scenarioUpdate,
          }),
          ...(fixes.length && {
            likelyFixes: [
              ...fixes,
              ...(es.likelyFixes || []).filter(
                (f) =>
                  !fixes.some(
                    (x) =>
                      x.ruleId === f.ruleId &&
                      x.agentVersion === f.agentVersion,
                  ),
              ),
            ],
          }),
          agentChecks: [
            ...(es.agentChecks || []).filter(
              (c) => c.version !== version.label,
            ),
            check,
          ],
        },
      };
    })
    .filter(Boolean);

/** Apply what raiseNudgesIn returned, through the store's dispatch. */
export const dispatchNudges = (dispatch, results) =>
  (results || []).forEach((r) => {
    if (Object.keys(r.patch).length)
      dispatch({ type: "patchEnvState", envId: r.envId, patch: r.patch });
  });

/* "Customer Support Line · After hours" → "After hours" beside its parent. */
const shortNames = (names) =>
  names.map((n, i) => {
    const parent = names.find((m, j) => j !== i && n.startsWith(`${m} · `));
    return parent ? n.slice(parent.length + 3) : n;
  });

/**
 * The line the agent-sync flow ends with — one card per environment, never a
 * count of individual changes: "Review agent v5 in Customer Support Line and
 * After hours".
 */
export const nudgeAnnouncement = (results) => {
  const withNudge = (results || []).filter((r) => r.nudges.length);
  const count = withNudge.length;
  const names = shortNames(withNudge.map((r) => r.env.name));
  const list =
    names.length <= 1
      ? names.join("")
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  const label = withNudge[0]?.nudges[0]?.agentVersion || null;
  return {
    count,
    envs: withNudge.map((r) => ({
      id: r.envId,
      name: r.env.name,
      count: r.nudges.length,
    })),
    text: count
      ? `Review ${label ? `agent ${label}` : "the agent change"} in ${list}`
      : "Nothing in the environments needs your input",
  };
};

/**
 * Open an environment's "Needs your input" inbox on its nudges — here, or
 * after navigating to it (the inbox subscribes once the workspace mounts).
 */
export const openNudgesIn = (navigate, envId, hereEnvId = null) => {
  if (envId === hereEnvId) {
    openGapsInbox("nudges");
    return;
  }
  navigate(paths.dashboard.simulate.environmentDetail(envId));
  setTimeout(() => openGapsInbox("nudges"), 400);
};

/** "R1 looks like a fix — its sub-goal should now pass." */
export const likelyFixLine = (f) =>
  `${f.ruleId} looks like a fix — its sub-goal should now pass.`;

/* ── prices (AC-16.11: offered with a price, started only on a click) ─────── */

const GEN_CREDITS = 12;
const usd = (n) => `$${(Math.round(n * 100) / 100).toFixed(2)}`;

export const runPrice = (env, scenarios, repeats = 3) => {
  const est = estimateRun({ env, scenarios, repeats });
  return {
    usd: est.usd,
    calls: scenarios * repeats,
    line: `${plural(scenarios * repeats, "call")} · ${usd(est.usd)}`,
  };
};

export const generationPrice = (n) => ({
  credits: n * GEN_CREDITS,
  line: `${n * GEN_CREDITS} credits · ${usd(n * GEN_CREDITS * 0.01)}`,
});

/** A new run of one agent version on some scenarios — a link; nothing starts until it opens. */
export const rerunHref = (envId, { agent, only = [], trials = 3 }) => {
  /* Opens the run plan prefilled — every run confirms its estimate and limit. */
  return runPlanHref(envId, { agent, only, trials });
};

/* ── answering the fix-or-policy question ────────────────────────────────── */

/**
 * One answer to a rule nudge, or a change of mind later. Never touches E.
 *   policy        publish @N (the reviewed draft), outdate the caller-dependent
 *                 scenarios, drafted rewrites wait for approval — S bumps
 *   not-intended  nothing changes; the new version failing it is a regression
 * Moving away from a published policy restores @1 in a new S version.
 */
export const answerRulePatch = (
  env,
  envState,
  nudge,
  answer,
  { draft } = {},
) => {
  const at = nowIso();
  const goalId = ruleGoalId(nudge.ruleId);
  const prior = nudge.answer || null;
  const answers = [...(nudge.answers || []), { answer, at }];
  const base = { ...envState };
  const out = {};
  let summary = null;

  /* Leaving an earlier answer behind. */
  if (prior === "policy" && answer !== "policy") {
    const goal = subGoalCatalogOf(env, envState).find((g) => g.id === goalId);
    const original = goal
      ? `Holds the line: ${nudge.rule.replace(/\.$/, "")}`
      : undefined;
    out.subGoalEdits = [
      ...(envState.subGoalEdits || []),
      {
        id: goalId,
        name: original,
        criterion: undefined,
        version: nextCatalogVersion(envState),
        kind: "revert",
        n: subGoalNumberOf(envState, goalId) + 1,
        subGoalVersion: nextSubGoalVersion(envState, goalId),
        policy: false,
        revertOf: nudge.id,
        ruleId: nudge.ruleId,
        at,
      },
    ];
    out.scenarios = (envState.scenarios || []).map((r) =>
      r.outdatedBy?.nudgeId === nudge.id
        ? (({ outdatedBy, ...rest }) => rest)(r)
        : r,
    );
    summary = `${goalId} back to its earlier criteria — ${nudge.ruleId} answered ${(ANSWERS[answer]?.label || answer).toLowerCase()} instead of a policy change`;
  }
  /* A draft kept from an earlier answer goes with it. */
  if ((envState.subGoalShadows || []).some((s) => s.nudgeId === nudge.id)) {
    out.subGoalShadows = (envState.subGoalShadows || []).filter(
      (s) => s.nudgeId !== nudge.id,
    );
  }

  if (answer === "policy") {
    const d = draft || draftSubGoalOf(envState, nudge);
    const version = nextSubGoalVersion({ ...base, ...out }, goalId);
    const callers = new Set(nudge.callerIds || []);
    out.subGoalEdits = [
      ...(out.subGoalEdits || envState.subGoalEdits || []),
      {
        id: goalId,
        name: d.name,
        criterion: d.criterion,
        version: nextCatalogVersion({ ...base, ...out }),
        kind: "policy",
        n: Number(version.slice(1)),
        subGoalVersion: version,
        policy: true,
        ruleId: nudge.ruleId,
        appliesFrom: nudge.agentVersion,
        prevVersion: nudge.prevVersion,
        nudgeId: nudge.id,
        at,
      },
    ];
    out.subGoalShadows = (
      out.subGoalShadows ||
      envState.subGoalShadows ||
      []
    ).filter((s) => s.nudgeId !== nudge.id);
    out.scenarios = (out.scenarios || envState.scenarios || []).map((r) =>
      callers.has(r.id)
        ? {
            ...r,
            outdatedBy: {
              ruleId: nudge.ruleId,
              version: `agent ${nudge.agentVersion}`,
              subGoal: `${goalId}${version}`,
              nudgeId: nudge.id,
              policy: true,
            },
          }
        : r,
    );
    const rows = (envState.scenarios || []).filter((r) => callers.has(r.id));
    return suiteLogPatch(
      envState,
      {
        ...out,
        ...resolveNudgePatch(envState, nudge.id, "approved", {
          answer,
          answers,
          published: `${goalId}${version}`,
          draft: d,
          rewrites: {
            status: "drafted",
            ids: [...callers],
            drafts: rows.map((r) => rewriteDraftOf(r, nudge)),
          },
        }),
      },
      {
        summary: `${goalId}${version} published — ${nudge.ruleId} is a policy change from agent ${nudge.agentVersion}; ${plural(callers.size, "scenario")} outdated`,
        by: "nudge",
      },
    );
  }

  const done = {
    ...out,
    ...resolveNudgePatch(envState, nudge.id, "approved", {
      answer,
      answers,
      rewrites: null,
    }),
  };
  return summary
    ? suiteLogPatch(envState, done, { summary, by: "nudge" })
    : done;
};

/** Approve the drafted rewrites: each gets a major version and leaves Outdated. */
export const approveRewritesPatch = (envState, nudge) => {
  const drafts = new Map((nudge.rewrites?.drafts || []).map((d) => [d.id, d]));
  let n = 0;
  const scenarios = (envState.scenarios || []).map((r) => {
    if (r.outdatedBy?.nudgeId !== nudge.id || !drafts.has(r.id)) return r;
    n += 1;
    const { outdatedBy, ...rest } = r;
    const d = drafts.get(r.id);
    /* Edited, so it is re-proved before it scores — a major bump. The
       outdated row is the version it replaces: snapshotted, so runs that
       pinned it still replay its text. */
    return bumpScenario(
      {
        ...rest,
        task: d.to,
        situation: d.to,
        rewroteFor: outdatedBy,
        editedAt: nowIso(),
      },
      "major",
      `Rewritten for ${nudge.published} (policy change from agent ${nudge.agentVersion})`,
      r,
    );
  });
  return suiteLogPatch(
    envState,
    {
      scenarios,
      nudges: (envState.nudges || []).map((x) =>
        x.id === nudge.id
          ? {
              ...x,
              rewrites: { ...x.rewrites, status: "approved", at: nowIso() },
            }
          : x,
      ),
    },
    {
      summary: `${plural(n, "scenario")} rewritten for ${nudge.published}`,
      largestBump: n ? "major" : null,
      by: "nudge",
    },
  );
};

/* ── Add: a handler in E, three exploration scenarios in S ────────────────── */

export const storedEnvList = (env, envState) =>
  envState?.envVersions?.length
    ? envState.envVersions
    : [...environmentVersions(env, envState)]
        .reverse()
        .map(({ current, ...v }) => v);

const ADD_VARIANTS = [
  {
    suffix: "asks",
    summary: "Asks for it directly",
    task: (t, d) => `The caller asks for exactly what ${t.name} does: ${d}.`,
    expected: (t) =>
      `Agent calls ${t.name} with the right arguments and confirms it was done.`,
  },
  {
    suffix: "implied",
    summary: "Needs it but never names it",
    task: (t, d) =>
      `The caller's request only gets resolved if the agent uses ${t.name} (${d}), but they never ask for it by name.`,
    expected: (t) =>
      `Agent recognises the need, calls ${t.name}, and tells the caller what it did.`,
  },
  {
    suffix: "missing-details",
    summary: "Leaves out a detail the call needs",
    task: (t, d) =>
      `The caller wants this — ${d} — but leaves out the ${t.args?.[0] || "detail"} the call needs.`,
    expected: (t) =>
      `Agent asks for the missing ${t.args?.[0] || "detail"} before calling ${t.name}, and doesn't guess.`,
  },
];

/** The three scenarios an approved Add generates, tagged with the capability. */
export const addScenarioRows = (
  env,
  tool,
  nudge,
  stamp = Date.now().toString(36),
) => {
  const d = (tool.desc || `use ${tool.name}`)
    .replace(/\.$/, "")
    .replace(/^./, (c) => c.toLowerCase());
  return ADD_VARIANTS.map((v, i) => ({
    id: `${env.id}-cap-${tool.name}-${stamp}-${v.suffix}`,
    useCase: `Uses ${tool.name}`,
    name: `${tool.name}-${v.suffix}`,
    summary: v.summary,
    title: `${tool.name} — ${v.summary.toLowerCase()}`,
    task: v.task(tool, d),
    situation: v.task(tool, d),
    persona: personaFor(env, i + 3),
    expected: v.expected(tool),
    outcome: v.expected(tool),
    turns: 5 + i,
    requiredTools: [tool.name],
    subGoals: [
      "Understand what the caller is asking for",
      `Call ${tool.name} with the arguments the task actually needs`,
      "Confirm the outcome with the caller",
    ],
    addedForCapability: tool.name,
    addedByNudge: nudge.id,
  }));
};

/**
 * Approve an Add: E gets the handler (a new version, plain vN), S gets three
 * new scenarios for the tool. Returns { patch, envLabel, ids }.
 */
export const approveAddPatch = (env, envState, nudge, { count = 3 } = {}) => {
  const at = nowIso();
  const tool = agentToolsFor(env, envState, nudge.agentVersion).find(
    (t) => t.name === nudge.capability,
  ) || { name: nudge.capability, desc: "", args: [] };
  const world = worldToolsFor(env, envState);
  const minted = {
    ...nextEnvVersion(env, envState, {
      changed: ["contract"],
      note: `Added a ${tool.name} handler — approved from agent ${nudge.agentVersion}'s change.`,
    }),
    tools: [...world.filter((t) => t.name !== tool.name), tool],
    fromNudge: nudge.id,
  };
  const envVersions = [...storedEnvList(env, envState), minted];
  const after = { ...envState, envVersions, activeEnvVersion: minted.label };
  const { rows } = stampNewBatch(
    addScenarioRows(env, tool, nudge).slice(0, Math.max(1, Math.min(3, count))),
    { env, envState: after, source: "generated", actor: "nudge", at },
  );
  const ids = rows.map((r) => r.id);
  return {
    envLabel: minted.label,
    ids,
    patch: suiteLogPatch(
      envState,
      {
        envVersions,
        activeEnvVersion: minted.label,
        unsavedEnvChanges: [],
        scenarios: [...rows, ...(envState.scenarios || [])],
        ...resolveNudgePatch(envState, nudge.id, "approved", {
          envVersion: minted.label,
          addedIds: ids,
        }),
      },
      {
        summary: `${plural(rows.length, "new scenario")} for ${tool.name}, with its handler — shown apart until they have run on both versions`,
        by: "nudge",
      },
    ),
  };
};

/* ── Remove, behaviour, dismiss ──────────────────────────────────────────── */

/**
 * Acknowledge a Remove: the scenarios that need the tool are stale on agent
 * versions without it — they still run, but count toward no decision until
 * someone updates or archives them. Nothing retired.
 */
export const approveRemovePatch = (envState, nudge, { by = "nudge" } = {}) =>
  suiteLogPatch(
    envState,
    {
      ...staleRulePatch(envState, nudge),
    },
    {
      summary: `${plural(nudge.scenarioIds.length, "scenario")} stale on agent versions without ${nudge.capability} — they still run, counting toward no decision`,
      by,
    },
  );

/** Approve a behaviour change: E's handler follows the agent's tool. */
export const approveBehaviourPatch = (env, envState, nudge) => {
  const minted = nextEnvVersion(env, envState, {
    changed: ["toolbehaviour"],
    note: `Updated the ${nudge.capability} handler to match agent ${nudge.agentVersion}.`,
  });
  return {
    envVersions: [
      ...storedEnvList(env, envState),
      { ...minted, fromNudge: nudge.id },
    ],
    activeEnvVersion: minted.label,
    unsavedEnvChanges: [],
    ...resolveNudgePatch(envState, nudge.id, "approved", {
      envVersion: minted.label,
    }),
  };
};

/** Dismiss: E + S stay pinned; the drift badge stays so nothing is hidden. */
export const dismissPatch = (envState, nudge) =>
  resolveNudgePatch(envState, nudge.id, "dismissed");

/** Re-open an answered or dismissed nudge — change your mind later. */
export const reopenPatch = (envState, nudge) => ({
  nudges: (envState.nudges || []).map((n) =>
    n.id === nudge.id ? { ...n, status: "open", reopenedAt: nowIso() } : n,
  ),
});

/* ── behaviour nudges from a commit that changed the world ───────────────── */

/** A repository commit that changed a tool's handler code proposes updating E's (P12). */
export const behaviourNudgesOf = (world, version) =>
  (world?.tools || []).map((tool) => ({
    id: `ndg-behaviour-${version.label}-${tool}`,
    kind: "behaviour",
    agentVersion: version.label,
    prevVersion: version.basedOnVersion || null,
    at: nowIso(),
    status: "open",
    capability: tool,
    scenarioIds: [],
    paths: world.paths,
    title: `${tool} behaves differently in ${version.label}`,
    ask: `The commit changed ${tool}'s handler code. Update the environment's handler to match?`,
    detail:
      "Approve and the environment gets a new version; scenarios using it are rechecked. Dismiss and the new version is tested against the old behaviour.",
  }));

/* ── core change: a different channel forks a new environment ────────────── */

export const channelLabel = (surface) =>
  SURFACES[surface]?.label || surface || "chat";

/**
 * The fork nudge for an agent on a different channel or runtime (P21). The
 * fork carries the scenarios whose required capabilities still exist.
 */
export const coreNudgeOf = (
  env,
  envState,
  { newLabel, prevLabel, toSurface, agentName },
) => {
  const from = env?.surface || "chat";
  if (!toSurface || toSurface === from) return null;
  const tools = new Set(manifestOf(env, envState, newLabel).tools);
  const rows = (envState?.scenarios || []).filter(
    (r) => !r.archived && !r.retired,
  );
  const carried = rows
    .filter((r) => requiredCapsOf(r, env, envState).every((c) => tools.has(c)))
    .map((r) => r.id);
  const e = currentEnvVersion(env, envState)?.label || "v1";
  const shown = currentEnvSnapshot(env, envState)?.label || e;
  return {
    id: `ndg-core-${newLabel}-${toSurface}`,
    kind: "core",
    agentVersion: newLabel,
    prevVersion: prevLabel,
    at: nowIso(),
    status: "open",
    channel: { from, to: toSurface },
    envVersion: e,
    scenarioIds: carried,
    total: rows.length,
    title: `${agentName || "This agent"} runs on ${channelLabel(toSurface).toLowerCase()}; this environment is ${channelLabel(from).toLowerCase()}`,
    ask: `This agent looks different. Fork a new environment from version ${shown}?`,
    detail: `${carried.length} of ${rows.length} scenarios still have every capability they need and carry over with their ids. This environment stays on ${shown}.`,
  };
};

/**
 * Approve a fork: the new environment and its state, and the patch that
 * resolves the nudge here. The caller dispatches adoptEnvironment + patchEnvState.
 */
export const forkFromNudge = (env, envState, nudge) => {
  const at = nowIso();
  const forkedId = `${env.id}-fork-${Math.random().toString(36).slice(2, 8)}`;
  const carried = new Set(nudge.scenarioIds);
  const scenarios = (envState.scenarios || [])
    .filter((r) => carried.has(r.id))
    .map((r) => ({
      ...r,
      addedInEnv: "v1",
      provedAgainst: "v1",
      carriedFrom: { envId: env.id, envVersion: nudge.envVersion },
    }));
  const label = `forked from ${env.name} ${nudge.envVersion}`;
  const forked = {
    ...env,
    id: forkedId,
    name: `${env.name} · ${channelLabel(nudge.channel.to)}`,
    surface: nudge.channel.to,
    tagline: `${label.charAt(0).toUpperCase()}${label.slice(1)}`,
    custom: true,
    adoptedAt: undefined,
    buildProgress: undefined,
    forkedFrom: env.id,
    forkedFromVersion: nudge.envVersion,
  };
  const {
    runs,
    releases,
    optimizations,
    previousAgents,
    unsavedEnvChanges,
    envVersions,
    activeEnvVersion,
    nudges,
    applicability,
    suiteLog,
    suiteVersions,
    likelyFixes,
    ...rest
  } = envState;
  const forkState = {
    ...rest,
    scenarios,
    agentVersions: agentVersions(envState).map((v) => ({ ...v })),
    /* The fork tests the version that asked for it — pinned unless that's production. */
    ...agentPinPatch(
      { ...envState, agentVersionPinned: false },
      nudge.agentVersion,
    ),
    envVersions: [
      {
        id: `${forkedId}-v1`,
        label: "v1",
        createdAt: at,
        note: `Forked from ${env.name} at environment ${snapshotOfWorld(env, envState, nudge.envVersion)?.label || nudge.envVersion} for ${channelLabel(nudge.channel.to).toLowerCase()} — ${scenarios.length} of ${nudge.total} scenarios carried, ids kept.`,
        scenarios: scenarios.length,
        changed: ["contract"],
        tools: worldToolsFor(env, envState),
        builtFor: nudge.agentVersion,
      },
    ],
    activeEnvVersion: "v1",
    forkOf: {
      envId: env.id,
      envName: env.name,
      envVersion: nudge.envVersion,
      nudgeId: nudge.id,
      at,
    },
    seededFromTemplate: false,
  };
  return {
    forked,
    forkState,
    sourcePatch: resolveNudgePatch(envState, nudge.id, "approved", {
      forkId: forkedId,
      forkName: forked.name,
    }),
  };
};

/* ── version history (AC-14.4) ───────────────────────────────────────────── */

const runCount = (runs, pred) =>
  runs.filter((r) => !r.synthetic && pred(r)).length;

/**
 * Agent, environment and scenario-set versions, newest first, each with a
 * one-line summary, a timestamp and how many runs used it.
 */
export const versionHistoryOf = (env, envState) => {
  const runs = envState?.runs || [];
  const agent = [...agentVersions(envState)].reverse().map((v) => ({
    label: v.label,
    at: v.createdAt || v.connectedAt || null,
    summary:
      v.note ||
      (v.changes || []).map((c) => c.title).join(", ") ||
      "A new agent version",
    runs: runCount(runs, (r) => r.agentVersion === v.label),
  }));
  const environment = environmentVersions(env, envState).map((v) => ({
    label: v.label,
    at: v.createdAt || null,
    summary: v.note,
    runs: runCount(runs, (r) => r.envVersion === v.label),
    current: v.current,
  }));
  const log = suiteVersionLogOf(envState);
  const suite = [...log].reverse().map((l, k) => ({
    id: `${l.label || "s"}-${k}`,
    label: l.label ? `S v${versionNumber(l.label)}` : null,
    at: l.at,
    summary: l.summary,
    runs: l.label ? runCount(runs, (r) => r.suiteVersion === l.label) : 0,
  }));
  const current = currentSuiteLabel(envState);
  return {
    agent,
    environment,
    suite,
    suiteCurrent: current,
    suiteCurrentRuns: runCount(runs, (r) => r.suiteVersion === current),
  };
};

/**
 * An outside change found re-reading the agent as an improvement starts:
 * save it as an agent version, then raise in every environment using the
 * agent whatever it no longer fits — exactly what a Sync does.
 * `commit` is the store's dispatch. Returns what was raised.
 */
export const recordOutsideChange = (
  commit,
  state,
  { agent, reread, activateEnvId } = {},
) => {
  if (!agent?.key || !reread?.changed || !reread.versionRecord) return [];
  commit({
    type: "recordAgentVersion",
    key: agent.key,
    version: reread.versionRecord,
    activateEnvId,
  });
  const usedIn = (
    connectedAgentsFrom(state).find((a) => a.key === agent.key)?.usedIn || []
  ).map((u) => u.envId);
  const envs = (state?.myEnvironments || []).filter(
    (e) => usedIn.includes(e.id) || e.id === activateEnvId,
  );
  const raised = raiseNudgesIn(state, envs, reread.versionRecord);
  dispatchNudges(commit, raised);
  return raised;
};
