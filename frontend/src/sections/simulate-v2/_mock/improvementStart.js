/*
  Starting an improvement run — one way, whoever starts it (Fix on an Error
  Feed issue, New improvement towards a goal, Debug failures on a run).

  The search only tries what the agent's constraints allow and never edits a
  part it protects. The budget caps it: when the search would cost more, it
  runs the trials the budget pays for and says it stopped there.
*/
import {
  runSearch,
  searchScope,
  maxRunsOf,
  MIN_RUNS_BEFORE_STOP,
  NO_GAIN_RUNS,
} from "./optimizer";
import {
  fixDepthOf,
  proofKindOf,
  runDefaultsOf,
  CONFIG_AREAS,
} from "./rsiModel";
import { areaStatesOf } from "../agents/configAreas";
import { getEval } from "./evals";
import { omegaReport } from "./omega";
import { hashSeed } from "./runStream";
import { passShareOf } from "./optimize";
import { definitionOf, envAgentKey } from "./agentRegistry";
import { frontmatterOf, loadedVOf, skillRuntimeOf, skillSetOf } from "./skills";
import { agentVersions, currentEnvVersion } from "./versions";
import {
  repoTestsOf,
  rereadFor,
  rereadStamp,
  textSearchDefault,
} from "./improvementPolicy";
import { observabilityOf } from "./observability";
import { pinsForNewRun } from "./envModel";
import { runSummaries } from "./comparison";
import {
  frozenSnapshotOf,
  currentEnvSnapshot,
  snapshotLabelOf,
} from "./envSnapshot";
import { mustPassOf } from "./mustPass";
import {
  optimizationId,
  OPT_STATUS,
  PER_CONVERSATION,
  REPEATS,
  RATE,
  searchSpend,
  searchSpendParts,
  searchMinutes,
  trialsWithin,
  costCeilingLabel,
  searchConversations,
  limitGate,
} from "./optimizationRuns";

/** The newest finished run in an environment — on `agentVersion` when given — what an improvement measures against. */
export const baseRunOf = (env, es, agentVersion = null) =>
  (env ? runSummaries(env, es) : [])
    /* Finished only — a run still going (or queued) is no baseline. A finished
     run's status is its outcome ("passed" / "failed"), and either counts. */
    .filter((r) => r.status !== "running" && r.status !== "queued")
    .filter((r) => !agentVersion || r.agentVersion === agentVersion)
    .sort(
      (a, b) => Date.parse(b.finishedAt || 0) - Date.parse(a.finishedAt || 0),
    )[0] || null;

/** One run of an environment, read the way every run reader reads it — or null. */
export const runBaseOf = (env, es, runId) =>
  env && runId
    ? runSummaries(env, es).find((r) => r.id === runId) || null
    : null;

/*
  Measuring today is a real run of the environment: every scenario, every
  repeat. What it costs is part of what the improvement spends — priced the
  same in the setup's estimate and on the record (`spentFirst`).
*/
export const todayConversations = (run) =>
  run ? (run.scenarioIds || []).length * (run.repeats || 1) : 0;
/*
  The environment as it is once an outside change is re-read into a new
  version: that version in production, so today is measured on it and the
  baseline's version and scores match.
*/
export const envStateOnVersion = (es, reread) => {
  if (!es || !reread?.changed || !reread.version) return es;
  const label = reread.version;
  return {
    ...es,
    agentVersions: [
      ...agentVersions(es).filter((v) => v.label !== label),
      { ...(reread.versionRecord || {}), label },
    ],
    ...(es.agent && {
      agent: {
        ...es.agent,
        values: { ...(es.agent.values || {}), activeVersion: { label } },
      },
    }),
    agentVersionPinned: false,
  };
};
export const todaySpend = (run, surface) =>
  todayConversations(run) *
  PER_CONVERSATION[surface === "voice" ? "voice" : "chat"];

/*
  A Fix on a run's issue searches on that issue: its root and suggested fix
  become the search's first candidates. Only a run-insights trigger carries
  one, so every other improvement searches exactly as before.
*/
export const issueFocusOf = (trigger) =>
  trigger?.source === "run-insights" && trigger.issueRoot
    ? { ...trigger.issueRoot, issueId: trigger.issueId }
    : null;

/*
  The scenarios a search may use are the run's, read against the set as it
  stands (`es`) for the agent version being improved: archived and stale
  ones count toward no decision, and proposed ones stay out unless they're
  a fix's own.
  Without `es` every scenario the run played is used, as before.
*/
/*
  What the sealed environment was built around — the entry point and the
  environment version — for the Fits the environment check on code
  candidates. Improvements recorded before the check existed (and the
  seeded ones) carry no `compatCheck` stamp and skip it.
*/
export const COMPAT_CHECK = 1;
export const compatFor = (env, es) => {
  const def = definitionOf(es?.agent);
  const entry = def?.entry || null;
  return {
    entry: entry || "agent/main.py",
    envVersion: env ? currentEnvVersion(env, es)?.label || null : null,
    runtime: /\.(t|j)sx?(:|$)/.test(entry || "") ? "node" : "python",
  };
};

/*
  The newer engine (the updated RSI PRD): static checks that really check,
  configuration areas, deeper changes needing stronger proof, baseline
  twice, a Diagnose step, goal targets and a grouped held-back set for every
  improvement, repeats from the run defaults, voice defaults and finalists
  run in each environment picked. New improvements carry it, stamped on
  `frozen`; one recorded without the stamp — every seeded improvement, which
  is built with `compatCheck: 0` — replays exactly as it always did.
*/
export const RSI_ENGINE = 1;

/* Voice calls run in real time, so a voice improvement starts smaller. */
export const VOICE_DEFAULTS = { scenarios: 12, repeats: 2, trials: 4 };

/*
  Fix my agent: the engine tries at most four candidates; budget and time
  default to 1,500 credits and 2 h 30 min; held-back scenarios want at least
  four to tell a real fix from noise, eight recommended. Below the minimum it
  still starts — the fix is proven on the targets only.
*/
export const MAX_CANDIDATES = 4;
export const FIX_DEFAULTS = { dollars: 15, minutes: 150 };
export const HELD_BACK = { minimum: 4, recommended: 8 };

/** "2 h 30 min" — a time limit in the review sentence. */
export const hoursLabel = (n) =>
  n < 60
    ? `${n} min`
    : `${Math.floor(n / 60)} h${n % 60 ? ` ${n % 60} min` : ""}`;

const PART_WORDS = {
  prompt: "instructions",
  tools: "tool descriptions",
  settings: "settings",
  skills: "skills",
  memory: "memory",
  code: "code",
  structure: "structure",
};
const listWords = (xs) =>
  xs.length < 2
    ? xs[0] || ""
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** The one sentence a fix is reviewed by before it starts. */
export const fixReviewSentence = ({
  targets,
  protectedN,
  allowed,
  credits,
  minutes,
}) =>
  `Fix ${targets} failing ${targets === 1 ? "case" : "cases"}${protectedN ? ` without breaking ${protectedN} protected ${protectedN === 1 ? "case" : "cases"}` : ""}, changing ${listWords((allowed || []).map((a) => PART_WORDS[a] || a))} only, up to ${credits} or ${hoursLabel(minutes)}.`;

/** Where a fix's held-back set stands against the minimum and recommendation. */
export const heldBackOf = (n) => ({
  n,
  ...HELD_BACK,
  tooFew: n < HELD_BACK.minimum,
  belowRecommended: n < HELD_BACK.recommended,
});
export const voiceDefaultsLine = () =>
  `Voice: ${VOICE_DEFAULTS.scenarios} scenarios × ${VOICE_DEFAULTS.repeats} tries each, up to ${VOICE_DEFAULTS.trials} runs by default — calls run in real time.`;

/* The scenarios a voice improvement keeps: the issue's own, then what fails,
   then must-pass, then the rest — each group by a fixed hash, never at random. */
const voiceSlice = (tasks, seed, cap) => {
  if (tasks.length <= cap) return tasks;
  const rank = (t) =>
    t.fromIssue
      ? 0
      : passShareOf(t) < 1 && t.status !== "unmeasured"
        ? 1
        : t.critical
          ? 2
          : 3;
  return [...tasks]
    .sort(
      (a, b) =>
        rank(a) - rank(b) ||
        hashSeed(`${seed}:voice:${a.id}`) - hashSeed(`${seed}:voice:${b.id}`),
    )
    .slice(0, cap);
};

/* How many times each scenario runs: the environment's run default, or two
   for a voice agent nobody set one for (new improvements only). */
export const repeatsFor = (env, es) => {
  const d = runDefaultsOf(es).repeats;
  return env?.surface === "voice" && es?.runDefaults?.repeats == null
    ? Math.min(d, VOICE_DEFAULTS.repeats)
    : d;
};

/*
  An environment's must-pass set, off its newest finished run: the scenarios
  that passed every repeat (mustPass.js — the same rule as the main
  environment). Marked ones that are flaky today are named, not counted.
    → { gated, markedNotCounted, scenarios }
*/
export const envMustPassOf = (env, es) => {
  const base = env && es ? baseRunOf(env, es) : null;
  const scope = searchScope({ env, envState: es, tasks: base?.tasks || [] });
  const { gated, markedNotCounted } = mustPassOf(scope.tasks, {
    marked: (t) => !!t.critical || scope.remembered(t),
  });
  return { gated, markedNotCounted, scenarios: (base?.tasks || []).length };
};

/* The other environments an improvement checks: what must keep passing in each. */
const otherEnvsFor = (env, state, regressionEnvIds = []) =>
  (regressionEnvIds || [])
    .filter((id) => id && id !== env?.id)
    .map((id) => {
      const e = (state?.myEnvironments || []).find((x) => x.id === id);
      const oes = state?.byEnv?.[id];
      if (!e || !oes) return null;
      const { gated, scenarios } = envMustPassOf(e, oes);
      return {
        id,
        name: e.name,
        mustPass: gated.map((t) => ({ id: t.id, title: t.title })),
        scenarios,
      };
    })
    .filter(Boolean);

/*
  Finished runs that already measured today's agent here: the whole set, same
  agent version, and the environment unchanged since (same snapshot — world,
  scenarios, evals, caller). The latest two count toward the baseline's two
  measurements, so it isn't paid for again.
*/
const todayRunsOf = (env, es, agentVersion) => {
  if (!env || !agentVersion) return [];
  const now = currentEnvSnapshot(env, es)?.label;
  return (es?.runs || [])
    .filter(
      (r) =>
        r.finishedAt &&
        !r.pending &&
        r.status !== "running" &&
        r.kind !== "trial" &&
        !r.partial &&
        r.agentVersion === agentVersion &&
        (r.scenarioIds || []).length &&
        snapshotLabelOf(r, env, es) === now,
    )
    .sort((a, b) => Date.parse(b.finishedAt) - Date.parse(a.finishedAt))
    .slice(0, 2)
    .map((r) => ({
      id: r.id,
      label: `Run ${r.ordinal || 1}`,
      scenarioIds: r.scenarioIds,
    }));
};

/* What the newer engine reads off the agent and its environment. */
const engineConfig = ({
  env,
  es,
  limits,
  agentVersion,
  state,
  regressionEnvIds,
  tasks,
  seed,
}) => {
  const def = definitionOf(es?.agent) || {};
  const states = limits?.areas ? areaStatesOf(limits) : null;
  const voice = env?.surface === "voice";
  return {
    rsi: {
      calibration: 1,
      todayRuns: todayRunsOf(env, es, agentVersion),
      version: agentVersion || null,
      areasOff: states
        ? Object.entries(states)
            .filter(([, on]) => !on)
            .map(([id]) => id)
        : [],
      areaLabels: Object.fromEntries(CONFIG_AREAS.map((a) => [a.id, a.label])),
      approvedSpeech: Array.isArray(limits?.approvedSpeech)
        ? limits.approvedSpeech
        : [],
      codeAgent: def.kind === "repo" || def.kind === "framework",
      /* The areas' sub-rules from the Constraints tab (off unless turned on). */
      areaRules: {
        whoSpeaksFirst: !!limits?.areaRules?.whoSpeaksFirst,
        toolsAddRemove: !!limits?.areaRules?.toolsAddRemove,
      },
      evalNames: (es?.evals || []).map((e) => {
        const id = typeof e === "string" ? e : e.id;
        return getEval(id)?.name || id;
      }),
    },
    repeats: repeatsFor(env, es),
    ...(state &&
      (regressionEnvIds || []).some((id) => id !== env?.id) && {
        otherEnvs: otherEnvsFor(env, state, regressionEnvIds),
      }),
    ...(voice &&
      (tasks || []).length > VOICE_DEFAULTS.scenarios && {
        voiceCap: {
          scenarios: VOICE_DEFAULTS.scenarios,
          of: tasks.length,
          repeats: repeatsFor(env, es),
          trials: VOICE_DEFAULTS.trials,
          seed,
        },
      }),
  };
};

/* The cost and latency ceilings, checked on every candidate as it runs —
   the one gate every screen uses (optimizationRuns.limitGate). */
/*
  The skills an improvement may change: the ones the agent loads, narrowed
  to the one picked (limits.skillFocus), each at the version it runs.
*/
export const skillsForSearch = (env, es, state, limits) => {
  if (!(limits?.allowed || []).includes("skills") || !es?.agent) return null;
  const agent = {
    ...es.agent,
    key: es.agent.key || envAgentKey(es.agent, env?.id),
  };
  const set = skillSetOf(agent, state?.workspaceSettings);
  const skills = set.skills
    .filter(
      (x) =>
        x.state === "on" && (!limits.skillFocus || x.id === limits.skillFocus),
    )
    .map((x) => {
      const v = loadedVOf(x);
      const md =
        (
          x.versions.find((y) => y.v === v) || x.versions[x.versions.length - 1]
        ).files.find((f) => f.path === "SKILL.md")?.text || "";
      return {
        id: x.id,
        v,
        md,
        description: frontmatterOf(md)?.description || "",
      };
    });
  return {
    skills,
    skillDepth: limits.skillDepth || "description",
    skillObserved: !!skillRuntimeOf(agent).observed,
  };
};

const limitGateFor = (limits, env, es) =>
  limitGate({
    limits,
    costKnown: costMeasuredFor(es?.agent),
    surface: env?.surface,
  });

export const planSearch = ({
  env,
  es,
  agentVersion,
  tasks,
  limits,
  seed,
  maxTrials,
  lineage,
  issue,
  textSearch,
  compatCheck = COMPAT_CHECK,
  engine = compatCheck ? RSI_ENGINE : 0,
  goal,
  optimizerId,
  state,
  regressionEnvIds,
  diagnosis = null,
  /* Edit plan's pick: { fix: [ids], protect: [ids] } — null keeps the suggestion. */
  pick = null,
  /* Changes reviewed before running: { items } — null tries the search's own. */
  candidates = null,
}) => {
  const eng = engine
    ? engineConfig({
        env,
        es,
        limits,
        agentVersion,
        state,
        regressionEnvIds,
        tasks,
        seed,
      })
    : null;
  const list = eng?.voiceCap
    ? voiceSlice(tasks || [], seed, VOICE_DEFAULTS.scenarios)
    : tasks || [];
  /* New improvements try at most four candidates, voice included. */
  const trialCap = eng ? MAX_CANDIDATES : null;
  return runSearch({
    env,
    tasks: list,
    ...(eng && { limitGate: limitGateFor(limits, env, es) }),
    scope: searchScope({ env, envState: es, tasks: list, agentVersion }),
    /* Reflective (GEPA) by default for new improvements; recorded ones searched with ProTeGi. */
    optimizerId: optimizerId || (engine ? "gepa" : "protegi"),
    config: {
      ...(eng || {}),
      ...(eng && goal && { goal }),
      ...(eng && diagnosis?.mechanism && { frame: diagnosis.mechanism }),
      ...(eng && pick && { pick }),
      ...(eng && candidates && { candidates }),
      allowed: limits.allowed,
      protected: limits.protected,
      ...(skillsForSearch(env, es, state, limits) || {}),
      /* Model switches are candidates only when the agent names approved models. */
      models:
        limits.model?.mode === "approved" ? limits.model.approved || [] : [],
      /* Earlier versions' changes, when the search runs across the lineage. */
      ...(lineage?.length ? { lineage } : {}),
      ...(maxTrials || trialCap
        ? { maxTrials: Math.min(maxTrials || Infinity, trialCap || Infinity) }
        : {}),
      ...(issue ? { issue } : {}),
      /* The repository's own tests, a static check on code candidates. */
      ...(repoTestsOf(es?.agent) ? { repoTests: repoTestsOf(es?.agent) } : {}),
      /* Fits the sealed environment — entry point, build, start command. */
      ...(compatCheck ? { compat: compatFor(env, es) } : {}),
      /* Phase 2: a voice agent searched in text, finalists confirmed as real calls. */
      ...(env?.surface === "voice" &&
      (textSearch ?? textSearchDefault(env?.surface))
        ? { textSearch: true }
        : {}),
      /* A voice search keeps a slice of the scenarios, but must-pass is the
       whole suite's (RSI PRD: every scenario that passes all its repeats on
       the current version) — run for finalists only, so it costs the
       finalists, not every candidate. */
      ...(eng?.voiceCap
        ? {
            mustPassPool: searchScope({
              env,
              envState: es,
              tasks: tasks || [],
              agentVersion,
            }).tasks,
          }
        : {}),
    },
    seed,
  });
};

/*
  What the search can stop early on, said once for every estimate: the
  planned run count is a maximum.
*/
export const stopEarlyNote = (max = MAX_CANDIDATES) =>
  max > MIN_RUNS_BEFORE_STOP
    ? `After at least ${MIN_RUNS_BEFORE_STOP} trials, stops early if ${NO_GAIN_RUNS} in a row don't beat the best on the target scenarios by more than trial-to-trial noise, and verifies the best — so it often spends less.`
    : "Stops at the limit with the best it found, and verifies the best.";

/**
 * What an improvement will spend, as a range: the search on the main
 * environment, plus any other environments' scenarios run for the finalists,
 * plus anything run first (reproducing an issue). For a voice agent searched
 * in text, also what it would cost and take with every trial a real call.
 *
 * Priced at the most runs the search can use (`runs`) — it stops early when
 * runs stop gaining, so this is a ceiling, not a forecast.
 */
export const improvementEstimate = ({
  preview,
  surface,
  extraScenarios = 0,
  firstConversations = 0,
  /* What the first run covers, in words — a fix names the environment's
     scenarios and the issue's. */
  firstDetail = "today's agent, measured before anything changes",
}) => {
  const kind = surface === "voice" ? "voice" : "chat";
  const per = PER_CONVERSATION[kind];
  const reps = preview?.repeats || REPEATS;
  const runs = preview ? maxRunsOf(preview) : 0;
  /* A new improvement prices its other environments inside the search. */
  const extraN = preview?.otherEnvs ? 0 : extraScenarios;
  const extra = (extraN * reps * 2 + firstConversations) * per;
  const likely = searchSpend(preview, surface, { trials: runs }) + extra;
  const conversations = preview
    ? searchConversations(preview, { trials: runs }) +
      extraN * reps * 2 +
      firstConversations
    : Math.round(likely / per);
  const minutes = preview
    ? searchMinutes(preview, surface, {
        trials: runs,
        firstConversations: firstConversations + extraN * reps * 2,
      })
    : Math.round(8 + conversations / RATE[kind]);
  /* The parts the estimate is made of, for a breakdown before it starts. */
  const firstN = firstConversations;
  const extraConv = extraN * reps * 2;
  const parts = [
    ...(firstN
      ? [
          {
            id: "reproduce",
            label: "First run",
            detail: firstDetail,
            conversations: firstN,
            usd: firstN * per,
          },
        ]
      : []),
    ...(preview ? searchSpendParts(preview, surface, { trials: runs }) : []),
    ...(extraConv
      ? [
          {
            id: "regressions",
            label: "Other environments",
            detail: "their scenarios, for regressions",
            conversations: extraConv,
            usd: extraConv * per,
          },
        ]
      : []),
  ];
  const out = {
    parts,
    runs,
    conversations,
    low: Math.round(likely * 0.8),
    likely: Math.round(likely),
    high: Math.round(likely * 1.25),
    minutes,
  };
  if (preview?.textSearch && surface === "voice") {
    const allCalls =
      searchSpend({ ...preview, textSearch: false }, surface, {
        trials: runs,
      }) + extra;
    out.allCalls = {
      likely: Math.round(allCalls),
      minutes: searchMinutes(preview, surface, {
        trials: runs,
        allCalls: true,
        firstConversations: firstConversations + extraN * reps * 2,
      }),
    };
  }
  return out;
};

export const BUDGETS = [15, 25, 50, 100, 250, 500];

/** The budgets an agent's run budget allows — never more than it. */
export const budgetsWithin = (ceiling) => {
  const list = BUDGETS.filter((b) => b <= ceiling);
  return list.length ? list : [ceiling];
};

/**
 * Can cost per conversation be measured for this agent? Vapi and Retell
 * report it; anything else needs its traceAI spans. Without either the cost
 * check is unverified — never passed.
 */
export const costMeasuredFor = (agent) =>
  ["vapi", "retell"].includes(agent?.values?.provider) ||
  !!observabilityOf(agent).on;

/**
 * Infrastructure that fails under the search, not the agent. A browser
 * environment needs a browser sandbox for every candidate run; the first
 * search there loses some of them, and says so instead of scoring a crash as
 * a candidate failing. Trying again gets a healthy pool.
 */
export const infraFailureOf = (env, es, trials) => {
  if (env?.surface !== "browser") return null;
  if ((es?.optimizations || []).some((o) => o.infra)) return null;
  const runs = Math.max(6, (trials || 8) * 2);
  const lost = Math.round(runs * 0.38);
  return {
    reason: `The browser sandbox crashed on ${lost} of ${runs} runs (Chromium ran out of memory), so the runs couldn't be compared fairly.`,
  };
};

/**
 * The improvement record, ready to patch into the environment. `spentFirst`
 * is what was spent before the search (reproducing), taken off the budget.
 */
export const buildImprovement = ({
  env,
  es,
  tasks,
  fromRunId,
  baseAgentVersion,
  trigger,
  limits,
  budget,
  regressionEnvIds,
  spentFirst = 0,
  seed,
  extra = {},
  hold = false,
  lineage,
  state,
  textSearch,
  targetMember,
  issueScenarios,
  reread: given,
  compatCheck = COMPAT_CHECK,
  optimizerId,
  pick = null,
  candidates = null,
}) => {
  const records = es?.optimizations || [];
  const issue = issueFocusOf(trigger);
  const engine = compatCheck ? RSI_ENGINE : 0;
  /* Held to the agent's own limits, whichever engine searches — never a
     ceiling the agent didn't set. */
  /* A goal improvement (or a run's failures) aims at its goal; a fix at its issue. */
  const goal =
    trigger?.kind === "error-feed" || trigger?.source === "run-insights"
      ? null
      : trigger?.goal || { id: "pass-rate", label: "Pass rate" };
  /* A fix's diagnosis frames where the search starts (new improvements). */
  const diagnosis = extra?.snapshot?.diagnosis || null;
  const engineArgs = engine
    ? {
        goal,
        optimizerId,
        state,
        regressionEnvIds,
        diagnosis,
        pick,
        candidates,
      }
    : { optimizerId };
  /* Re-read the agent first, so the baseline always matches production. A
     caller with the connected agent re-reads it and saves any version found;
     otherwise it's re-read here when there's a workspace to read it against. */
  const reread = rereadStamp(
    given !== undefined
      ? given
      : state && es?.agent
        ? rereadFor(
            { ...es.agent, key: es.agent.key || envAgentKey(es.agent, env.id) },
            state,
          )
        : null,
  );
  /* A re-read the caller made (and measured today on) moves the baseline to
     the new version. One made here, with today already measured, can't — the
     scores belong to the version they were run on; the change is only noted. */
  const baseVersion =
    given !== undefined && reread?.changed && reread.version
      ? reread.version
      : baseAgentVersion;
  const full = planSearch({
    env,
    es,
    agentVersion: baseVersion,
    tasks,
    limits,
    seed,
    lineage,
    issue,
    textSearch,
    compatCheck,
    ...engineArgs,
  });
  const reps = full.repeats || REPEATS;
  const over = searchSpend(full, env.surface) + spentFirst > budget;
  const kMoney = over
    ? trialsWithin(full, budget - spentFirst, env.surface)
    : Infinity;
  /* The time budget caps it the same way: trials the minutes pay for. */
  const firstConversations =
    spentFirst / PER_CONVERSATION[env.surface === "voice" ? "voice" : "chat"];
  const minutesFor = (trials) =>
    searchMinutes(full, env.surface, { trials, firstConversations });
  const maxMinutes = limits?.budget?.minutes || Infinity;
  const trialRate =
    RATE[env.surface === "voice" && !full.textSearch ? "voice" : "chat"];
  const finalRate = RATE[env.surface === "voice" ? "voice" : "chat"];
  /* Zero when the finalists' checks alone take longer than the cap. */
  const kTime =
    minutesFor(full.trials.length) > maxMinutes
      ? Math.max(
          0,
          Math.floor(
            ((maxMinutes -
              8 -
              ((full.finalists?.length || 1) * full.heldMeasured * reps +
                (full.mustPassMeasured || 0) *
                  (full.finalists?.length || 1) *
                  reps +
                firstConversations) /
                finalRate) *
              trialRate) /
              (reps * Math.max(1, full.trainMeasured)),
          ),
        )
      : Infinity;
  const k = Math.min(kMoney, kTime);
  /* Checking the finalists alone would go over the budget or the time cap:
     it stops at the budget before searching, never over it. */
  const overBefore = k <= 0;
  const result =
    !overBefore && k < full.trials.length
      ? planSearch({
          env,
          es,
          agentVersion: baseVersion,
          tasks,
          limits,
          seed,
          maxTrials: k,
          lineage,
          issue,
          textSearch,
          compatCheck,
          ...engineArgs,
        })
      : full;
  /* Nothing falls short of the goal: the improvement ends before searching. */
  const nothing =
    !!engine && !hold && !!result.goal && result.goalTargets === 0;
  const by = kTime < kMoney ? "time" : "money";
  const stopBefore = overBefore && !hold && !nothing;
  const infra = hold ? null : infraFailureOf(env, es, result.trials.length);
  const proof = proofKindOf(es?.agent);
  /* Fresh variations the held-back set wrote are the improvement's too. */
  const pending = [...(issueScenarios || [])];
  (result.held || [])
    .filter((t) => t.fresh)
    .forEach((t) => {
      if (pending.some((p) => p.id === t.id)) return;
      const rep =
        pending.find((p) => (p.groupId || p.id) === t.groupId) ||
        (es?.scenarios || []).find((r) => (r.groupId || r.id) === t.groupId) ||
        {};
      pending.push({
        ...rep,
        id: t.id,
        name: `${
          rep.name ||
          String(t.groupId)
            .replace(/[^a-z0-9]+/gi, "-")
            .toLowerCase()
        }-fresh`,
        title: t.title,
        groupId: t.groupId,
        freshVariation: true,
        /* Kept because it reproduces today — a target, like the issue's own. */
        target: true,
        critical: !!t.critical,
      });
    });
  const ending = hold
    ? OPT_STATUS.NOT_REPRODUCED
    : nothing
      ? OPT_STATUS.NOTHING
      : stopBefore
        ? OPT_STATUS.OVER_BUDGET
        : infra
          ? OPT_STATUS.FAILED
          : OPT_STATUS.RUNNING;
  /* An ending known before searching still reproduces first: the record
     starts running and settles to it once Reproduce has played
     (optimizationRuns.statusOf). Records kept exactly as seeded (no engine
     stamp) end at once, as they always did. */
  const pendingEnding = engine && ending !== OPT_STATUS.RUNNING ? ending : null;
  const createdAt = new Date().toISOString();
  return {
    id: optimizationId(es),
    name: `Improvement ${records.length + 1}`,
    optimizerId: result.optimizer?.id || "protegi",
    model: "claude-opus-5",
    status: pendingEnding ? OPT_STATUS.RUNNING : ending,
    ...(pendingEnding && { pendingEnding, startedAt: createdAt }),
    ...(nothing && { nothing: { goal: result.goal } }),
    createdAt,
    fromRunId,
    baseAgentVersion: baseVersion,
    includedIds: [],
    /* What Diagnose found (new improvements): an Error Feed fix's root cause
       and fix layer, else the Debug-failures analyzers on these scenarios. */
    diagnosis: engine ? diagnosisFor({ env, tasks, extra }) : [],
    trigger,
    limits,
    budget,
    /* What ran before the search (reproducing an issue) — part of what it spent. */
    spentFirst,
    regressionEnvIds,
    costMeasured: costMeasuredFor(es?.agent),
    /* How deep a fix can go: an agent we can only call ends in a suggestion. */
    fixDepth: fixDepthOf(es?.agent).depth,
    /* Where candidates run and how far the proof can be trusted. */
    proof: {
      kind: proof.kind,
      label: proof.label,
      tier: proof.tier,
      tierLabel: proof.tierLabel,
    },
    ...(reread && { reread }),
    ...(result.textSearch && { textSearch: true }),
    ...(targetMember && { targetMember }),
    /* Scenarios this improvement wrote stay with it until someone adds them. */
    ...(pending.length && { issueScenarios: pending }),
    ...(lineage?.length ? { lineage: lineage.map((v) => v.label) } : {}),
    surface: env.surface,
    /* What every verdict is computed against — frozen now, never edited. */
    frozen: {
      scenarios:
        result.trainMeasured +
        result.heldMeasured +
        (result.mustPassMeasured || 0),
      held: result.heldMeasured,
      /* The three sets, kept apart (new improvements). */
      ...(result.mustPassMeasured != null && {
        targets: result.trainMeasured,
        mustPassSet: result.mustPassMeasured,
      }),
      evaluations: (es?.evals || []).map((e) =>
        typeof e === "string" ? e : e.id,
      ),
      /* Must-pass: marked, regression memory, and everything passing every repeat today. */
      mustPass:
        result.mustPass?.total ??
        [...result.train, ...result.held].filter((t) => t.critical).length,
      ...(result.mustPass && { mustPassParts: result.mustPass }),
      ...(result.trainMeasuredTasks.some((t) => t.anchor) && {
        anchors: result.trainMeasuredTasks.filter((t) => t.anchor).length,
      }),
      model: limits?.model || null,
      cost: costCeilingLabel(limits),
      protected: limits?.protected || [],
      ...((limits?.protectedMembers || []).length && {
        protectedMembers: limits.protectedMembers,
      }),
      ...(result.repoTests && { repoTests: result.repoTests }),
      /* Code candidates checked against the sealed environment (missing = skipped). */
      ...(result.compat && {
        compatCheck: COMPAT_CHECK,
        compat: result.compat,
      }),
      /* The newer engine's checks (missing = recorded before them). */
      ...(engine && {
        staticCheck: 1,
        areaCheck: 1,
        depthRule: 1,
        calibration: 1,
        diagnoseStep: 1,
        goalTargets: 1,
        heldGroups: 1,
        inconclusiveOut: 1,
        costNoise: 1,
        multiEnv: 1,
        repeats: reps,
        ...(Array.isArray(limits?.approvedSpeech) && {
          approvedSpeech: limits.approvedSpeech,
        }),
        ...(limits?.areas && { areas: limits.areas }),
        ...(result.voiceCap && { voiceCap: result.voiceCap }),
        ...(result.calibration && { calibrationNoise: result.noise }),
      }),
      /* The other pins a comparison holds fixed — only the agent varies. */
      ...pinsForNewRun(env, es),
      /* The environment version it is frozen on — one number for all of the
         above (envSnapshot.js). Display only; nothing replays from it. */
      ...(env && { snapshot: frozenSnapshotOf(env, es) }),
      rules: (env?.rules || []).length,
    },
    minutes: minutesFor(result.trials.length),
    /* Each run plays about as long as a run of its size would: its target
       scenarios × repeats × ~5 s a conversation, 20 s to 2 min. */
    runMs: Math.min(
      120000,
      Math.max(20000, (result.trainMeasured || 3) * reps * 5000),
    ),
    ...(stopBefore
      ? {
          stoppedAtBudget: {
            budget,
            trials: 0,
            of: full.trials.length,
            by,
            minutes: limits?.budget?.minutes,
          },
        }
      : result !== full
        ? {
            stoppedAtBudget: {
              budget,
              trials: result.trials.length,
              of: full.trials.length,
              by,
              minutes: limits?.budget?.minutes,
            },
            /* The whole search — what a raised budget continues into. */
            continueResult: full,
          }
        : {}),
    /* Held back when the run stops before searching — kept for "Continue anyway". */
    ...(result.calibration && { calibration: result.calibration }),
    ...(hold || infra || nothing || stopBefore
      ? { result: null, pendingResult: result }
      : { result }),
    ...(infra && { infra }),
    ...extra,
  };
};

/* The Diagnose step's input for a new improvement. */
const diagnosisFor = ({ env, tasks, extra }) => {
  if (extra?.diagnosis) return extra.diagnosis;
  const snap = extra?.snapshot;
  if (snap?.rootCause || snap?.fixLayer) {
    return [
      {
        id: `root:${snap.issueId}`,
        label: snap.title || snap.issueId,
        headline: snap.rootCause || null,
        layer: snap.fixLayer || null,
        source: "error-feed",
      },
    ];
  }
  try {
    return omegaReport({ env, summary: { tasks: tasks || [] }, baseline: null })
      .filter((a) => !a.nothing)
      .slice(0, 3)
      .map((a) => ({
        id: a.id,
        label: a.label,
        headline: a.headline,
        severity: a.severity,
        source: "debug-failures",
      }));
  } catch (e) {
    return [];
  }
};

/*
  Outcome metrics — goals like "booking success". Not an eval with a judge:
  read straight off the conversation's tool calls, so they can't be talked
  round. One per tool that completes something for the caller, named after
  what it completes.
*/
const OUTCOME_VERBS = [
  [/^(book|reserve|schedule)/i, "booking success rate", false],
  [/^refund/i, "refund completion rate", false],
  [/^cancel/i, "cancellation completion rate", false],
  [/^(transfer|escalat|handoff)/i, "Resolution without transfer", true],
  [/^(order|purchase|checkout)/i, "order completion rate", false],
  [/^(update|change|modify|reschedul)/i, "change success rate", false],
];
/* Tools that only read — never an outcome, whatever else their name says. */
const READ_ONLY =
  /^(lookup|look|get|check|find|list|read|search|fetch|query)$/i;
/* Words in a tool's name that aren't what it acts on. */
const OUTCOME_FILLER = new Set([
  "to",
  "a",
  "the",
  "for",
  "on",
  "of",
  "new",
  "existing",
  "human",
  "agent",
  "request",
  "api",
]);
/*
  Named after what the tool acts on, in its own words — book_pickup is
  "Pickup booking success rate", not a generic "Booking" — with the
  definition said where the goal is picked.
*/
export const outcomeMetricsOf = (env) => {
  const out = new Map();
  (env?.tools || []).forEach((t) => {
    const parts = String(t.name || "")
      .split(/[_\-.\s]+/)
      .filter(Boolean);
    /* The verb is the tool name's first word — get_booking_status reads a
       booking, it doesn't make one. */
    if (!parts.length || parts.some((p) => READ_ONLY.test(p))) return;
    const verb = OUTCOME_VERBS.find(([re]) => re.test(parts[0]));
    if (!verb) return;
    const [, base, inverse] = verb;
    const object = parts
      .slice(1)
      .filter((p) => !OUTCOME_FILLER.has(p.toLowerCase()))
      .join(" ");
    const label = inverse
      ? base
      : object
        ? `${object.charAt(0).toUpperCase()}${object.slice(1)} ${base}`
        : `${base.charAt(0).toUpperCase()}${base.slice(1)}`;
    if (out.has(label)) return;
    out.set(label, {
      id: `outcome:${t.name}`,
      label,
      tool: t.name,
      note: inverse
        ? `Conversations that end without ${t.name} being called`
        : `Conversations where ${t.name} returned success`,
    });
  });
  return [...out.values()];
};
