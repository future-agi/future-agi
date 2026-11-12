/*
  What a trial's evidence holds (roadmap runs.md "Live run and trial
  detail"): each artifact the adapter provides, and the ones it doesn't —
  labelled missing with why, never silently absent. A failed check names
  its source rule and the scenario version it ran. A dispute is a note on
  the result; the recorded score is never rewritten.
*/
import { rulesOf, ruleOfScenario } from "./envModel";
import { scenarioVersionOf } from "./scenarioVersion";
import { takesSnapshot } from "./candidateSource";
import { currentUser } from "./scenarioProvenance";
import { traceOf } from "./runTrace";
import { hashSeed } from "./runStream";
import { sideEffectOf } from "./toolEnv";
import { toolChangeOf, toolTablesOf } from "./toolArgSpec";
import { callToolsOf } from "./callRecord";

/** [{ id, label, recorded, note, pane }] — `pane` is the drawer tab that shows it. */
export const trialArtifactsOf = ({
  task,
  env,
  envState,
  stage,
  conversational,
  voice,
  /* The call record (taskToVoiceData) — when given, its tool calls are the
     ones counted, so Evidence agrees with the transcript. */
  call = null,
}) => {
  const steps = (task?.steps || []).length;
  const tools = call
    ? callToolsOf(call.transcript).length
    : traceOf(task, env, envState).spans.filter((s) => s.kind === "tool")
        .length;
  const agentName = envState?.agent?.name || "This agent";
  return [
    voice && {
      id: "recording",
      label: "Call recording",
      recorded: steps > 0,
      note: steps ? "Both sides, as heard" : "The call never connected",
      pane: "transcript",
    },
    conversational && {
      id: "transcript",
      label: "Transcript",
      recorded: steps > 0,
      note: steps ? `${steps} turns` : "No turns were recorded",
      pane: "transcript",
    },
    !conversational && {
      id: "actions",
      label: "Actions",
      recorded: steps > 0,
      note: steps ? `${steps} actions` : "No actions were recorded",
      pane: "transcript",
    },
    stage === "browser" && {
      id: "screens",
      label: "Screenshots",
      recorded: steps > 0,
      note: "One per action",
      pane: "transcript",
    },
    {
      id: "trace",
      label: "Trace",
      recorded: steps > 0,
      note: steps
        ? "LLM, tool and guard spans on one time axis"
        : "This scenario recorded no steps",
      pane: "trace",
    },
    {
      id: "tools",
      label: "Tool calls",
      recorded: true,
      note: tools
        ? `${tools} calls with what was sent and returned`
        : "None made",
      pane: "trace",
    },
    {
      id: "state",
      label: "State changes",
      recorded: !!envState?.twinBacking,
      note: envState?.twinBacking
        ? "Before and after each write"
        : "Not recorded — this environment isn't backed by a cloned service, so state changes aren't captured. Tool calls in Trace show what was sent.",
      pane: "twin",
    },
    takesSnapshot(envState?.agent)
      ? {
          id: "files",
          label: "Files the agent produced",
          recorded: true,
          note: "None produced in this trial",
          pane: null,
        }
      : {
          id: "files",
          label: "Files the agent produced",
          recorded: false,
          note: `Not provided — ${agentName} runs on its own platform and doesn't send files back`,
          pane: null,
        },
  ].filter(Boolean);
};

/** Where a failed check comes from: its rule (when the scenario tests one) and the scenario version it ran. */
export const checkSourceOf = (task, env, envState, run = null) => {
  const scenario =
    (envState?.scenarios || []).find((s) => s.id === task?.id) || task;
  const rule = scenario
    ? ruleOfScenario(scenario, rulesOf(env, envState))
    : null;
  const version =
    run?.scenarioVersions?.[task?.id] ||
    (scenario ? scenarioVersionOf(scenario) : null);
  return { rule, version };
};

const disputeKey = (runId, taskId) => `${runId || "run"}:${taskId}`;

export const disputesOf = (envState, runId, taskId) =>
  envState?.disputes?.[disputeKey(runId, taskId)] || [];

/** The patch that adds a dispute note — the score it disputes stays as recorded. */
export const addDisputePatch = (envState, runId, taskId, note, score) => ({
  disputes: {
    ...(envState?.disputes || {}),
    [disputeKey(runId, taskId)]: [
      ...disputesOf(envState, runId, taskId),
      { note, by: currentUser().name, at: new Date().toISOString(), score },
    ],
  },
});

/**
 * Whether fresh state was restored for this trial (world.md, Reset check).
 * An interrupted trial never got its reset, and a small share of the rest
 * meets leftover state — drawn by hash, so the same trial always reads the
 * same. Leftover state means repeatability is not claimed for the result.
 */
export const resetCheckOf = (task) => {
  const interrupted = !!(
    task?.fault?.transport ||
    task?.turnLimit ||
    ["interrupted", "stopped", "cancelled"].includes(task?.status)
  );
  const leftover = interrupted || hashSeed(`${task?.id}:reset`) % 12 === 0;
  return leftover
    ? {
        clean: false,
        line: "Reset · leftover state from an earlier trial — repeatability not claimed",
        note: interrupted
          ? "This trial was interrupted before the reset finished, so the next one can't be called a clean re-run."
          : "The reset left rows from an earlier trial behind, so this result can't be repeated from the seed.",
      }
    : {
        clean: true,
        line: "Reset · fresh state restored",
        note: "This trial started from the environment's seed data, never another trial's end state.",
      };
};

/**
 * What became of one tool call (world.md, State diff): a read, a write that
 * committed, one that was attempted but never landed (failed or timed out),
 * or one the world denied (a rule or a permission). A write whose answer was
 * lost still committed. `committed` on the call, when the run set it, wins.
 */
export const callOutcomeOf = (call) => {
  if (!call?.wrote)
    return { id: "read", label: "Read", note: "read only", answered: true };
  const kind = call.fault?.kind || call.status;
  const retried = call.fault?.outcome === "retried";
  if (kind === "permission")
    return {
      id: "denied",
      label: "Denied",
      note: "not committed — blocked by a rule or permission",
      answered: false,
    };
  if (kind === "after_commit" || call.committed === true)
    return {
      id: "committed",
      label: "Committed",
      note: "wrote to the world, but the response was lost",
      answered: false,
    };
  if (
    call.committed === false ||
    ["timeout", "error", "rate_limited", "malformed", "missing"].includes(kind)
  )
    return retried
      ? {
          id: "committed",
          label: "Committed",
          note: "wrote to the world on retry",
          answered: true,
        }
      : {
          id: "attempted",
          label: "Attempted",
          note: "not committed — failed or timed out",
          answered: false,
        };
  return {
    id: "committed",
    label: "Committed",
    note: "wrote to the world",
    answered: true,
  };
};

const singular = (t) => String(t).replace(/s$/, "");

/**
 * The records this trial changed, derived from its write tool calls against
 * the environment's seeded tables — `{ evidence, changes, notCommitted }`.
 * `evidence` is false only when the trial made no tool calls at all.
 * `changes`: [{ id, tool, table, record, field, before, after, lost }],
 * `notCommitted`: [{ id, tool, outcome }] — attempted and denied writes.
 */
export const worldChangesOf = (task, env, envState) => {
  const tables = [
    ...(env?.seed?.tables || []),
    ...(envState?.dataImports || []),
  ].map((t) => t.name);
  const calls = task?.callLog?.calls || [];
  /* A step that is itself a write tool (an ops or API run) with no logged call. */
  const stepWrites = (task?.steps || []).filter(
    (s) =>
      s.tool &&
      sideEffectOf({ name: s.tool }) === "write" &&
      !calls.some((c) => c.name === s.tool),
  );
  const evidence =
    calls.length > 0 || (task?.steps || []).some((s) => s.tool || s.action);
  const changes = [];
  const notCommitted = [];
  const record = (id, name, args, call) => {
    const table =
      toolTablesOf(name, "write", tables).changes[0] ||
      `${name.split("_").pop()}s`;
    const change = toolChangeOf(name);
    changes.push({
      id,
      tool: name,
      table,
      record:
        args?.id ||
        args?.order_id ||
        `${singular(table)}-${1000 + (hashSeed(id) % 9000)}`,
      ...change,
      lost: call ? !callOutcomeOf(call).answered : false,
    });
  };
  calls.forEach((c) => {
    const outcome = callOutcomeOf(c);
    if (outcome.id === "committed") record(c.id, c.name, c.args, c);
    else if (outcome.id !== "read")
      notCommitted.push({ id: c.id, tool: c.name, outcome });
  });
  stepWrites.forEach((s, i) =>
    record(`${task.id}-step-${i}`, s.tool, s.args, null),
  );
  /* world.md §5: a successful action in one system isn't enough when a
     second one disagrees — a refund paid while its ticket stays open. */
  const ticketTable = tables.find((t) => /ticket|case/i.test(t));
  const money = changes.find(
    (c) =>
      !c.lost && /refund|payment|charge|credit/i.test(`${c.tool} ${c.table}`),
  );
  const mismatches =
    money && ticketTable && !changes.some((c) => c.table === ticketTable)
      ? [
          {
            id: `${money.id}-mismatch`,
            line: `${money.table} shows ${money.record} done, but ${ticketTable} still has it open — the two systems disagree.`,
          },
        ]
      : [];
  return { evidence, changes, notCommitted, mismatches };
};
