/*
  Production traces to scenarios (roadmap traces.md, Week 2): pick exact
  traces, freeze their IDs, see what evidence each one actually has, and
  choose what the new scenario should represent. The selection never follows
  a changing filter or new traces arriving.
*/
import { productionConversationsOf, replayScenarioOf } from "./scenarioSources";
import { agentVersions } from "./versions";
import { hashSeed } from "./runStream";
import { scrubPersonalData } from "./rsiModel";

/** The Observe project these traces come from — the one linked to the
    agent, or none. A name is never made up for an unlinked agent. */
export const traceProjectOf = (env, envState) => {
  const p = envState?.agent?.values?.observeProject;
  return p?.name || p?.id || null;
};

/**
 * The production traces for this environment's agent: each with its trace ID,
 * agent version, outcome, spans and the evidence it actually holds —
 * transcript, tool calls with responses, and the starting state of the
 * records it touched (often missing).
 */
/* Something a production call relied on that lives outside the agent's own
   tools — the world built from the agent can't hold it until someone adds
   it (a clinic's referrals come from the GP's system). */
const OUTSIDE = [
  [/clinic|appointment|patient|health/i, "a referral record"],
  [/refund|return|retail|order/i, "a gift-card balance"],
  [/airline|flight|travel|rebook/i, "a partner-airline booking"],
  [/bank/i, "a joint-account record"],
  [/guest|hotel|booking/i, "a group-booking record"],
  [/support|help|customer/i, "a warranty record"],
];
const outsideRecordOf = (env) =>
  OUTSIDE.find(([re]) => re.test(`${env?.name || ""} ${env?.id || ""}`))?.[1] ||
  "a record from another system";

/*
  How Observe records production traffic (tracer models: a Trace is one run —
  one input, its spans, one output — with an optional session; a TraceSession
  groups the traces of one conversation):
  · Chat agents: each caller turn is one trace; the conversation is a session.
  · Voice agents: a call is one trace holding the whole conversation (Observe
    lists them as calls); there are no sessions to pick.
  Personal data is shown scrubbed (`shown`, fake values marked); the raw text
  stays on `turns`, which `replayScenarioOf` scrubs again when it drafts.
*/
export const isVoiceEnv = (env) => env?.surface === "voice";

const shownTurns = (turns, name) =>
  turns.map((t) => ({
    role: t.role,
    text: scrubPersonalData(t.text, { names: [name], mark: true }).text,
  }));

const versionOf = (envState, key) => {
  const versions = agentVersions(envState).map((v) => v.label);
  if (!versions.length) return "v1";
  const h = hashSeed(key) >>> 0;
  return versions[
    Math.max(0, versions.length - 1 - (h % Math.min(2, versions.length)))
  ];
};

/* One conversation → the traces it made, plus its session (chat only). */
const recordConversations = (env, envState) => {
  const convs = productionConversationsOf(env);
  const voice = isVoiceEnv(env);
  const traces = [];
  const sessions = [];
  convs.forEach((c, i) => {
    const h = hashSeed(c.id) >>> 0;
    const agentVersion = versionOf(envState, c.id);
    const startingState = h % 3 === 0;
    const uncovered = h % 3 === 1 ? outsideRecordOf(env) : null;
    if (voice) {
      /* The call is the trace. */
      traces.push({
        ...c,
        traceId: `T-${101 + i}`,
        agentVersion,
        spans: 6 + (h % 9),
        shown: shownTurns(c.turns, c.callerName),
        evidence: {
          transcript: true,
          tools: h % 4 !== 0,
          startingState,
          uncovered,
        },
      });
      return;
    }
    const sessionId = `S-${201 + i}`;
    /* The agent's reply to the last caller turn, when the sample ends on one. */
    const full =
      c.turns[c.turns.length - 1]?.role === "caller"
        ? [
            ...c.turns,
            { role: "agent", text: "I've noted that and updated the account." },
          ]
        : c.turns;
    const callerAt = c.turns
      .map((t, k) => (t.role === "caller" ? k : -1))
      .filter((k) => k >= 0);
    const mine = callerAt.map((k, n) => {
      const th = hashSeed(`${c.id}:${n}`) >>> 0;
      const reply = full[k + 1]?.text || "";
      const exchange = [
        ...c.turns.slice(0, k + 1),
        { role: "agent", text: reply },
      ];
      const last = n === callerAt.length - 1;
      return {
        ...c,
        id: `${c.id}-t${n + 1}`,
        traceId: `T-${101 + traces.length + n}`,
        sessionId,
        turn: n + 1,
        turnsInSession: callerAt.length,
        /* Its input carries the session so far; the drafts replay up to this turn. */
        turns: c.turns.slice(0, k + 1),
        shown: shownTurns(exchange, c.callerName),
        priorTurns: k,
        at: new Date(new Date(c.at).getTime() + n * 45000).toISOString(),
        durationS: null,
        latencyS: 1.4 + (th % 60) / 10,
        outcome: c.outcome === "failed" && last ? "failed" : "passed",
        agentVersion,
        spans: 3 + (th % 6),
        /* Tool calls this run made (none while the agent only asks); missing
           only when made but not recorded. */
        toolCalls: n > 0 ? 1 + (th % 2) : 0,
        evidence: {
          transcript: true,
          tools: n === 0 || th % 4 !== 0,
          /* The records as they stood when the session began. */
          startingState,
          uncovered: last ? uncovered : null,
        },
      };
    });
    traces.push(...mine);
    sessions.push({
      ...c,
      id: `sess-${c.id}`,
      traceId: sessionId,
      unit: "sessions",
      traceIds: mine.map((t) => t.traceId),
      shown: shownTurns(full, c.callerName),
      shownTraceAt: Object.fromEntries(
        callerAt.map((k, n) => [k, mine[n].traceId]),
      ),
      agentVersion,
      spans: mine.reduce((a, t) => a + t.spans, 0),
      evidence: {
        transcript: true,
        /* Missing for the session when any of its traces lacks them. */
        tools: mine.every((t) => t.evidence.tools),
        startingState,
        uncovered,
      },
    });
  });
  return { traces, sessions };
};

/** Production traces: a chat agent's turns, or a voice agent's calls. */
export const productionTracesOf = (env, envState) =>
  recordConversations(env, envState).traces;

/** A chat agent's sessions — one per conversation, listing its traces. None for voice. */
export const productionSessionsOf = (env, envState) =>
  recordConversations(env, envState).sessions;

/** Traces and sessions together, to find an import's source by its ID. */
export const productionEvidenceOf = (env, envState) => {
  const r = recordConversations(env, envState);
  return [...r.traces, ...r.sessions];
};

/** What the new scenario represents — each says what it keeps and what it makes up. */
export const TRACE_GOALS = [
  {
    id: "reproduce",
    label: "Reproduce the failure",
    short: "What went wrong, judged against your policy",
    keeps:
      "The triggering situation, what the caller said and the failure evidence",
    makes:
      "What should have happened — written from the policy, never copied from the agent's answer",
    needs: "The starting state of the records, for an exact reproduction",
  },
  {
    id: "preserve",
    label: "Keep a success as a regression",
    short: "Locks in a call that worked",
    keeps: "The workflow that worked, step by step",
    makes:
      "Explicit checks — an observed success still needs a reviewed expected outcome",
    needs: "Tool calls and their responses",
  },
  {
    id: "replay",
    label: "Replay the conversation",
    short: "Plays each call back word for word",
    keeps: "The caller's words, said verbatim, and the recorded tool responses",
    makes:
      "One scenario per trace that plays the conversation back as it happened — nothing made up, so state the trace didn't record stays missing",
    needs: "A transcript; tool responses to replay them",
  },
  {
    id: "variants",
    label: "Make variations",
    short: "Similar cases, marked Generated",
    keeps: "A link to the trace each one comes from",
    makes:
      "Neighbouring cases — changed data, ambiguity, a tool failing, a pushy caller — labelled Generated, never as something that happened",
    needs: "A transcript",
  },
  {
    id: "pattern",
    label: "Pull out a common pattern",
    short: "One scenario for what the traces share",
    keeps: "Every trace as a source, and which ones didn't fit",
    makes:
      "One scenario for the situation the traces share, standing for the whole group",
    needs: "Two or more traces with the same symptom",
    min: 2,
  },
];

const day = (iso) =>
  new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

/** The time range the selected traces span. */
export const rangeOf = (traces) => {
  if (!traces.length) return "";
  const t = traces.map((x) => Date.parse(x.at)).sort((a, b) => a - b);
  const a = day(new Date(t[0]).toISOString());
  const b = day(new Date(t[t.length - 1]).toISOString());
  return a === b ? a : `${a} – ${b}`;
};

/*
  What one trace's draft is, from the goal and what the trace shows: a
  passed trace can't reproduce a failure — it's kept as a regression — and
  a failed one has no success to keep, so it reproduces the failure.
*/
export const draftIntentOf = (goal, outcome) => {
  if (goal === "reproduce" && outcome === "passed") return "preserve";
  if (goal === "preserve" && outcome === "failed") return "reproduce";
  return goal;
};

/* The title and summary of a reproduce / keep draft — what the draft is. */
const INTENT_TITLE = {
  reproduce: (id) => `reproduces ${id}`,
  preserve: (id) => `keeps ${id} as a regression`,
};
const intentSummaryOf = (intent, origin, id) =>
  intent === "preserve"
    ? `Keeps what worked in ${origin} ${id} as a regression`
    : `Reproduces ${origin} ${id} — the situation and the failure, judged against the policy`;

/*
  A draft retitled for what its record shows, once the reviewer says so:
  Failure reproduces it, Success keeps it as a regression. Partial evidence
  — and every other kind of draft — keeps the title it has.
*/
export const retitleForShows = (row, shows) => {
  const imp = row?.traceImport;
  const intent =
    shows === "failure" ? "reproduce" : shows === "success" ? "preserve" : null;
  const [id] = imp?.sourceIds || [];
  if (
    !intent ||
    !id ||
    imp.sourceIds.length !== 1 ||
    !["reproduce", "preserve"].includes(imp.goal) ||
    imp.goal === intent
  )
    return row;
  /* Only a title this import wrote ("<topic> — reproduces <id>"). */
  const title = String(row.title || "");
  const tail = [INTENT_TITLE[imp.goal](id), `keeps what worked in ${id}`].find(
    (x) => title.endsWith(` — ${x}`),
  );
  if (!tail) return row;
  const topic = title.slice(0, -` — ${tail}`.length);
  const origin =
    (row.summary || "").match(
      /^(?:Reproduces|Keeps what worked in) (.+?) \S+(?: —| as a regression)/,
    )?.[1] || "production trace";
  return {
    ...row,
    title: `${topic} — ${INTENT_TITLE[intent](id)}`,
    summary: intentSummaryOf(intent, origin, id),
    traceImport: { ...imp, goal: intent },
  };
};

/**
 * The drafts a frozen selection becomes under `goal`. Every draft carries the
 * import — the frozen trace IDs, project, range and goal — so its source
 * stays traceable whatever happens to the filter or the traces later.
 */
export const traceDraftsOf = (traces, goal, env, importInfo) => {
  /* A case that relied on something the world doesn't hold waits for the
     environment — it isn't drafted until the environment covers it. */
  const needsOf = (sourceIds) =>
    traces.find((t) => sourceIds.includes(t.traceId) && t.evidence?.uncovered)
      ?.evidence.uncovered;
  const stamp = (row, sourceIds, extra = {}, intent = goal) => {
    const needs = needsOf(sourceIds);
    return {
      ...row,
      traceImport: { ...importInfo, goal: intent, sourceIds },
      ...extra,
      ...(needs && {
        blocked: {
          gate: "data",
          needs,
          reason: `It relies on ${needs}, which isn't in this environment.`,
        },
      }),
    };
  };
  if (goal === "pattern") {
    const base = replayScenarioOf(traces[0], env);
    return [
      stamp(
        base,
        traces.map((t) => t.traceId),
        {
          id: `pattern::${traces.map((t) => t.id).join("+")}`,
          title: `${traces[0].topic} — pattern across ${traces.length} traces`,
          summary: `One situation standing for production traces ${traces.map((t) => t.traceId).join(", ")}`,
          productionReplay: undefined,
        },
      ),
    ];
  }
  return traces.flatMap((t) => {
    const base = replayScenarioOf(t, env);
    if (goal === "variants")
      return ["the details changed", "a tool failing mid-call"].map((what, i) =>
        stamp(base, [t.traceId], {
          id: `${base.id}::variant-${i + 1}`,
          title: `${t.topic} — variation: ${what}`,
          summary: `Generated variation of production trace ${t.traceId} — not something that happened`,
          generatedVariant: { parentTrace: t.traceId, what },
          productionReplay: undefined,
        }),
      );
    /* Each draft says what it is: a passed trace under "Reproduce the
       failure" is kept as a regression, never titled as reproducing one. */
    const intent = draftIntentOf(goal, t.outcome);
    const titles = {
      reproduce: `${t.topic} — ${INTENT_TITLE.reproduce(t.traceId)}`,
      preserve: `${t.topic} — ${INTENT_TITLE.preserve(t.traceId)}`,
      replay: base.title,
    };
    /* Where it came from, said as it is — an upload isn't a production trace. */
    const origin =
      {
        transcript: "uploaded transcript",
        dataset: "dataset row",
        simulation: "failed simulation trial",
        simCall: "simulated call",
      }[t.sourceKind] || "production trace";
    const summaries = {
      reproduce: intentSummaryOf("reproduce", origin, t.traceId),
      preserve: intentSummaryOf("preserve", origin, t.traceId),
    };
    return [
      stamp(
        base,
        [t.traceId],
        {
          id: goal === "replay" ? base.id : `${intent}::${t.id}`,
          title: titles[intent],
          ...(t.sourceKind && {
            source: {
              transcript: "transcript",
              dataset: "dataset-import",
              simulation: "simulation",
              simCall: "simulation",
            }[t.sourceKind],
          }),
          ...(goal !== "replay" && {
            productionReplay: undefined,
            summary: summaries[intent],
          }),
        },
        intent,
      ),
    ];
  });
};

/** The same import from the CLI. */
export const fromTracesCli = ({ env, ids, goal, group }) =>
  `fi sim scenarios from-traces --environment ${env?.id} ${ids.map((id) => `--trace ${id}`).join(" ")} --scope-unit trace --intent ${goal}${group ? ` --group "${group}"` : ""}`;

/** Drafting cost for an import: the model reads each trace and writes its draft. */
export const draftingCostOf = (n) => ({ low: n * 6, high: n * 11 });

/** A large selection is drafted as a queue — one item at a time, cancellable. */
export const QUEUE_FROM = 8;
