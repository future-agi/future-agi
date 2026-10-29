/*
  Production traces to scenarios (roadmap traces.md, Week 2): pick exact
  traces, freeze their IDs, see what evidence each one actually has, and
  choose what the new scenario should represent. The selection never follows
  a changing filter or new traces arriving.
*/
import { productionConversationsOf, replayScenarioOf } from "./scenarioSources";
import { agentVersions } from "./versions";
import { hashSeed } from "./runStream";

/** The Observe project these traces come from. */
export const traceProjectOf = (env, envState) =>
  envState?.agent?.values?.observeProject?.id ||
  `${String(env?.name || "agent").toLowerCase().replace(/[^a-z0-9]+/g, "-")}-prod`;

/**
 * The production traces for this environment's agent: each with its trace ID,
 * agent version, outcome, spans and the evidence it actually holds —
 * transcript, tool calls with responses, and the starting state of the
 * records it touched (often missing).
 */
export const productionTracesOf = (env, envState) => {
  const versions = agentVersions(envState).map((v) => v.label);
  return productionConversationsOf(env).map((c, i) => {
    const h = hashSeed(c.id) >>> 0;
    return {
      ...c,
      traceId: `T-${101 + i}`,
      agentVersion: versions.length ? versions[Math.max(0, versions.length - 1 - (h % Math.min(2, versions.length)))] : "v1",
      spans: 6 + (h % 9),
      evidence: {
        transcript: true,
        tools: h % 4 !== 0,
        startingState: h % 3 === 0,
      },
    };
  });
};

/** What the new scenario represents — each says what it keeps and what it makes up. */
export const TRACE_GOALS = [
  {
    id: "reproduce",
    label: "Reproduce the failure",
    keeps: "The triggering situation, what the caller said and the failure evidence",
    makes: "What should have happened — written from the policy, never copied from the agent's answer",
    needs: "The starting state of the records, for an exact reproduction",
  },
  {
    id: "preserve",
    label: "Keep a success as a regression",
    keeps: "The workflow that worked, step by step",
    makes: "Explicit checks — an observed success still needs a reviewed expected outcome",
    needs: "Tool calls and their responses",
  },
  {
    id: "replay",
    label: "Replay the conversation",
    keeps: "The caller's words, said verbatim, and the recorded tool responses",
    makes: "Nothing — but it can't recreate state the trace didn't record",
    needs: "A transcript; tool responses to replay them",
  },
  {
    id: "variants",
    label: "Make variations",
    keeps: "A link to the trace each one comes from",
    makes: "Neighbouring cases — changed data, ambiguity, a tool failing, a pushy caller — labelled Generated, never as something that happened",
    needs: "A transcript",
  },
  {
    id: "pattern",
    label: "Pull out a common pattern",
    keeps: "Every trace as a source, and which ones didn't fit",
    makes: "One situation that stands for the group",
    needs: "Two or more traces with the same symptom",
    min: 2,
  },
];

const day = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

/** The time range the selected traces span. */
export const rangeOf = (traces) => {
  if (!traces.length) return "";
  const t = traces.map((x) => Date.parse(x.at)).sort((a, b) => a - b);
  const a = day(new Date(t[0]).toISOString());
  const b = day(new Date(t[t.length - 1]).toISOString());
  return a === b ? a : `${a} – ${b}`;
};

/**
 * The drafts a frozen selection becomes under `goal`. Every draft carries the
 * import — the frozen trace IDs, project, range and goal — so its source
 * stays traceable whatever happens to the filter or the traces later.
 */
export const traceDraftsOf = (traces, goal, env, importInfo) => {
  const stamp = (row, sourceIds, extra = {}) => ({
    ...row,
    traceImport: { ...importInfo, goal, sourceIds },
    ...extra,
  });
  if (goal === "pattern") {
    const base = replayScenarioOf(traces[0], env);
    return [
      stamp(base, traces.map((t) => t.traceId), {
        id: `pattern::${traces.map((t) => t.id).join("+")}`,
        title: `${traces[0].topic} — pattern across ${traces.length} traces`,
        summary: `One situation standing for production traces ${traces.map((t) => t.traceId).join(", ")}`,
        productionReplay: undefined,
      }),
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
    const titles = {
      reproduce: `${t.topic} — reproduces ${t.traceId}`,
      preserve: `${t.topic} — keeps what worked in ${t.traceId}`,
      replay: base.title,
    };
    const summaries = {
      reproduce: `Reproduces production trace ${t.traceId} — the situation and the failure, judged against the policy`,
      preserve: `Keeps what worked in production trace ${t.traceId} as a regression`,
    };
    return [
      stamp(base, [t.traceId], {
        id: goal === "replay" ? base.id : `${goal}::${t.id}`,
        title: titles[goal],
        ...(goal !== "replay" && {
          productionReplay: undefined,
          summary: summaries[goal],
        }),
      }),
    ];
  });
};

/** The same import from the CLI. */
export const fromTracesCli = ({ env, ids, goal, group }) =>
  `agent-learn scenario from-traces --environment ${env?.id} ${ids.map((id) => `--trace ${id}`).join(" ")} --scope-unit trace --intent ${goal}${group ? ` --group "${group}"` : ""}`;

/** Drafting cost for an import: the model reads each trace and writes its draft. */
export const draftingCostOf = (n) => ({ low: n * 6, high: n * 11 });

/** A large selection is drafted as a queue — one item at a time, cancellable. */
export const QUEUE_FROM = 8;
