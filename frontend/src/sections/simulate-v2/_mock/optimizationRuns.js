/**
 * Optimization runs — the record, not the search.
 *
 * An optimization belongs to the environment and its agent, not to the one
 * simulation run that happened to prompt it. Filing them under a run would mean
 * a team's whole optimization history vanished the day somebody tidied up their
 * runs list, and it would make "how has this agent been tuned over time" a
 * question with no screen behind it. So they live on the environment, each one
 * remembering which run it was started from.
 *
 * Every record carries the diagnosis it was created against. A search is only
 * as good as the problem statement it was given, and six weeks later "why did
 * we try that" is answerable only if the evidence travelled with the attempt.
 */

import {
  OPTIMIZERS,
  optimizerById,
  KIND_SURFACE,
  blockingVerdict,
  protectedHit,
  depthOf,
  areaOfProposal,
  searchTrace,
  goalWords,
  noGainRunsOf,
  plateauStopOf,
} from "./optimizer";
import { CONFIG_AREAS } from "./rsiModel";
import { creditsLabel } from "./metering";
import { PER_CONVERSATION_USD } from "./rates";

export const OPT_STATUS = {
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  /* Started from an Error Feed issue whose failure didn't happen on today's
     agent — it stops before searching. */
  NOT_REPRODUCED: "not-reproduced",
  /* A new goal improvement where every scenario already meets the goal —
     it ends before searching. */
  NOTHING: "nothing-to-improve",
  /* Checking the finalists alone would cost more than the budget (or take
     longer than its time cap) — it stops before searching. */
  OVER_BUDGET: "over-budget",
  /* Stopped by a person while it ran: the trials that finished stay, with
     what they cost; none was checked on the held-back scenarios, so there's
     no winner and today's version stays. */
  STOPPED: "stopped",
};

/**
 * Stop a running improvement where it is (`progress` from
 * improvementProgressOf). The trials that finished are kept as evidence and
 * spend; finalists, held-back results and the winner are dropped — none of
 * them were checked.
 */
export const stopImprovement = (record, progress, now = Date.now()) => {
  const r = record?.result || null;
  const done = progress?.runsDone || 0;
  const at = new Date(now).toISOString();
  const { pendingEnding: _pending, ...rest } = record || {};
  return {
    ...rest,
    status: OPT_STATUS.STOPPED,
    completedAt: at,
    stoppedBy: {
      at,
      trials: done,
      of: progress?.runsMax || r?.trials?.length || 0,
      /* Past the first stage, today's agent has been measured. */
      measured: !!progress && progress.stage !== "reproduce",
    },
    result: r
      ? {
          ...r,
          trials: (r.trials || []).slice(0, done),
          finalists: [],
          winner: null,
          heldPerScenario: {},
        }
      : null,
  };
};

/**
 * Models that can drive the search.
 *
 * This is the model doing the optimizing — reading failures and writing
 * candidate prompts — not the model under test. Worth keeping separate in
 * someone's head, because the strongest available model is usually the right
 * choice here even when the agent itself runs on something cheaper.
 */
export const OPTIMIZER_MODELS = [
  {
    id: "claude-opus-5",
    label: "Claude Opus 5",
    note: "Strongest at reading failures and writing the fix",
  },
  {
    id: "claude-sonnet-5",
    label: "Claude Sonnet 5",
    note: "Faster, close behind on prompt rewriting",
  },
  {
    id: "gpt-5",
    label: "GPT-5",
    note: "Second opinion — a different model proposes differently",
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    note: "Cheapest; fine for many small variations",
  },
];

/* The planner reads the failures and decides what to change — it decides
   the outcome. The writer turns each plan into the exact edit, and can be a
   cheaper model. Same as the planner unless someone picks otherwise. */
export const SAME_AS_PLANNER = "same";
export const modelLabelOf = (id) =>
  OPTIMIZER_MODELS.find((m) => m.id === id)?.label || id;
export const improverModelsText = (planner, writer) =>
  !writer || writer === SAME_AS_PLANNER || writer === planner
    ? `planned and written by ${modelLabelOf(planner)}`
    : `planned by ${modelLabelOf(planner)}, written by ${modelLabelOf(writer)}`;

export const optimizations = (envState) => envState?.optimizations || [];

/** Newest first, which is the only order a runs list is ever read in. */
export const optimizationList = (envState) =>
  [...optimizations(envState)].sort((a, b) =>
    a.createdAt < b.createdAt ? 1 : -1,
  );

/** The ones a given simulation run produced. */
export const optimizationsFromRun = (envState, runId) =>
  optimizationList(envState).filter((o) => o.fromRunId === runId);

export const nextOptimizationName = (envState) =>
  `Improvement ${optimizations(envState).length + 1}`;

/* One past the highest id, never the count — a deleted improvement's id is
   never handed to a new one (its drafts and links still name it). */
export const optimizationId = (envState) =>
  `OPT-${String(optimizations(envState).reduce((m, o) => Math.max(m, Number(String(o.id).replace(/\D/g, "")) || 0), 0) + 1).padStart(5, "0")}`;

/**
 * One row's worth of headline, so the list and the detail header cannot drift.
 *
 * `heldScore` is the number that leads, never the training best — a list that
 * ranks runs by what they scored on the split they were tuned against is a
 * leaderboard of overfitting.
 */
export const optimizationSummary = (record) => {
  const r = record?.result;
  if (!r) return { headline: "—", sub: "", tone: "text.disabled" };
  if (record.status === OPT_STATUS.RUNNING) {
    return {
      headline: "Running",
      sub: `${r.trials?.length || 0} trials`,
      tone: "#CA8A04",
    };
  }
  const lift = r.heldScore - r.heldBase;
  return {
    headline: `${r.heldScore}%`,
    sub: `held back · ${lift >= 0 ? "+" : ""}${lift} from ${r.heldBase}%`,
    tone: lift > 0 ? "#16A34A" : lift < 0 ? "#DC2626" : "text.secondary",
  };
};

/** What a run is worth acting on, stated once so every surface agrees. */
export const optimizationVerdict = (record) => {
  const r = record?.result;
  if (!r) return null;
  if (r.hollow?.length) {
    return {
      tone: "#DC2626",
      title: `${r.hollow.length} of the winner's fixes have no tool evidence`,
      body: "The search found wording that passes the checks on scenarios that failed for a missing tool call. That is the self improver doing its job against a check that cannot tell the difference — fix the check before shipping this prompt.",
    };
  }
  /* The run that scored highest on the targets but isn't the winner. Say why
     from what actually happened to it — and not at all once someone chose it
     (the "You chose Run N" line says it then). */
  if (r.overruled && record.preferredTrial !== r.overruled.n) {
    const n = r.overruled.n;
    const finalist = (r.finalists || []).find((f) => f.n === n);
    if (r.overruled.brokeBlocker) {
      return {
        tone: "#CA8A04",
        title: `Run ${n} scored higher and was rejected`,
        body: "It broke a must-pass scenario. The winner below is the best trial that did not.",
      };
    }
    if (!finalist) {
      return {
        tone: "#CA8A04",
        title: `Run ${n} scored higher on the targets but wasn't a finalist`,
        body:
          "Only finalists are checked on the held-back set, so it was never proven. The winner below is the best finalist that passed — choose Run " +
          n +
          " in Trade-offs to verify it instead.",
      };
    }
    return {
      tone: "#CA8A04",
      title: `Run ${n} scored higher and was rejected`,
      body: `${finalist.why ? `Rejected: ${finalist.why}.` : "It didn't pass the held-back checks."} The winner below is the best trial that passed.`,
    };
  }
  /*
    A held-out set with nothing failing in it cannot show an improvement.

    It reported 96% "from 100%" and the screen called that no improvement, as
    though the search had failed to lift something liftable. It had not: every
    held-out scenario already passed, so the only thing that set could ever
    report was a regression — and here it reported one, which is the more
    serious finding and was being read as the milder one.
  */
  /* Too few reproduced to hold any back: there is no held-back number to
     read a gap, a gain or a regression from — the issue line says so. */
  if (r.split?.thin) return null;
  /* Passed the held-back set when it was picked, then not on the run that proves it. */
  if (r.confirm && !r.confirm.held) {
    const n = r.winner?.n;
    return {
      tone: "#DC2626",
      title: `Run ${n} didn't hold on a re-run`,
      body: `It scored ${r.confirm.selectionScore}% on the held-back set when it was picked and ${r.confirm.score}% when that set ran again: ${r.confirm.why}. A result that holds once can be luck — the pick is never its own proof.`,
    };
  }
  if (r.heldBase >= 100) {
    const broke = (r.heldTasks || []).filter(
      (t) => r.heldPerScenario?.[t.id] === "broke",
    );
    return r.heldScore < r.heldBase
      ? {
          tone: "#DC2626",
          title:
            broke.length === 1
              ? `The winner broke “${broke[0].title}”`
              : `The winner broke ${broke.length || 1} held-back scenarios`,
          body: "Every held-back scenario passed before this change and that one does not now. The held-back set was too easy to show a gain, so a regression was the only thing it could ever report — and it reported one.",
        }
      : {
          tone: "#CA8A04",
          title: "The held-back set could not test this",
          body: "Every held-back scenario already passed, so there was nothing there for the winner to improve. The held-back number is evidence of no regression, not evidence of a gain — add harder scenarios before reading a lift into it.",
        };
  }
  if (r.heldScore < r.heldBase) {
    return {
      tone: "#DC2626",
      title: `Held-back rate fell ${r.heldBase - r.heldScore} points`,
      body: "The winner scored better on the scenarios it was tuned against and worse on the ones it was not. That is the definition of a prompt fitted to its training split.",
    };
  }
  if (r.gap > 12) {
    return {
      tone: "#CA8A04",
      title: `${r.gap} points between training and held back`,
      body: "The prompt learned the scenarios it was tuned on more than it learned the task. Worth more scenarios before trusting the lift.",
    };
  }
  if (r.heldScore > r.heldBase) {
    return {
      tone: "#16A34A",
      title: `Held-back rate improved by ${r.heldScore - r.heldBase} points`,
      body: "The winner does better on scenarios the search never saw, which is the only evidence that it learned the task rather than the split.",
    };
  }
  return {
    tone: "#DC2626",
    title: "No improvement on held-back scenarios",
    body: "Whatever the training split gained did not survive contact with scenarios the search had not seen. The reasoned changes are the better route here.",
  };
};

export { OPTIMIZERS, optimizerById };

/* ── limits, spend and the outcome ─────────────────────────────────────── */

/**
 * What an improvement may change and what it must not move, until the agent
 * sets its own. The safe defaults: prompts and tool descriptions only, the
 * model untouched, cost and latency held close to where they are, and no
 * critical scenario broken.
 */
export const DEFAULT_LIMITS = {
  model: { mode: "locked", approved: [] },
  /* No increase by default (the updated RSI PRD). */
  costCeiling: 0,
  latencyCeiling: 10,
  allowed: ["prompt", "tools"],
  protected: [],
  budget: { dollars: 250, minutes: 150 },
};

/* The cost ceiling the seeded improvements were once rewritten to — read
   back to the agent's own "No increase" when the store loads. */
export const LEGACY_COST_CEILING = 5;
/* "No increase" reads as no increase beyond what two runs of the same agent
   already differ by. */
export const COST_NOISE = 2;

/*
  What an improvement may change, deepest last — a deeper change needs
  stronger proof and stricter review, and code needs the agent's code.
*/
export const SURFACES = [
  {
    id: "prompt",
    label: "Prompts",
    example: "Rewrite the refund instructions in the system prompt",
    delivered: "New prompt version, or a PR when prompts live in code",
  },
  {
    id: "tools",
    label: "Tool descriptions",
    example: "Clarify when a tool applies, or add a missing parameter",
    delivered: "New agent version or PR",
  },
  {
    id: "settings",
    label: "Agent settings",
    example: "Temperature, turn-taking or retrieval settings",
    delivered: "New agent version",
  },
  {
    id: "skills",
    label: "Skills",
    example: "Sharpen when the refund skill is used, or its instructions",
    delivered: "New skill version on a new agent version",
  },
  {
    id: "memory",
    label: "Memory",
    example: "What is kept between conversations and when it's forgotten",
    delivered: "New agent version",
  },
  {
    id: "code",
    label: "Code",
    example: "Add a check before booking, fix a parser, change routing",
    delivered: "GitHub PR",
  },
  {
    id: "structure",
    label: "Structure",
    example: "Split into a router and specialists, add a retrieval step",
    delivered: "GitHub PR with a written rationale",
  },
];

const SURFACE_LABEL = {
  prompt: "prompts",
  tools: "tool descriptions",
  settings: "agent settings",
  skills: "skills",
  memory: "memory",
  code: "code",
  structure: "structure",
};
/* The same surfaces as `agent-learn improve create --allow` names them. */
const SURFACE_CLI = {
  prompt: "prompt",
  tools: "tools",
  settings: "config",
  skills: "skill",
  memory: "memory",
  code: "code",
  structure: "orchestration",
};
export const allowFlagOf = (allowed = DEFAULT_LIMITS.allowed) => {
  const order = SURFACES.map((x) => x.id);
  return (
    [...allowed]
      .filter((a) => SURFACE_CLI[a])
      .sort((a, b) => order.indexOf(a) - order.indexOf(b))
      .map((a) => SURFACE_CLI[a])
      .join(",") || "prompt"
  );
};
export const allowedLabel = (allowed = DEFAULT_LIMITS.allowed) => {
  const order = SURFACES.map((x) => x.id);
  /* Only the kinds of change there are — an older stored choice that names
     another is left out. */
  const words = [...allowed]
    .filter((a) => SURFACE_LABEL[a])
    .sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map((a) => SURFACE_LABEL[a]);
  const text =
    words.length > 1
      ? `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`
      : words[0] || "nothing";
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** The limits as rows, for a sheet that shows them before a run starts. */
export const modelLimitLabel = (limits, model) =>
  limits?.model?.mode === "approved" && limits.model.approved?.length
    ? `${[model, ...limits.model.approved.filter((m) => m !== model)].filter(Boolean).join(", ")} only`
    : model
      ? `Unchanged · ${model}`
      : "Unchanged";
export const ceilingLabel = (n) => (n <= 0 ? "No increase" : `At most +${n}%`);

/* A cost ceiling is a percentage over today, or an amount per conversation. */
export const costCeilingLabel = (limits) =>
  limits?.costMode === "abs" && limits.costCeilingAbs != null
    ? `At most $${Number(limits.costCeilingAbs).toFixed(2)} per conversation`
    : ceilingLabel(limits?.costCeiling ?? 5);

/* A protected part as a person reads it: a file, a tool, or a prompt section. */
export const protectedLabel = (x) =>
  x.startsWith("tool:")
    ? `tool ${x.slice(5)}`
    : x.startsWith("section:")
      ? `${x.slice(8).split("#")[0].replace(/^.*\//, "")} › ${x.split("#")[1]}`
      : x;

/*
  `mustPass` — how many must-pass scenarios the improvement holds, across
  every environment it checks — joins the rows when the caller knows it.
*/
export const limitRows = (
  limits = DEFAULT_LIMITS,
  model,
  { mustPass = null } = {},
) => [
  { id: "model", label: "Model", value: modelLimitLabel(limits, model) },
  {
    id: "cost",
    label: "Cost per conversation",
    value: costCeilingLabel(limits),
  },
  {
    id: "latency",
    label: "Latency",
    value: ceilingLabel(limits.latencyCeiling),
  },
  {
    id: "changes",
    label: "Allowed changes",
    value: `${allowedLabel(limits.allowed)} only`,
  },
  ...((limits.protected || []).length
    ? [
        {
          id: "protected",
          label: "Never edited",
          value: limits.protected.map(protectedLabel).join(", "),
        },
      ]
    : []),
  ...(mustPass != null
    ? [
        {
          id: "mustPass",
          label: "Must-pass",
          value: mustPass
            ? `${mustPass} ${mustPass === 1 ? "scenario" : "scenarios"}. A case that passed every time before must not drop beyond normal noise. A case that fails every run is run again to confirm before it counts.`
            : "none yet",
        },
      ]
    : []),
  ...(limits.budget
    ? [
        {
          id: "budget",
          label: "Run budget",
          value: creditsLabel(limits.budget.dollars),
        },
      ]
    : []),
  ...(limits.budget?.minutes
    ? [
        {
          id: "time",
          label: "Time limit",
          value:
            limits.budget.minutes < 60
              ? `${limits.budget.minutes} min`
              : `${Math.floor(limits.budget.minutes / 60)} h${limits.budget.minutes % 60 ? ` ${limits.budget.minutes % 60} min` : ""}`,
        },
      ]
    : []),
];

/* What one simulated conversation costs — at the account's rate plan, the
   same rates a run plan prices with — and how often each is repeated so a
   lucky run can't win. */
export const PER_CONVERSATION = PER_CONVERSATION_USD;
export const REPEATS = 3;

/*
  How many finalists were (or will be) scored on the held-back set. Before a
  search has run, the default; a search with no eligible candidate scores none.
*/
const finalistCount = (result) =>
  Array.isArray(result?.finalists)
    ? result.finalists.length
    : result?.winner
      ? 1
      : 3;

/*
  Phase 2 · voice in text: a voice agent's trials can run as text
  conversations — faster and cheaper — and only finalists run as real calls.
*/
const trialKind = (result, surface) =>
  surface === "voice" && !result?.textSearch ? "voice" : "chat";
const finalKind = (surface) => (surface === "voice" ? "voice" : "chat");
/* Conversations a minute, by kind. */
export const RATE = { voice: 40, chat: 120 };

/* Repeats per scenario — the record's own (new improvements), else 3. */
export const repeatsOf = (x) =>
  x?.result?.repeats || x?.frozen?.repeats || x?.repeats || REPEATS;

/*
  The baseline is today's agent measured twice; a run of the same version on
  the unchanged environment (calibration.reused) stands in for one of them.
*/
const coveredOf = (cal) =>
  (cal?.reused || []).reduce((n, r) => n + (r.covered ?? cal.scenarios), 0);
const baselineConversations = (cal, reps) =>
  cal ? Math.max(0, 2 * cal.scenarios - coveredOf(cal)) * reps : 0;
const baselineDetail = (cal, stopped = "") => {
  const reused = cal?.reused || [];
  const n = cal?.scenarios || 0;
  const labels = reused.map((r) => r.label).join(" and ");
  const said = !reused.length
    ? "today's agent measured twice"
    : coveredOf(cal) >= 2 * n
      ? `${labels} reused — nothing new to run`
      : reused.length === 1 && reused[0].covered >= n
        ? `today's agent measured once more — ${labels} counts as the first`
        : `today's agent measured twice, less what ${labels} already ran (${reused.map((r) => r.covered).join(" + ")} of ${n} scenarios)`;
  return `${said}${stopped}`;
};

/*
  What a new improvement runs beyond the trials and the finalists' held-back
  check: the baseline twice, each finalist on the must-pass set, two more
  repeats for a code finalist, and each finalist once in every other
  environment picked. Zero for older records.
*/
const extraFinalConversations = (result) => {
  const reps = result.repeats || REPEATS;
  const calib = baselineConversations(result.calibration, reps);
  const mustPass =
    (result.mustPassMeasured || 0) * finalistCount(result) * reps;
  const deeper = (result.finalists || []).reduce(
    (n, f) =>
      n +
      (f.repeats && f.repeats > reps
        ? (f.repeats - reps) * result.heldMeasured
        : 0),
    0,
  );
  const envs = (result.otherEnvs || []).reduce(
    (n, e) =>
      n + finalistCount(result) * (e.scenarios || e.mustPass || 0) * reps,
    0,
  );
  /* An issue's fix: the winner once on today's other failures. */
  const others = (result.otherFailing || []).length * reps;
  return calib + mustPass + deeper + envs + others;
};

/** Every conversation a search runs — `trials` prices another run count (an estimate's maximum). */
export const searchConversations = (result, { trials } = {}) => {
  if (!result) return 0;
  const reps = result.repeats || REPEATS;
  return (
    ((trials ?? result.trials.length) * result.trainMeasured +
      finalistCount(result) * result.heldMeasured) *
      reps +
    extraFinalConversations(result)
  );
};

/** What a search spends: every trial runs the target scenarios, each finalist the held-back and must-pass sets. */
export const searchSpend = (result, surface, { trials } = {}) => {
  if (!result) return 0;
  const reps = result.repeats || REPEATS;
  return (
    (trials ?? result.trials.length) *
      result.trainMeasured *
      reps *
      PER_CONVERSATION[trialKind(result, surface)] +
    finalistCount(result) *
      result.heldMeasured *
      reps *
      PER_CONVERSATION[finalKind(surface)] +
    extraFinalConversations(result) * PER_CONVERSATION[finalKind(surface)]
  );
};

/**
 * The same spend as searchSpend, split into the parts a cost breakdown names:
 * candidates written and tested, the held-out check, the baseline, must-pass,
 * extra repeats and other environments. Parts sum to searchSpend.
 *   → [{ id, label, detail, conversations, usd }]
 */
export const searchSpendParts = (result, surface, { trials } = {}) => {
  if (!result) return [];
  const reps = result.repeats || REPEATS;
  const t = trials ?? result.trials.length;
  const perTrial = PER_CONVERSATION[trialKind(result, surface)];
  const perFinal = PER_CONVERSATION[finalKind(surface)];
  const finalists = finalistCount(result);
  const part = (id, label, detail, conversations, per) => ({
    id,
    label,
    detail,
    conversations,
    usd: conversations * per,
  });
  return [
    part(
      "candidates",
      "Candidates",
      `${t} written and tested on the target scenarios`,
      t * result.trainMeasured * reps,
      perTrial,
    ),
    part(
      "heldOut",
      "Held-out check",
      `${finalists} finalist${finalists === 1 ? "" : "s"} on scenarios they never saw`,
      finalists * result.heldMeasured * reps,
      perFinal,
    ),
    part(
      "baseline",
      "Baseline",
      baselineDetail(result.calibration),
      baselineConversations(result.calibration, reps),
      perFinal,
    ),
    part(
      "mustPass",
      "Must-pass",
      "finalists on what must keep passing",
      (result.mustPassMeasured || 0) * finalists * reps,
      perFinal,
    ),
    part(
      "deeper",
      "Extra repeats",
      "a code finalist checked more times",
      (result.finalists || []).reduce(
        (n, f) =>
          n +
          (f.repeats && f.repeats > reps
            ? (f.repeats - reps) * result.heldMeasured
            : 0),
        0,
      ),
      perFinal,
    ),
    part(
      "otherFailing",
      "Other failures",
      "the winner re-checked on what else failed today",
      (result.otherFailing || []).length * reps,
      perFinal,
    ),
    part(
      "otherEnvs",
      "Other environments",
      "finalists checked for regressions",
      (result.otherEnvs || []).reduce(
        (n, e) => n + finalists * (e.scenarios || e.mustPass || 0) * reps,
        0,
      ),
      perFinal,
    ),
  ].filter(
    (p) =>
      p.conversations > 0 ||
      (p.id === "baseline" && result.calibration?.reused?.length),
  );
};

/* What a stopped improvement spent past its first run: the baseline once it
   got that far, and the trials that finished. */
const stoppedSpendParts = (record, surface) => {
  const r = record?.result;
  const s = record?.stoppedBy || {};
  if (!r) return [];
  const reps = r.repeats || REPEATS;
  const out = [];
  const base = s.measured ? baselineConversations(r.calibration, reps) : 0;
  if (base)
    out.push({
      id: "baseline",
      label: "Baseline",
      detail: baselineDetail(r.calibration, " · stopped by you"),
      conversations: base,
      usd: base * PER_CONVERSATION[finalKind(surface)],
    });
  const tried = (s.trials || 0) * r.trainMeasured * reps;
  if (tried)
    out.push({
      id: "candidates",
      label: "Candidates",
      detail: `${s.trials} tried on the target scenarios before it was stopped`,
      conversations: tried,
      usd: tried * PER_CONVERSATION[trialKind(r, surface)],
    });
  return out;
};

/**
 * What an improvement actually spent, by part — the same total as
 * improvementTotalSpend: reproducing first, then the search's parts (or
 * just the baseline when it stopped before searching), then any coding
 * agent's attempts.
 */
export const improvementSpendParts = (
  record,
  surface,
  { attempts = [] } = {},
) => {
  if (!record) return [];
  const kind = surface || record.surface;
  const per = PER_CONVERSATION[finalKind(kind)];
  const reps = repeatsOf(record);
  const firstConv = record.reproduce?.total ? record.reproduce.total * reps : 0;
  const first = record.spentFirst ?? firstConv * per;
  const parts = [];
  if (first)
    parts.push({
      id: "reproduce",
      label: "First run",
      /* What it spent covers the whole first run — the environment's
         scenarios and, for a fix, the issue's — so the count follows it. */
      detail:
        record.trigger?.kind === "error-feed"
          ? "today's agent on the environment's scenarios and the issue's"
          : "today's agent, measured before anything changed",
      conversations:
        record.spentFirst != null ? Math.round(first / per) : firstConv,
      usd: first,
    });
  const status = statusOf(record);
  if (
    [
      OPT_STATUS.NOT_REPRODUCED,
      OPT_STATUS.NOTHING,
      OPT_STATUS.OVER_BUDGET,
    ].includes(status)
  ) {
    const p = record.pendingResult;
    if (p?.calibration) {
      const n = baselineConversations(p.calibration, p.repeats || REPEATS);
      parts.push({
        id: "baseline",
        label: "Baseline",
        detail: baselineDetail(p.calibration, " · stopped before searching"),
        conversations: n,
        usd: n * per,
      });
    }
    return parts;
  }
  if (status === OPT_STATUS.FAILED) return parts;
  if (status === OPT_STATUS.STOPPED)
    return [...parts, ...stoppedSpendParts(record, kind)];
  const r = record.result;
  if (record.fixDepth === "suggestion" && r) {
    const conv = (r.trainMeasured + r.heldMeasured) * (r.repeats || REPEATS);
    parts.push({
      id: "measure",
      label: "Measuring",
      detail: "a suggestion only — no candidates run",
      conversations: conv,
      usd: conv * per,
    });
  } else parts.push(...searchSpendParts(r, kind));
  const attemptUsd = attempts.reduce((n, a) => n + (a.spend || 0), 0);
  if (attemptUsd)
    parts.push({
      id: "attempts",
      label: "Coding agent attempts",
      detail: `${attempts.length} submitted`,
      conversations: null,
      usd: attemptUsd,
    });
  return parts;
};

/**
 * What an improvement actually spent. A Suggestion-only improvement runs no
 * candidates — only the reproduction on the agent as it runs today.
 */
export const improvementSpend = (record, surface) => {
  const r = record?.result;
  if (!r) return 0;
  if (record.fixDepth === "suggestion")
    return (
      (r.trainMeasured + r.heldMeasured) *
      (r.repeats || REPEATS) *
      PER_CONVERSATION[finalKind(surface)]
    );
  return searchSpend(r, surface);
};

/*
  Everything an improvement spent, said once for the list, the page and the
  agent's numbers: what it ran first (reproducing an issue), the search, and
  each attempt a coding agent submitted. One that stopped before searching
  still spent what measuring it cost; an infrastructure failure only what
  ran before it.
*/
export const improvementTotalSpend = (
  record,
  surface,
  { attempts = [] } = {},
) => {
  if (!record) return 0;
  const per = PER_CONVERSATION[finalKind(surface || record.surface)];
  const first =
    record.spentFirst ??
    (record.reproduce?.total
      ? record.reproduce.total * repeatsOf(record) * per
      : 0);
  const status = statusOf(record);
  const stoppedFirst = [
    OPT_STATUS.NOT_REPRODUCED,
    OPT_STATUS.NOTHING,
    OPT_STATUS.OVER_BUDGET,
  ].includes(status);
  if (stoppedFirst) {
    const p = record.pendingResult;
    const measure =
      baselineConversations(p?.calibration, p?.repeats || REPEATS) * per;
    return first + measure;
  }
  if (status === OPT_STATUS.STOPPED)
    return (
      first +
      stoppedSpendParts(record, surface || record.surface).reduce(
        (n, p) => n + p.usd,
        0,
      )
    );
  if (status === OPT_STATUS.FAILED) return first;
  return (
    first +
    improvementSpend(record, surface || record.surface) +
    attempts.reduce((n, a) => n + (a.spend || 0), 0)
  );
};

/*
  How many trials a budget pays for, after the finalists' checks. Zero when
  checking the finalists alone would cost more than the budget — the
  improvement then stops at the budget before searching.
*/
export const trialsWithin = (result, budget, surface) => {
  const reps = result.repeats || REPEATS;
  const perTrial =
    result.trainMeasured * reps * PER_CONVERSATION[trialKind(result, surface)];
  const verify =
    (finalistCount(result) * result.heldMeasured * reps +
      extraFinalConversations(result)) *
    PER_CONVERSATION[finalKind(surface)];
  return perTrial > 0
    ? Math.max(0, Math.floor((budget - verify) / perTrial))
    : result.trials.length;
};

/**
 * How long a search takes, in minutes — trials at their pace, finalists at
 * theirs, plus anything run first. `allCalls` prices a voice search as if
 * every trial were a real call, to show both ways.
 */
export const searchMinutes = (
  result,
  surface,
  { trials, firstConversations = 0, allCalls = false } = {},
) => {
  if (!result) return 0;
  const n = trials ?? result.trials.length;
  const kind = allCalls ? finalKind(surface) : trialKind(result, surface);
  const reps = result.repeats || REPEATS;
  return Math.round(
    8 +
      (n * result.trainMeasured * reps) / RATE[kind] +
      (finalistCount(result) * result.heldMeasured * reps +
        extraFinalConversations(result) +
        firstConversations) /
        RATE[finalKind(surface)],
  );
};

/* What each kind of change does to cost and latency per conversation — a
   longer prompt costs a little, an added step costs more. Percent. */
const KIND_EFFECT = {
  "System prompt": { cost: 1, latency: 1 },
  "Tool description": { cost: 0.5, latency: 0 },
  Memory: { cost: 2, latency: -1 },
  Architecture: { cost: 4, latency: 6 },
};
/* What switching to each model does to cost and latency, against a
   mid-priced default. */
const MODEL_EFFECT = {
  "gpt-4o": { cost: 0, latency: 0 },
  "gpt-4.1": { cost: -6, latency: -4 },
  "claude-sonnet-4": { cost: 4, latency: 3 },
  "gemini-2.5-pro": { cost: 3, latency: 8 },
};
/* Cost and latency grow with what a change adds: every added line is read
   on every turn. To a tenth of a percent, so candidates can be told apart. */
const round1 = (n) => Math.round(n * 10) / 10;
export const winnerEffect = (winner) => {
  const sum = (winner?.proposals || []).reduce(
    (a, p) => {
      const e =
        p.kind === "Model"
          ? MODEL_EFFECT[p.model] || { cost: 2, latency: 2 }
          : KIND_EFFECT[p.kind] || { cost: 1, latency: 1 };
      const added = (p.diff || [])
        .filter((d) => d.type === "add")
        .reduce((n, d) => n + (d.text || "").length, 0);
      const weight = p.kind === "Model" ? 0 : added / 160;
      return {
        cost: a.cost + e.cost * 0.6 + weight * 0.5,
        latency: a.latency + e.latency * 0.6 + weight * 0.3,
      };
    },
    { cost: 0, latency: 0 },
  );
  /* Shorter conversations once failures are fixed (newer records carry it). */
  return {
    cost: round1(sum.cost - (winner?.costSavings || 0)),
    latency: round1(sum.latency),
  };
};

const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n)}%`;

/*
  The cost and latency ceilings as one gate — the search drops a candidate
  over either as it runs, and every screen that picks among candidates uses
  the same one. "No increase" allows the ±2% two runs of one agent differ
  by; a fixed amount is checked against today's cost per conversation; cost
  only where it can be measured.
*/
export const limitGate = ({ limits, costKnown = true, surface }) => {
  const ceiling = limits?.costCeiling ?? 0;
  const tol = ceiling <= 0 ? COST_NOISE : 0;
  const latency = limits?.latencyCeiling ?? 10;
  const abs =
    limits?.costMode === "abs" && limits.costCeilingAbs != null
      ? limits.costCeilingAbs
      : null;
  const today = PER_CONVERSATION[surface === "voice" ? "voice" : "chat"];
  return (proposals, costSavings = 0) => {
    const e = winnerEffect({ proposals, costSavings });
    if (costKnown && abs != null && today * (1 + e.cost / 100) > abs)
      return {
        id: "cost",
        actual: `$${(today * (1 + e.cost / 100)).toFixed(3)} a conversation, over $${Number(abs).toFixed(2)}`,
      };
    if (costKnown && abs == null && e.cost > ceiling + tol)
      return {
        id: "cost",
        actual: `cost ${signed(e.cost)}, over ${ceiling <= 0 ? "no increase" : `+${ceiling}%`}`,
      };
    if (e.latency > latency)
      return {
        id: "latency",
        actual: `latency ${signed(e.latency)}, over +${latency}%`,
      };
    return null;
  };
};
/** The same gate for a recorded improvement. */
export const limitGateOf = (record) =>
  limitGate({
    limits: record?.limits || DEFAULT_LIMITS,
    costKnown: record?.costMeasured !== false,
    surface: record?.surface,
  });

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Everything an improvement run concluded, said once so the page, the Error
 * Feed chip and the lists agree. Seven endings: Ready for review, Stopped
 * early (no gain in the last two runs — the best was verified), No safe
 * improvement, Could not reproduce, Stopped at budget, Failed for
 * infrastructure reasons, and Suggestion only — for agents we can only call.
 */
/** Started more than a day ago and still not finished — timed out. */
export const STALE_AFTER_MS = 24 * 60 * 60 * 1000;
export const staleSince = (rec, now = Date.now()) => {
  const at = Date.parse(rec?.startedAt || rec?.createdAt || "");
  return Number.isFinite(at) && now - at > STALE_AFTER_MS;
};

/*
  Cost a candidate saves by fixing failing conversations: a failing
  conversation runs long (retries, loops back), so fixing every failing one
  pays for some of the tokens a change adds. Newer records carry it; for
  older ones it's read off what the candidate fixed, the same way.
*/
const COST_SAVED_IF_ALL_FIXED = 6;
export const costSavingsOf = (trial) => {
  if (!trial) return 0;
  if (trial.costSavings != null) return trial.costSavings;
  const states = Object.values(trial.perScenario || {});
  const failingBefore = states.filter(
    (v) => v !== "same" && v !== "broke",
  ).length;
  const fixed = states.filter((v) => v === "fixed").length;
  return failingBefore
    ? Math.round(((COST_SAVED_IF_ALL_FIXED * fixed) / failingBefore) * 10) / 10
    : 0;
};
/** A candidate's cost and latency against today, savings included. */
export const candidateEffect = (trial, proposals = trial?.proposals || []) =>
  winnerEffect({ proposals, costSavings: costSavingsOf(trial) });

/*
  The run someone chose over the search's pick (Trade-offs, Choose winner),
  with its changes — null when the search's own pick stands.
*/
export const chosenTrialOf = (record) => {
  const r = record?.result;
  const n = record?.preferredTrial;
  if (!r?.winner || !n || n === r.winner.n) return null;
  const t = (r.trials || []).find((x) => x.n === n);
  return t
    ? { ...t, proposals: (r.pool || []).filter((p) => t.picks.includes(p.id)) }
    : null;
};
/* Only a passing verdict marks a change verified: a chosen run is verified
   once it has run on the full suite (`verifiedTrial`). The search's pick was
   verified by the search. */
export const chosenVerified = (record) =>
  !chosenTrialOf(record) || record.verifiedTrial === record.preferredTrial;

/**
 * The held-back rate a chosen candidate would likely reach. A finalist was
 * scored there once, at the end — that number stands. Anyone else's is scaled
 * from what the winner did there by how much of its target lift it keeps,
 * shown as a projection.
 */
export const projectedHeld = (result, candidate) => {
  const w = result?.winner;
  if (!w || !candidate) return null;
  if (candidate.n === w.n) return result.heldScore;
  const finalist = (result.finalists || []).find((f) => f.n === candidate.n);
  if (finalist) return finalist.heldScore;
  const lift = result.heldScore - result.heldBase;
  const share =
    w.delta > 0 ? Math.max(0, Math.min(1.2, candidate.delta / w.delta)) : 1;
  return Math.round(result.heldBase + lift * share);
};

/* A verdict of no safe improvement has nothing to rate. */
export const improvementOutcome = (rec, opts) => {
  const out = outcomeOf(rec, opts);
  return out?.strength && out.ending?.key === "no-fix"
    ? { ...out, strength: null }
    : out;
};
const outcomeOf = (rec, { model } = {}) => {
  /* An ending still playing its Reproducing phase reads as running; once
     that phase has passed it reads as its ending, saved or not yet. */
  const settled = statusOf(rec);
  const record =
    rec && settled !== rec.status ? { ...rec, status: settled } : rec;
  const r = record?.result;
  const limits = record?.limits || DEFAULT_LIMITS;
  /* Fixed by the customer's coding agent and judged by us: there's no search
     of ours to read an ending from — its state is the agent's session
     (improverAttempts' coderStatusOf), which every surface asks instead. */
  if (record?.improver) {
    return {
      ending: { key: "coder", label: "With your coding agent" },
      headline: "",
      coder: true,
      held: null,
      effect: { cost: 0, latency: 0 },
      costKnown: false,
      checks: [],
      issue: null,
      train: null,
      gap: null,
      heldScore: null,
      finalists: [],
    };
  }
  if (record?.status === OPT_STATUS.NOTHING) {
    return {
      ending: { key: "nothing", label: "Nothing to improve" },
      headline: `Nothing to improve — every scenario already meets ${goalWords(record.nothing?.goal)}.`,
    };
  }
  if (record?.status === OPT_STATUS.NOT_REPRODUCED) {
    return {
      ending: { key: "not-reproduced", label: "Could not reproduce" },
      headline:
        "The failure didn't happen on today's agent, so there was nothing to fix.",
    };
  }
  if (record?.status === OPT_STATUS.OVER_BUDGET) {
    const s = record.stoppedAtBudget || {};
    return {
      ending: { key: "budget", label: "Stopped at budget" },
      headline:
        s.by === "time"
          ? `Stopped at budget — checking the finalists alone would take longer than the ${s.minutes}-minute time cap, so nothing was searched.`
          : `Stopped at budget — checking the finalists alone would cost more than the ${creditsLabel(s.budget || 0)} budget, so nothing was searched.`,
      beforeSearch: true,
    };
  }
  if (record?.status === OPT_STATUS.STOPPED) {
    const s = record.stoppedBy || {};
    return {
      ending: { key: "stopped", label: "Stopped" },
      headline: s.trials
        ? `Stopped after ${s.trials} of up to ${s.of} ${s.of === 1 ? "trial" : "trials"}. None was checked on the held-back scenarios, so nothing can be chosen — today's version stays. The trials that ran stay here.`
        : "Stopped before it tried a change. Today's version stays.",
      beforeSearch: !s.trials,
    };
  }
  if (record?.status === OPT_STATUS.FAILED) {
    return {
      ending: { key: "failed", label: "Failed for infrastructure reasons" },
      headline: `${record.infra?.reason || "The environment couldn't run the search."} An infrastructure problem, not the agent — no run was counted as a pass or a fail, and the crashed runs aren't charged to the budget.`,
    };
  }
  /* A run that's reported nothing for a day has stopped — the engine times
     it out as an infrastructure failure instead of showing it running. */
  if (record?.status === OPT_STATUS.RUNNING && staleSince(record)) {
    return {
      ending: { key: "failed", label: "Failed for infrastructure reasons" },
      headline:
        "It stopped reporting progress more than a day ago and was timed out. An infrastructure problem, not the agent — nothing it hadn't finished was charged. Start it again to retry.",
    };
  }
  if (record?.status === OPT_STATUS.RUNNING || !r)
    return { ending: { key: "running", label: "Running" } };
  /*
    Suggestion only: we can only call this agent, so the improvement
    reproduces the failure and ends with a diagnosis and a suggested fix —
    no candidate was tried, so there is no proof. Once the fix is applied and
    the new version released, a re-run on the same environment version proves it.
  */
  if (record?.fixDepth === "suggestion") {
    const suggestion = suggestedFixOf(record);
    const all = r.trainMeasured + r.heldMeasured;
    const reproduced = record.reproduce
      ? `Reproduced on ${record.reproduce.reproduced} of ${plural(record.reproduce.total, "scenario")} from ${record.trigger?.issueId || "the issue"}`
      : `Reproduced the failures on ${r.failingToday ?? "some"} of ${plural(all, "scenario")}`;
    return {
      ending: { key: "suggestion", label: "Suggestion only" },
      headline: record.suggestChosen
        ? `${reproduced}. A diagnosis and a suggested fix — not proven: you chose suggest only, so no change was tried.`
        : `${reproduced}. A diagnosis and a suggested fix — not proven: we can only call this agent, so no change could be tried.`,
      suggestion,
      checks: [],
      held: null,
      issue: issueOutcome({ ...record, result: { ...r, winner: null } }),
    };
  }

  const winner = r.winner;
  /*
    What ships: the search's pick, or the run someone chose instead. The
    limits are checked on what ships; its held-back figure is the finalist's
    own, or projected until it's verified.
  */
  const chosen = chosenTrialOf(record);
  const shipped = chosen || winner;
  const chosenFinalist = chosen
    ? (r.finalists || []).find((f) => f.n === chosen.n) || null
    : null;
  const verified = chosenVerified(record);
  const held = {
    fixed: 0,
    broke: 0,
    stillFailing: 0,
    unchanged: 0,
    flaky: 0,
    inconclusive: 0,
    offScript: 0,
  };
  Object.values(r.heldPerScenario || {}).forEach((v) => {
    if (v === "off-script") held.offScript += 1;
    else if (v === "fixed") held.fixed += 1;
    else if (v === "broke") held.broke += 1;
    else if (v === "still-failing") held.stillFailing += 1;
    else if (v === "flaky") held.flaky += 1;
    else if (v === "inconclusive") held.inconclusive += 1;
    else held.unchanged += 1;
  });
  /* Flaky and inconclusive ones failed before and aren't fixed now. */
  held.failingBefore =
    held.fixed + held.stillFailing + held.flaky + held.inconclusive;
  const effect = candidateEffect(shipped);
  const surfaces = [
    ...new Set(
      (shipped?.proposals || [])
        .filter((p) => p.kind !== "Model")
        .map((p) => KIND_SURFACE[p.kind] || "code"),
    ),
  ].sort(
    (a, b) =>
      ["prompt", "tools", "code"].indexOf(a) -
      ["prompt", "tools", "code"].indexOf(b),
  );
  const offLimits = surfaces.filter((x) => !limits.allowed.includes(x));
  /* Must-pass failures that repeated: the search's pick's own; a chosen
     finalist passed or it wasn't chosen; anyone else's aren't known until
     it's verified. */
  const heldBlockers = chosen ? [] : r.heldBlockers || [];
  const blockersBroken = heldBlockers.length;
  const mustPassUnknown = !!chosen && !chosenFinalist && !verified;

  const costKnown = record?.costMeasured !== false;
  const switched =
    (shipped?.proposals || []).find((p) => p.kind === "Model")?.model || null;
  const hitProtected = [
    ...new Set(
      (shipped?.proposals || [])
        .map((p) => protectedHit(p, limits.protected || []))
        .filter(Boolean),
    ),
  ];
  /* The newer engine's rows — only on records stamped with them. */
  const fz = record?.frozen || {};
  /* "No increase" allows the ±2% two runs of one agent already differ by. */
  const noIncrease = (limits.costCeiling ?? 0) <= 0;
  const tol = noIncrease ? COST_NOISE : 0;
  const costLabel = noIncrease
    ? "Cost per conversation · no increase"
    : `Cost per conversation within +${limits.costCeiling}%`;
  const areas = fz.areaCheck
    ? [
        ...new Set(
          (shipped?.proposals || [])
            .map((p) => p.area || areaOfProposal(p))
            .filter(Boolean),
        ),
      ]
    : [];
  const areaWord = (id) => CONFIG_AREAS.find((a) => a.id === id)?.label || id;
  const offAreas =
    fz.areaCheck && limits.areas
      ? areas.filter((a) => limits.areas[a] === false)
      : [];
  const shippedFinalist =
    (r?.finalists || []).find((f) => f.n === shipped?.n) || null;
  const envRuns = fz.multiEnv ? shippedFinalist?.envs || null : null;
  const deepWinner =
    fz.depthRule && shipped ? depthOf(shipped.proposals || []) >= 3 : false;
  const heldNow = chosen ? projectedHeld(r, chosen) : r.heldScore;
  const lift = r ? heldNow - r.heldBase : 0;
  const checks = shipped
    ? [
        ...(fz.staticCheck
          ? [
              {
                id: "static",
                label: "Static checks",
                actual:
                  "areas, protected parts, settings in range, judges untouched",
                pass: !shipped.static || shipped.static.pass !== false,
              },
            ]
          : []),
        {
          id: "model",
          label:
            limits.model?.mode === "approved"
              ? "Model on the approved list"
              : "Model unchanged",
          actual: switched
            ? `${model ? `${model} → ` : "switches to "}${switched}`
            : model || "unchanged",
          pass:
            !switched ||
            (limits.model?.mode === "approved" &&
              (limits.model.approved || []).includes(switched)),
        },
        costKnown && limits.costMode === "abs" && limits.costCeilingAbs != null
          ? (() => {
              /* An amount: today's cost per conversation, moved by the change. */
              const today =
                PER_CONVERSATION[record.surface === "voice" ? "voice" : "chat"];
              const after = today * (1 + effect.cost / 100);
              return {
                id: "cost",
                label: `Cost per conversation under $${Number(limits.costCeilingAbs).toFixed(2)}`,
                actual: `$${after.toFixed(3)} (${signed(effect.cost)})`,
                pass: after <= limits.costCeilingAbs,
              };
            })()
          : costKnown
            ? {
                id: "cost",
                label: costLabel,
                actual: `${signed(effect.cost)}${tol && effect.cost > 0 && effect.cost <= tol ? ` · within the ±${tol}% two runs differ by` : ""}`,
                pass: effect.cost <= (limits.costCeiling ?? 0) + tol,
              }
            : /* No cost data from this agent: unverified, never passed. */
              {
                id: "cost",
                label: costLabel,
                actual: "Unverified — this agent reports no cost",
                pass: null,
              },
        {
          id: "latency",
          label: `Latency within +${limits.latencyCeiling}%`,
          actual: signed(effect.latency),
          pass: effect.latency <= limits.latencyCeiling,
        },
        {
          id: "changes",
          label: `${allowedLabel(limits.allowed)} only`,
          actual: offLimits.length
            ? `also ${offLimits.map((x) => SURFACE_LABEL[x] || x).join(", ")}`
            : allowedLabel(surfaces).toLowerCase() || "no change",
          pass: !offLimits.length,
        },
        ...(fz.areaCheck && areas.length
          ? [
              {
                id: "areas",
                label: "Only areas this agent allows",
                actual: offAreas.length
                  ? `changes ${offAreas.map(areaWord).join(", ")}`
                  : areas.map(areaWord).join(", "),
                pass: !offAreas.length,
              },
            ]
          : []),
        ...((limits.protected || []).length
          ? [
              {
                id: "protected",
                label: "Protected parts untouched",
                actual: hitProtected.length
                  ? `edits ${hitProtected.join(", ")}`
                  : "untouched",
                pass: !hitProtected.length,
              },
            ]
          : []),
        ...(shipped.compat
          ? [
              {
                id: "compat",
                label: "Fits the sealed environment",
                actual: shipped.compat.pass
                  ? "entry point, build and start command unchanged"
                  : shipped.compat.reason,
                pass: !!shipped.compat.pass,
              },
            ]
          : []),
        ...(shipped.repoTests
          ? [
              {
                id: "repoTests",
                label: "The repository's own tests still pass",
                actual: `${shipped.repoTests.command} · ${shipped.repoTests.pass ? "passed" : "failed"}`,
                pass: !!shipped.repoTests.pass,
              },
            ]
          : []),
        {
          id: "blockers",
          label: "No must-pass failure repeated on a re-run",
          actual: mustPassUnknown
            ? "not run yet — it runs when you verify this pick"
            : blockersBroken
              ? `${plural(blockersBroken, "failed", "failed")} again`
              : `none of ${r.mustPass?.total ?? "the"} must-pass${(r.flakyToday || []).length ? ` · ${(r.flakyToday || []).length} flaky, not counted` : ""}${markedNotCountedText(r) ? ` · ${markedNotCountedText(r)}` : ""}`,
          pass: mustPassUnknown ? null : !blockersBroken,
        },
        /* A candidate never changes the rules it's graded against — a rule
       change is a spec change, decided by a person outside the loop. */
        {
          id: "rules",
          label: "No tested rule changed",
          actual: "rules untouched",
          pass: true,
        },
        ...(fz.calibration && r?.calibration
          ? [
              mustPassUnknown
                ? {
                    /* A projection isn't a measurement — it's checked once it's verified. */
                    id: "calibration",
                    label: "Gain clears run-to-run noise",
                    actual: `projected ${lift >= 0 ? "+" : "−"}${Math.abs(lift)} pts against ±${r.noise} — measured when you verify this pick`,
                    pass: null,
                  }
                : {
                    id: "calibration",
                    label: "Gain clears run-to-run noise",
                    actual: `${lift >= 0 ? "+" : "−"}${Math.abs(lift)} pts against ±${r.noise} · baseline run twice: ${r.calibration.disagreed} of ${r.calibration.scenarios} disagreed`,
                    /* A fix is judged on its issue's scenarios; a held-back set already
         passing can only show a regression. */
                    pass:
                      isIssueFix(record) ||
                      r.heldBase >= 100 ||
                      !!r.split?.thin ||
                      lift > (r.noise || 0),
                  },
            ]
          : []),
        ...(envRuns?.length
          ? [
              {
                id: "envs",
                label: "Must-pass held in every environment",
                actual: envRuns
                  .map((e) =>
                    e.held
                      ? `${e.name} · ${e.mustPass} held`
                      : `${e.name} · “${e.broke.title}” failed twice`,
                  )
                  .join(" · "),
                pass: envRuns.every((e) => e.held),
              },
            ]
          : []),
        ...(deepWinner
          ? [
              {
                id: "depth",
                label: "Deeper change, stronger proof",
                actual: `Code change → ${shippedFinalist?.repeats || repeatsOf(record) + 2} repeats per finalist and the full regression set${shipped.repoTests ? ", repository tests" : ""}`,
                pass: true,
              },
            ]
          : []),
      ]
    : [];
  const failedCheck = checks.find((c) => c.pass === false);

  const fixedLine = held.failingBefore
    ? `Fixed ${held.fixed} of ${held.failingBefore} failing ${held.failingBefore === 1 ? "scenario" : "scenarios"} on the held-back set`
    : "The held-back set was already passing";
  const costText = costKnown
    ? `cost per conversation ${signed(effect.cost)}`
    : "cost not measured";
  /* Nothing held back: say what was measured, not a held-back set that wasn't there. */
  const result = r.split?.thin
    ? `${shipped ? `Target ${r.base}% → ${shipped.score}%` : "No change kept"}, too few scenarios to hold any back, ${costText}`
    : `${fixedLine}, broke ${held.broke ? held.broke : "none"}, ${costText}`;

  const issue = issueOutcome(record);
  /*
    A fix is judged on its issue first. Held-back scenarios from the rest of
    the suite not moving is fine for a fix — they must not break, and the
    critical-scenario and regression checks still apply. Only a broken
    must-pass scenario or a held-back regression stops it.
  */
  const fixesIssue = !!issue?.fixed;
  const soft = optimizationVerdict(record);
  const blocking =
    blockingVerdict(r) ||
    (soft?.tone === "#DC2626" && !(fixesIssue && r.heldScore >= r.heldBase)
      ? soft
      : null);
  const base = {
    strength: chosen ? null : proofStrength(r, issue, held),
    held,
    /* An issue's fix: what else failed today, as the winner left it — fixed
       along the way, or still failing to fix separately. Read for the
       search's pick only; a chosen run wasn't re-checked on them. */
    alongTheWay:
      shipped && !chosen && (r.otherFailing || []).length
        ? {
            fixed: r.otherFailing.filter((f) => f.fixed),
            still: r.otherFailing.filter((f) => !f.fixed),
          }
        : null,
    effect,
    costKnown,
    checks,
    issue,
    noise: r.noise,
    stoppedEarly: r.stoppedEarly,
    shallower: r.shallower,
    train: shipped ? { from: r.base, to: shipped.score } : null,
    /* Target score minus held-back score — how much the change fitted the
       scenarios it was tuned on rather than the task. */
    gap: shipped ? shipped.score - heldNow : null,
    heldScore: heldNow,
    /* The run someone chose, and whether its numbers are measured yet. */
    chosen: chosen
      ? {
          n: chosen.n,
          held: heldNow,
          finalist: !!chosenFinalist,
          verified,
          projected: !verified && !chosenFinalist,
        }
      : null,
    /* Too few reproduced to hold any back. */
    thin: !!r.split?.thin,
    finalists: r.finalists || [],
  };
  if (!winner) {
    return {
      ...base,
      ending: { key: "no-fix", label: "No safe improvement" },
      headline: `No safe improvement — ${blockingVerdict(r).title.replace(/^No eligible winner — /, "")}.`,
    };
  }
  if (failedCheck) {
    /* Said as what the candidate would do, not as the rule it failed. */
    const why =
      {
        cost: `it would raise cost per conversation ${failedCheck.actual}, over the ${limits.costMode === "abs" ? `$${Number(limits.costCeilingAbs).toFixed(2)}` : noIncrease ? "no-increase" : `+${limits.costCeiling}%`} limit`,
        latency: `it would slow responses by ${failedCheck.actual}, over the +${limits.latencyCeiling}% limit`,
        changes: `it changes ${offLimits.map((x) => SURFACE_LABEL[x] || x).join(" and ")}, which this agent's constraints don't allow`,
        protected: `it edits ${hitProtected.join(", ")}, which this agent protects`,
        blockers:
          heldBlockers.length === 1
            ? `it failed the must-pass scenario “${heldBlockers[0].title.replace(/\.$/, "")}”, and again on a re-run`
            : `it failed ${blockersBroken} must-pass scenarios, each again on a re-run`,
        repoTests: `it breaks the repository's own tests (${shipped.repoTests?.command})`,
        compat:
          "it doesn't fit the sealed environment — rebuild the environment from its commit to test it",
        areas: `it ${failedCheck.actual}, which this agent's constraints don't allow`,
        calibration: `gained ${lift >= 0 ? "+" : "−"}${Math.abs(lift)} pts on the held-back set, within run-to-run noise (±${r.noise})`,
        envs: `failed a must-pass scenario in ${(envRuns || [])
          .filter((e) => !e.held)
          .map((e) => e.name)
          .join(" and ")}, and again on a re-run`,
      }[failedCheck.id] || failedCheck.label.toLowerCase();
    return {
      ...base,
      ending: { key: "no-fix", label: "No safe improvement" },
      headline: `No safe improvement — the ${chosen ? "trial you chose" : "best trial"} ${why.replace(/^it /, "")}.`,
    };
  }
  if (blocking && !chosen) {
    return {
      ...base,
      ending: { key: "no-fix", label: "No safe improvement" },
      headline: `No safe improvement — ${blocking.title.charAt(0).toLowerCase()}${blocking.title.slice(1)}.`,
    };
  }
  /* A fix that improved the suite but not the issue it was started for isn't
     a fix for that issue — said plainly rather than shipped under its name. */
  if (issue && issue.targets && !issue.fixed) {
    return {
      ...base,
      ending: { key: "no-fix", label: "No safe improvement" },
      headline: `No fix for ${issue.id} — the best trial didn't fix it on any of the ${plural(issue.targets, "scenario")} where it failed.`,
    };
  }
  /* Fixed only where the search could see it: fitted, not fixed. */
  if (issue && issue.held && !issue.heldFixed) {
    return {
      ...base,
      ending: { key: "no-fix", label: "No safe improvement" },
      headline: `No fix for ${issue.id} — the best trial fixed it on scenarios the search saw, but not on the ${issue.held === 1 ? "one" : issue.held} it never saw.`,
    };
  }
  /*
    Beyond run-to-run noise. For a goal or a run's failures, the held-back
    gain has to clear what two runs of the same agent already differ by. A
    fix is judged on its issue's scenarios instead — each fixed on every
    repeat, which noise doesn't do.
  */
  if (
    !issue &&
    !chosen &&
    r.heldBase < 100 &&
    r.heldScore - r.heldBase <= (r.noise || 0)
  ) {
    return {
      ...base,
      ending: { key: "no-fix", label: "No safe improvement" },
      headline: `No safe improvement — the held-back gain (${r.heldScore - r.heldBase >= 0 ? "+" : "−"}${Math.abs(r.heldScore - r.heldBase)} pts) is within run-to-run noise (±${r.noise}).`,
    };
  }
  /* A fix leads with its issue; the rest of the held-back set is the next line. */
  const searchSaid = issue?.targets
    ? /* Its failing scenarios include held-back ones the search never saw —
       said, so the count doesn't read against Reproduce's. */
      `Fixed ${issue.id} on ${issue.fixed} of ${issue.targets} failing ${issue.targets === 1 ? "scenario" : "scenarios"}${issue.held ? ` (${issue.held} never seen by the search)` : ""}, broke ${held.broke ? held.broke : "none"}, ${costText}`
    : result;
  /* A chosen run is read on its own numbers — projected until it's verified. */
  const said = chosen
    ? `Run ${chosen.n}, your pick: ${base.chosen.projected ? "projected " : ""}${heldNow}% on the held-back set (from ${r.heldBase}%), ${costText}${verified ? "" : " — verify it before it can ship"}`
    : searchSaid;
  /* The search stopped once runs in a row stopped beating the best so far;
     the best was verified like any other winner, and ships the same way. */
  if (plateauStopOf(r)) {
    const s = r.stoppedEarly;
    return {
      ...base,
      ending: { key: "stopped-early", label: "Stopped early" },
      headline: `Stopped early — no gain beyond noise in the last ${noGainRunsOf(s)} trials, after ${s.after} of up to ${s.of}. Best: ${said.charAt(0).toLowerCase()}${said.slice(1)}.`,
    };
  }
  if (record.stoppedAtBudget) {
    const s = record.stoppedAtBudget;
    const limit =
      s.by === "time"
        ? `${s.minutes}-minute time cap`
        : `${creditsLabel(s.budget || 0)} budget`;
    return {
      ...base,
      ending: { key: "budget", label: "Stopped at budget" },
      headline: `Stopped at the ${limit} after ${s.trials} of ${s.of} trials. Best so far: ${said.charAt(0).toLowerCase()}${said.slice(1)}.`,
    };
  }
  return {
    ...base,
    ending: { key: "ready", label: "Ready for review" },
    headline: `${said}.`,
  };
};

/**
 * The fix a Suggestion-only improvement hands over: the search's pick, or
 * else the changes that address the most failing scenarios.
 */
export const suggestedFixOf = (record) => {
  const r = record?.result;
  if (!r) return [];
  if (r.winner?.proposals?.length) return r.winner.proposals;
  return [...(r.pool || [])]
    .sort((a, b) => (b.addresses?.length || 0) - (a.addresses?.length || 0))
    .slice(0, 2);
};

/*
  How much the held-back result is worth: proven, likely, or too few to tell.
  "Fixed 3 of 4" reads as proof whatever the size of the set; this says what
  it's worth. Read off the winner's re-run after the pick (new improvements).
*/
export const PROVEN_MIN = 5;
export const TELL_MIN = 4;
export const proofStrength = (r, issue, held) => {
  if (!r?.rsi || !r.winner) return null;
  if (r.split?.thin)
    return {
      key: "few",
      label: "Too few to tell",
      note: "nothing could be held back, so this rests on scenarios the search saw",
    };
  /* What it had to fix where it never looked: the issue's held-back scenarios
     and the environment's that fail the same way — else every failing one. */
  const envT = (r.heldTasks || []).filter(
    (t) =>
      t.envHeld &&
      t.before !== "passed" &&
      r.heldPerScenario?.[t.id] !== "off-script",
  );
  const envFixed = envT.filter(
    (t) => r.heldPerScenario?.[t.id] === "fixed",
  ).length;
  const n = issue ? issue.held + envT.length : held?.failingBefore || 0;
  const fixed = issue ? issue.heldFixed + envFixed : held?.fixed || 0;
  const parts =
    issue && envT.length
      ? ` (${issue.heldFixed} of ${issue.held} from the issue, ${envFixed} of ${envT.length} elsewhere that fail the same way)`
      : "";
  const of = `fixed ${fixed} of ${n} failing ${n === 1 ? "scenario" : "scenarios"} it never saw${parts}`;
  if (n < TELL_MIN)
    return {
      key: "few",
      label: "Too few to tell",
      note: `${of} — it takes ${TELL_MIN} or more to tell`,
    };
  /* Scored on an evaluation that agrees with people at chance: whatever it
     moved, it can't prove the agent got better. */
  const goalRel = r.goal?.reliability;
  if (goalRel?.tier === "unreliable") {
    return {
      key: "none",
      label: "Not proven",
      note: `${of}, but scored on ${r.goal.label}, which agrees with your annotators about as often as chance (κ ${goalRel.kappa.toFixed(2)})`,
    };
  }
  const lift = r.heldScore - r.heldBase;
  const noise = r.noise || 0;
  const share = fixed / n;
  /* Older improvements weren't re-run after the pick: likely at best. */
  const confirmed = !!r.confirm?.held;
  const rerun = r.confirm
    ? confirmed
      ? ", and again on a re-run after the pick"
      : ", but not on the re-run after the pick"
    : ", on the same run that picked it";
  if (
    confirmed &&
    fixed >= PROVEN_MIN &&
    share >= 0.75 &&
    (issue || lift >= 2 * noise)
  ) {
    return { key: "proven", label: "Proven", note: `${of}${rerun}` };
  }
  if (
    (confirmed || !r.confirm) &&
    fixed >= 2 &&
    share >= 0.5 &&
    (issue || lift > noise)
  ) {
    const short = !r.confirm
      ? "a re-run after the pick"
      : fixed < PROVEN_MIN
        ? `${PROVEN_MIN} fixed`
        : "three in four fixed";
    return {
      key: "likely",
      label: "Likely",
      note: `${of}${rerun} — ${short} would prove it`,
    };
  }
  return { key: "none", label: "Not proven", note: `${of}${rerun}` };
};

/* Marked must-pass but not passing every repeat today — named, never gating. */
export const markedNotCountedText = (r) => {
  const list = r?.markedNotCounted || [];
  if (!list.length) return null;
  const flaky = list.every((x) => x.flaky);
  return list.length === 1
    ? `“${list[0].title.replace(/\.$/, "")}” is marked must-pass but ${flaky ? "flaky" : "failing"} today, so it isn't counted`
    : `${list.length} marked must-pass but ${flaky ? "flaky" : "not passing every repeat"} today, not counted`;
};

/* What was held back, said once for the proof, the receipt and the hints. */
export const heldSplitText = (split) => {
  if (!split || split.thin || !["issue", "goal"].includes(split.kind))
    return null;
  const env = split.envHeld
    ? ` + ${split.envHeld} environment ${split.envHeld === 1 ? "scenario" : "scenarios"} failing the same way`
    : "";
  return `${split.held} of ${split.reproducing} ${split.kind === "issue" ? "reproducing" : "failing"} groups + ${split.fresh} fresh${env}`;
};

/* A fix judged on its issue first: Fix on an Error Feed issue, or on an issue
   from a run's Insights tab. */
export const isIssueFix = (record) =>
  record?.trigger?.kind === "error-feed" ||
  record?.trigger?.source === "run-insights";

/**
 * For a fix from the Error Feed or a run's Insights: what the winner did to
 * the issue itself, on the scenarios where it failed — including the ones the
 * search never saw. Null for any other improvement.
 */
export const issueOutcome = (record) => {
  const r = record?.result;
  const id = isIssueFix(record) ? record.trigger.issueId : null;
  if (!id || !r) return null;
  /* Inconclusive scenarios aren't targets (new improvements). */
  const target = (t) =>
    t.fromIssue === id &&
    t.before !== "passed" &&
    !(record.frozen?.inconclusiveOut && t.before === "flaky");
  /* A conversation where the caller went off script says nothing either way. */
  const trainT = (r.trainMeasuredTasks || [])
    .filter(target)
    .filter((t) => r.winner?.perScenario?.[t.id] !== "off-script");
  const heldT = (r.heldTasks || [])
    .filter(target)
    .filter((t) => r.heldPerScenario?.[t.id] !== "off-script");
  const trainFixed = trainT.filter(
    (t) => r.winner?.perScenario?.[t.id] === "fixed",
  ).length;
  const heldFixed = heldT.filter(
    (t) => r.heldPerScenario?.[t.id] === "fixed",
  ).length;
  return {
    id,
    targets: trainT.length + heldT.length,
    fixed: r.winner ? trainFixed + heldFixed : 0,
    held: heldT.length,
    heldFixed: r.winner ? heldFixed : 0,
  };
};

/** The issue line under a fix's verdict. */
export const issueLine = (issue, held, { thin = false } = {}) => {
  if (!issue?.targets) return null;
  if (thin || !issue.held) {
    return thin
      ? "Held-back check is thin: too few of the issue's scenario groups reproduced to hold any back, so this rests on scenarios the search saw."
      : "None of the issue's scenarios were held back, so this rests on scenarios the search saw.";
  }
  const own =
    issue.held === 1
      ? `The one issue scenario the search never saw ${issue.heldFixed ? "is fixed" : "still fails"}`
      : `${issue.heldFixed} of the ${issue.held} issue scenarios the search never saw are fixed`;
  const restFailing = held ? held.failingBefore - issue.held : 0;
  const rest =
    held && restFailing > 0
      ? `; on the rest of the held-back set it fixed ${held.fixed - issue.heldFixed} of ${restFailing} other failing scenarios`
      : "";
  return `${own}${rest}.`;
};

/*
  How a candidate is proven, cheapest first; it stops at the first tier it
  fails. Static checks (model, protected parts, nothing touching the judges;
  for a code change, that it fits the sealed environment — same entry point,
  build and start command — and the repository's own tests), the target
  scenarios, the held-back set
  — scored once, at the end, for finalists only — the must-pass set (a
  failure counts only when it repeats on a re-run), and repeats.
*/
export const ladderOf = (record, trial) => {
  const r = record?.result;
  if (!r || !trial) return [];
  const isWinner = r.winner?.n === trial.n;
  const finalist = (r.finalists || []).find((f) => f.n === trial.n) || null;
  const tests = trial.repoTests || null;
  /* Only code candidates in an improvement stamped with the check carry it. */
  const compat = trial.compat || null;
  /* Stamped records check statics for real, and say what deeper proof a code change got. */
  const fz = record.frozen || {};
  const stat = fz.staticCheck ? trial.static || { pass: true } : null;
  const reps = fz.repeats || REPEATS;
  const tiers = [
    stat
      ? {
          id: "static",
          label: "Static checks",
          state: stat.pass ? "pass" : "fail",
          note: stat.pass
            ? "model, protected parts, areas, settings in range, judges untouched"
            : `${stat.reason} It stops here.`,
        }
      : {
          id: "static",
          label: "Static checks",
          state: "pass",
          note: "model, protected parts, judges untouched",
        },
    ...(compat
      ? [
          {
            id: "compat",
            label: "Fits the environment",
            state: compat.pass ? "pass" : "fail",
            note: compat.pass
              ? compat.note || "entry point, build and start command unchanged"
              : `${compat.reason} It stops here.`,
          },
        ]
      : []),
    ...(tests
      ? [
          {
            id: "repoTests",
            label: "Repository tests",
            state: tests.pass ? "pass" : "fail",
            note: tests.pass
              ? `${tests.command} · still pass`
              : `${tests.command} · failed — it stops here`,
          },
        ]
      : []),
    /* Stamped records hold every candidate to cost and latency as it runs. */
    ...(stat
      ? [
          {
            id: "limits",
            label: "Cost and latency",
            state: trial.overLimit ? "fail" : "pass",
            note: trial.overLimit
              ? `${trial.overLimit.actual} — dropped here`
              : "within the agent's ceilings",
          },
        ]
      : []),
    /* A run issue's Fix targets the issue's scenarios; a goal the scenarios
       short of it; older records the rest of the suite with them. */
    typeof trial.issueFixed === "number" && trial.issueTargets
      ? {
          id: "target",
          label: "Target scenarios",
          state: trial.issueFixed > 0 ? "pass" : "fail",
          note:
            trial.issueFixed > 0
              ? `fixed ${trial.issueFixed} of ${trial.issueTargets} issue ${trial.issueTargets === 1 ? "scenario" : "scenarios"} the search could see`
              : "didn't fix the issue's scenarios",
        }
      : typeof trial.goalFixed === "number" && trial.goalTargets
        ? {
            id: "target",
            label: "Target scenarios",
            state: trial.goalFixed > 0 && trial.delta > 0 ? "pass" : "fail",
            note:
              trial.goalFixed > 0
                ? `fixed ${trial.goalFixed} of ${trial.goalTargets} short of ${goalWords(r.goal)} · ${trial.delta >= 0 ? "+" : "−"}${Math.abs(trial.delta)} pts`
                : `fixed none of the ${trial.goalTargets} short of ${goalWords(r.goal)}`,
          }
        : {
            id: "target",
            label: "Target scenarios",
            state: trial.delta > 0 ? "pass" : "fail",
            note:
              trial.delta > 0
                ? `+${trial.delta} pts on the target scenarios`
                : "no gain on the scenarios it was for",
          },
    { id: "held", label: "Held-back set", state: "todo", note: "" },
    { id: "mustpass", label: "Must-pass set", state: "todo", note: "" },
    { id: "repeats", label: "Repeats", state: "todo", note: "" },
  ];
  const finish = (t) => t;
  const at = (id) => tiers.findIndex((t) => t.id === id);
  const set = (id, patch) => {
    tiers[at(id)] = { ...tiers[at(id)], ...patch };
  };
  const after = (id) => tiers.slice(at(id) + 1).map((t) => t.id);
  if (stat && !stat.pass) {
    after("static").forEach((id) =>
      set(id, { state: "skip", note: "not run — it failed a static check" }),
    );
    return finish(tiers);
  }
  if (compat && !compat.pass) {
    after("compat").forEach((id) =>
      set(id, {
        state: "skip",
        note: "not run — it doesn't fit the sealed environment",
      }),
    );
    return finish(tiers);
  }
  if (tests && !tests.pass) {
    after("repoTests").forEach((id) =>
      set(id, { state: "skip", note: "stopped at the repository's tests" }),
    );
    return finish(tiers);
  }
  if (at("limits") >= 0 && tiers[at("limits")].state === "fail") {
    ["target", "held", "mustpass", "repeats"].forEach((id) =>
      set(id, {
        state: "skip",
        note: "not a finalist — over the agent's limits",
      }),
    );
    return finish(tiers);
  }
  if (tiers[at("target")].state === "fail") return finish(tiers);
  /* Must-pass is run for finalists only on newer records — read off the
     finalist; older ones read it off the run. */
  const fin0 = (r.finalists || []).find((f) => f.n === trial.n) || null;
  const mpFails = fin0?.mustPassFails || trial.mustPassFails || [];
  const repeated = mpFails.filter((f) => f.repeated);
  const once = mpFails.filter((f) => !f.repeated);
  if (!finalist) {
    set("held", {
      state: "skip",
      note: trial.brokeBlocker
        ? "not a finalist — a must-pass failure repeated on its run"
        : "not a finalist",
    });
    if (!trial.brokeBlocker)
      set("mustpass", { state: "skip", note: "runs for finalists only" });
    if (trial.brokeBlocker)
      set("mustpass", {
        state: "fail",
        note: `“${repeated[0]?.title || "a must-pass scenario"}” failed, and again on a re-run`,
      });
    return finish(tiers);
  }
  const thin = !!r.split?.thin;
  const lift = finalist.heldScore - r.heldBase;
  const heldNote = thin
    ? "held-back check is thin — too few groups reproduced to hold any back"
    : `${finalist.pick ? "finalist 1" : "finalist"} · scored once at the end · ${lift >= 0 ? "+" : "−"}${Math.abs(lift)} pts · run-to-run noise ±${r.noise || 0}${r.calibration ? ", from the baseline run twice" : ""}`;
  set("held", {
    state: thin ? "thin" : finalist.accepted ? "pass" : "fail",
    note:
      finalist.accepted || thin ? heldNote : `${heldNote} · ${finalist.why}`,
  });
  if (
    tiers[at("held")].state === "fail" &&
    finalist.why !== "a must-pass scenario failed again on a re-run"
  )
    return finish(tiers);
  const blockers = fin0?.mustPassFails
    ? repeated
    : isWinner
      ? r.heldBlockers || []
      : finalist.why === "a must-pass scenario failed again on a re-run"
        ? [{}]
        : [];
  const parts = r.mustPass ? `${r.mustPass.total} must-pass` : "must-pass";
  /* Each other environment picked: its must-pass set held, or which broke. */
  const envBroke = fz.multiEnv
    ? (finalist.envs || []).find((e) => !e.held)
    : null;
  const envNote =
    fz.multiEnv && (finalist.envs || []).length
      ? ` · held in ${
          finalist.envs
            .filter((e) => e.held)
            .map((e) => e.name)
            .join(", ") || "no other environment"
        }`
      : "";
  set("mustpass", {
    state: blockers.length || envBroke ? "fail" : "pass",
    note: blockers.length
      ? `${blockers[0].title ? `“${blockers[0].title}”` : "a must-pass scenario"} failed, and again on a re-run`
      : envBroke
        ? `“${envBroke.broke.title}” failed in ${envBroke.name}, and again on a re-run`
        : `${parts} held${envNote}${once.length ? ` · ${once.length} failed once and passed the re-run — not counted` : ""}${(r.flakyToday || []).length ? ` · ${(r.flakyToday || []).length} flaky today, never disqualifying` : ""}`,
  });
  if (tiers[at("mustpass")].state === "fail") return finish(tiers);
  set("repeats", {
    state: "pass",
    note:
      fz.depthRule && finalist.fullRegression
        ? `Code change → ${finalist.repeats} repeats per finalist and the full regression set · flips counted only when they repeat`
        : `each scenario ${fz.depthRule ? finalist.repeats || reps : REPEATS}×, flips counted only when they repeat`,
  });
  return finish(tiers);
};

/* The judge comes from a different model family than the proposer, so the
   proposer never grades its own work. */
export const judgeFor = (proposerId = "") =>
  /claude/i.test(proposerId) ? "GPT-5" : "Claude Sonnet 5";

/* ── where a running improvement is ────────────────────────────────────── */

/*
  Draft → Reproducing → Searching → Verifying, derived from the step of its
  trace it has reached — timed the way the trace plays (`RunTrace`), from
  when the run started. Null once it has ended.
*/
/* Diagnosing is a step of the search, not a state of its own — it reads as
   Searching wherever a status is shown; the stepper still names Diagnose. */
export const IMPROVEMENT_PHASES = {
  draft: "Draft",
  reproducing: "Reproducing",
  diagnosing: "Searching",
  searching: "Searching",
  verifying: "Verifying",
};
const STEP_PHASE = {
  reread: "reproducing",
  reproduce: "reproducing",
  calibrate: "reproducing",
  diagnose: "diagnosing",
  split: "searching",
  candidates: "searching",
  trials: "searching",
  holdout: "verifying",
  suggest: "verifying",
};
export const TRACE_STEP_MS = 800;
/* One candidate run, start to scored — long enough to watch them arrive. */
export const RUN_MS = 1600;
const runsOfStep = (s) =>
  (s.lines || []).filter((l) => /^run \d+ —/.test(l)).length;
/* How long one of an improvement's runs plays — paced like a run of its
   size when the record says (`runMs`, set as it starts), else the old pace. */
export const runMsOf = (record) => record?.runMs || RUN_MS;
const stepDuration = (s, stepMs = TRACE_STEP_MS, runMs = RUN_MS) => {
  if (s.id === "trials") return runsOfStep(s) * runMs + stepMs * 0.55;
  const n = (s.lines || []).length;
  return n * Math.max(180, stepMs / (n + 1)) + stepMs * 0.55;
};
/*
  Endings decided before any search — Could not reproduce, Nothing to
  improve, Stopped at budget, Failed — still play their Reproducing phase
  first (RSI PRD: Draft → Reproducing → Searching → Verifying → an ending).
  The record is written running with the ending pending (`pendingEnding`)
  and reads as that ending once its Reproduce steps would have played.
*/
const REPRODUCE_STEPS = new Set(["reread", "reproduce", "calibrate"]);
/** The trace's Reproduce steps — what a pending ending plays before it settles. */
export const reproduceStepsOf = (record) => {
  const r = record?.result || record?.pendingResult;
  return r
    ? searchTrace(r, record).filter((s) => REPRODUCE_STEPS.has(s.id))
    : [];
};
export const reproduceMsOf = (record) =>
  Math.max(
    reproduceStepsOf(record).reduce((t, s) => t + stepDuration(s), 0),
    /* Never so short it reads as instant. */
    TRACE_STEP_MS * 3,
  );
/** The status a record has now — a pending ending once its Reproduce has played. */
/* When an improvement's own clock starts. A fix waits for its first run
   (`waitingForRun`) — until that run finishes, nothing after Reproducing
   plays, so the fix never runs ahead of the run you're watching. */
export const startMsOf = (record, now = Date.now()) => {
  if (record?.waitingForRun) {
    /* Unwatched, its run is due by `waitUntil`; watched, the run's page
       starts it the moment the run ends. */
    const until = Date.parse(record.waitUntil || "");
    return until && now >= until ? until : now;
  }
  return Date.parse(record?.startedAt || record?.createdAt || "") || now;
};
/** Still waiting on its first run at `now`. */
export const waitingAt = (record, now = Date.now()) =>
  !!record?.waitingForRun && !(Date.parse(record.waitUntil || "") <= now);

export const statusOf = (record, now = Date.now()) => {
  if (!record?.pendingEnding) return record?.status;
  /* A caller that marked it finished without knowing — its ending stands. */
  if (record.status === OPT_STATUS.COMPLETED) return record.pendingEnding;
  if (record.status !== OPT_STATUS.RUNNING) return record.status;
  const start = startMsOf(record, now);
  return now >= start + reproduceMsOf(record)
    ? record.pendingEnding
    : record.status;
};
/** The record with its pending ending applied — what a page saves once Reproduce has played. */
export const settlePending = (record, now = Date.now()) => {
  if (!record?.pendingEnding) return record;
  const { pendingEnding, ...rest } = record;
  return {
    ...rest,
    status: pendingEnding,
    completedAt: new Date(now).toISOString(),
  };
};

export const improvementPhaseOf = (record, now = Date.now()) => {
  if (record?.status === "draft")
    return { key: "draft", label: IMPROVEMENT_PHASES.draft };
  if (statusOf(record, now) !== OPT_STATUS.RUNNING) return null;
  /* Waiting on its first run, or an ending decided up front: reproducing. */
  if (record.pendingEnding || waitingAt(record, now))
    return {
      key: "reproducing",
      label: IMPROVEMENT_PHASES.reproducing,
      step: "reproduce",
    };
  const r = record.result;
  if (!r) return { key: "draft", label: IMPROVEMENT_PHASES.draft };
  const steps = searchTrace(r, record);
  const elapsed = Math.max(0, now - startMsOf(record, now));
  let t = 0;
  for (const s of steps) {
    t += stepDuration(s, TRACE_STEP_MS, runMsOf(record));
    if (elapsed < t) {
      const key = STEP_PHASE[s.id] || "searching";
      return { key, label: IMPROVEMENT_PHASES[key], step: s.id };
    }
  }
  return {
    key: "verifying",
    label: IMPROVEMENT_PHASES.verifying,
    step: steps[steps.length - 1]?.id,
  };
};

/*
  How far a running improvement has got, on the one clock every view reads —
  from when it started, timed the way the trace plays. The stage is the
  PRD's: Reproduce, Search, Verify. `runsDone` is how many of its runs have
  finished; `done` once the last step has played. Null when it isn't running.
*/
const STEP_STAGE = {
  reread: "reproduce",
  reproduce: "reproduce",
  calibrate: "reproduce",
  diagnose: "search",
  split: "search",
  candidates: "search",
  trials: "search",
  holdout: "verify",
  suggest: "verify",
};
export const improvementProgressOf = (record, now = Date.now()) => {
  if (record?.status !== OPT_STATUS.RUNNING) return null;
  /* An ending decided up front: Reproduce plays, then it settles to it. */
  if (record.pendingEnding) {
    const elapsed = Math.max(0, now - startMsOf(record, now));
    const total =
      record.fixDepth === "suggestion"
        ? 0
        : (record.pendingResult?.trials || []).length;
    return {
      stage: "reproduce",
      step: "reproduce",
      runsDone: 0,
      runsTotal: total,
      done: elapsed >= reproduceMsOf(record),
      pending: true,
    };
  }
  if (!record.result) return null;
  const steps = searchTrace(record.result, record);
  const total =
    record.fixDepth === "suggestion" ? 0 : (record.result.trials || []).length;
  /* The most it could run, and whether it stopped short: while searching it
     reads "2 of up to 8"; once the runs have played, an early stop is said. */
  const early =
    record.fixDepth !== "suggestion" ? plateauStopOf(record.result) : null;
  const runsMax = early ? early.of : total;
  const elapsed = Math.max(0, now - startMsOf(record, now));
  let t = 0;
  let runsDone = 0;
  for (const s of steps) {
    const d = stepDuration(s, TRACE_STEP_MS, runMsOf(record));
    if (elapsed < t + d) {
      if (s.id === "trials")
        runsDone = Math.min(total, Math.floor((elapsed - t) / runMsOf(record)));
      const stage = STEP_STAGE[s.id] || "search";
      return {
        stage,
        step: s.id,
        runsDone,
        runsTotal: total,
        runsMax,
        stoppedEarly: !!early && stage === "verify",
        done: false,
      };
    }
    if (s.id === "trials") runsDone = total;
    t += d;
  }
  return {
    stage: "verify",
    step: steps[steps.length - 1]?.id,
    runsDone: total,
    runsTotal: total,
    runsMax,
    stoppedEarly: !!early,
    done: true,
  };
};

/*
  A running improvement's run count, said the same everywhere. The planned
  count is a maximum: "2 of up to 8" while it searches; once the runs have
  played, the count it used, and — if it stopped short — why.
    → { done, max, upTo, stopped } (stopped: "stopped early, no gain in the last 2" or null)
*/
export const runsCountOf = (progress, result) => {
  if (!progress) return null;
  const max = progress.runsMax ?? progress.runsTotal;
  const stopped = progress.stoppedEarly
    ? `stopped early, no gain beyond noise in the last ${noGainRunsOf(result?.stoppedEarly)}`
    : null;
  return {
    done: progress.runsDone,
    max,
    upTo: !stopped && progress.stage !== "verify",
    stopped,
  };
};

/**
 * A running improvement whose search has played out is finished — whether or
 * not anyone had it open. Without this it sat on Verifying until its page was
 * opened, and a day later read as an infrastructure failure. Applied when the
 * store loads.
 */
/*
  Seeded improvements were once stored with their cost ceiling rewritten to
  +5% though the agent's limits said "No increase". Only records built by
  the older engine (no `staticCheck` stamp) were rewritten; read them back
  to "No increase".
*/
const legacyCeiling = (o) =>
  !o.frozen?.staticCheck &&
  o.limits?.costMode !== "abs" &&
  o.limits?.costCeiling === LEGACY_COST_CEILING &&
  o.frozen?.cost === `At most +${LEGACY_COST_CEILING}%`;
const unlegacy = (o) => ({
  ...o,
  limits: { ...o.limits, costCeiling: 0 },
  frozen: { ...o.frozen, cost: "No increase" },
});

export const settleImprovements = (es, now = Date.now()) => {
  const list = es?.optimizations || [];
  if (
    !list.some(
      (o) =>
        (o.status === OPT_STATUS.RUNNING && (o.result || o.pendingEnding)) ||
        (o.pendingEnding && o.status === OPT_STATUS.COMPLETED) ||
        legacyCeiling(o),
    )
  )
    return es;
  let changed = false;
  const optimizations = list.map((o0) => {
    const o = legacyCeiling(o0) ? unlegacy(o0) : o0;
    if (o !== o0) changed = true;
    /* An ending that played its Reproducing phase settles to it. */
    if (o.pendingEnding) {
      if (statusOf(o, now) === o.status) return o;
      changed = true;
      return settlePending(o, now);
    }
    if (o.status !== OPT_STATUS.RUNNING || !o.result) return o;
    const start = startMsOf(o, now);
    const done =
      start +
      searchTrace(o.result, o).reduce(
        (t, s) => t + stepDuration(s, TRACE_STEP_MS, runMsOf(o)),
        0,
      );
    if (now < done) return o;
    changed = true;
    return {
      ...o,
      status: OPT_STATUS.COMPLETED,
      completedAt: new Date(done).toISOString(),
    };
  });
  return changed ? { ...es, optimizations } : es;
};

/**
 * Starting the same improvement twice returns the one already running
 * (roadmap runs.md: repeated submission reuses the existing handle) — same
 * source run, base version and goal. Null when nothing matches.
 */
export const runningTwinOf = (envState, { fromRunId, baseAgentVersion, goalId = null }) =>
  optimizations(envState).find(
    (o) =>
      o.status === OPT_STATUS.RUNNING &&
      o.fromRunId === fromRunId &&
      (o.baseAgentVersion || null) === (baseAgentVersion || null) &&
      (o.trigger?.goal?.id || null) === (goalId || null),
  ) || null;
