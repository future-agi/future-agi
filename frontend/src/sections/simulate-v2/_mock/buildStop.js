/*
  A first build that stopped part-way. The build engine numbers its steps
  2–12 (step 1, Understanding the agent, wrote the contract before Approve)
  and the page shows them as three phases. A build can stop in any of them:
  the phases it finished are kept, the retry resumes from the step that
  stopped, and one sentence says what stopped and why — never the engine's
  own message, which stays behind Show details.

  Demo: ?build=fail-world | fail-scenarios | fail-checks | fail-ours on the
  environment page (EnvironmentWorkspace) stops the first build there.
*/
import {
  BUILD_PHASES,
  BUILD_STEP_TOTAL,
  firstBuildOf,
  phaseOfStep,
  stopPointOf,
  versionBuildProgress,
} from "./versionBuild";
import { PROVIDER_LABELS } from "./agentRegistry";

/* Where each demo case stops, by engine step. */
export const BUILD_STOP_CASES = {
  "fail-world": 4,
  "fail-scenarios": 7,
  "fail-checks": 8,
  "fail-ours": 11,
};
export const isBuildStopCase = (c) => !!BUILD_STOP_CASES[c];

const plural = (n, one, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** True once a first build has stopped (said by the build strip when it does). */
export const isBuildStopped = (build) => !!build?.first && !!build?.stoppedAt;

/** The step the build stopped at, else null. */
export const stoppedStepOf = (build) => {
  if (!isBuildStopped(build)) return null;
  const at = stopPointOf(build);
  return at ? build.steps[at.index] : null;
};

const READ_TOOL = /^(lookup|get|find|search|fetch|read|list|check)_/;

/* The words of a tool's subject: lookup_order → "order"; a tool that
   doesn't read one record returns "a record". */
const nounOf = (tool) =>
  READ_TOOL.test(String(tool || ""))
    ? String(tool).replace(READ_TOOL, "").replace(/_/g, " ")
    : "record";

/* A tool that reads one record — the kind that can invent one. */
const readToolOf = (tools) =>
  tools.find((t) => /^(lookup|get|find|fetch|read)_/.test(t)) ||
  tools.find((t) => READ_TOOL.test(t)) ||
  tools[0] ||
  "lookup_order";

/* Where the agent is reached — the endpoint, number or URL it was connected at. */
export const agentWhereOf = (agent) => {
  const v = agent?.values || {};
  const where =
    v.endpoint ||
    v.sdkEndpoint ||
    v.mcpUrl ||
    v.phoneNumber ||
    agent?.location ||
    null;
  if (where) return where;
  /* A code agent is started in the sandbox: reached on the port it runs on. */
  const def = agent?.definition || {};
  const port = /--port[ =](\d+)/.exec(def.runCommand || "")?.[1];
  if (port) return `port ${port} in the sandbox`;
  if (def.repoUrl || v.repoUrl) return def.repoUrl || v.repoUrl;
  /* A hosted platform agent: its provider and assistant id. */
  if (v.provider && v.agentId)
    return `${PROVIDER_LABELS[v.provider] || v.provider} agent ${v.agentId}`;
  return null;
};

/* What a step costs, for "Spent N credits before it stopped". */
const creditsOf = (step, nScn) => {
  switch (step.no) {
    case 2:
      return 24;
    case 3:
      return step.total * 6;
    case 4:
      return step.total * 2;
    case 5:
      return Math.ceil(step.total / 40);
    case 6:
      return nScn * 6;
    case 7:
      return nScn * 3;
    case 9:
      return nScn * 4;
    case 10:
      return nScn * 2;
    default:
      return 2;
  }
};

/**
 * What a stopped build shows: null unless it stopped. `scenarios` are the
 * environment's rows (for the titles a scenario stop names), `agent` its
 * connected agent (for where step 8 couldn't reach).
 */
export function buildStopOf(build, { scenarios = [], agent = null } = {}) {
  if (!isBuildStopped(build)) return null;
  const at = stopPointOf(build);
  if (!at) return null;
  const step = build.steps[at.index];
  const phaseId = phaseOfStep(step);
  const phaseAt = BUILD_PHASES.findIndex((p) => p.id === phaseId);
  const phase = BUILD_PHASES[phaseAt];
  const c = build.changes || {};
  const tools = c.tools || [];
  const tables = c.tables || [];
  const rows = tables.reduce((n, t) => n + (t.rows || 0), 0);
  const ids = c.scenarios || [];
  const nScn = ids.length || build.scenarios || 0;
  const titleOf = (id) => {
    const r = scenarios.find((s) => s.id === id);
    return r?.title || r?.name || null;
  };
  const kase = build.stop?.case || "fail-ours";
  const fixed = !!build.stop?.fixedAt;

  /* Spent: every step it finished, and part of the one that stopped. */
  const spent = Math.round(
    build.steps.slice(0, at.index).reduce((n, s) => n + creditsOf(s, nScn), 0) +
      creditsOf(step, nScn) * 0.45,
  );

  /* The one sentence: what stopped and why, in the person's words. */
  let sentence;
  let fix = null;
  let log;
  const where = `${step.label} (step ${step.no} of ${BUILD_STEP_TOTAL})`;
  if (kase === "fail-world") {
    const tool = readToolOf(tools);
    const noun = nounOf(tool);
    const aNoun = `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;
    sentence = `${tool} didn't answer truthfully: it returned ${aNoun} that isn't in the world.`;
    fix = {
      kind: "builder",
      label: "Fix with the builder",
      chip: `Build stopped · ${step.label}`,
      context: `The build stopped at ${step.label}: ${tool} returned ${aNoun} that isn't in the world`,
      draft: `Make ${tool} answer only from the world, and refuse when there's no match`,
      reply: [
        `${tool} made up ${aNoun} when the id wasn't in the world. It now reads only the world's ${noun}s and refuses with "not found" otherwise.`,
        {
          ok: `Rewrote ${tool}`,
          sub: "Answers from the world, refuses when there's no match",
        },
        {
          ok: "Probed it again",
          sub: `15 of 15 answered truthfully, including the refusal`,
        },
      ],
    };
    log = [
      `step 4/12 validate_environment`,
      `probe 12/15  ${tool}({"id": "X-90412"})`,
      `  expect  refusal  (X-90412 not in world.${noun.replace(/ /g, "_")}s)`,
      `  got     200 {"id": "X-90412", "status": "shipped"}`,
      `truthfulness_check FAILED  fabricated_row  handler=${tool}`,
      `14 of 15 probes passed · halted before generate_data`,
    ];
  } else if (kase === "fail-scenarios") {
    const names = ids.slice(-2).map(titleOf).filter(Boolean);
    const shown =
      names.length === 2
        ? names
        : ["Refund over the limit", "Change address after dispatch"];
    sentence = `2 use cases got no scenario that passed all three checks (${shown.join(", ")}).`;
    fix = {
      kind: "builder",
      label: "Fix with the builder",
      chip: `Build stopped · ${step.label}`,
      context: `The build stopped at ${step.label}: 2 use cases got no scenario that passed all three checks (${shown.join(", ")})`,
      draft: "Rewrite the use cases that got no scenario",
      reply: [
        `Both use cases asked for something the world can't settle, so every scenario for them was vacuous. I rewrote them so the end state can be checked.`,
        {
          ok: `Rewrote "${shown[0]}"`,
          sub: "Its end state is now one the checks can see",
        },
        {
          ok: `Rewrote "${shown[1]}"`,
          sub: "Its end state is now one the checks can see",
        },
      ],
    };
    log = [
      `step 7/12 validate_scenarios`,
      `gates: ready · solvable · not_vacuous`,
      `use_case "${shown[0]}"  3 candidates  ready ✓  solvable ✓  not_vacuous ✗ (end state unchanged by any action)`,
      `use_case "${shown[1]}"  3 candidates  ready ✓  solvable ✗ (no path reaches status=address_changed)`,
      `kept ${Math.max(0, nScn - 2)} of ${nScn} · 2 use cases uncovered · halted before connect_agent`,
    ];
  } else if (kase === "fail-checks") {
    const whereAgent = agentWhereOf(agent);
    const portNo = /^port (\d+)/.exec(whereAgent || "")?.[1];
    const target = portNo
      ? `http://127.0.0.1:${portNo}`
      : whereAgent || "<agent endpoint>";
    sentence = whereAgent
      ? `Couldn't reach your agent at ${whereAgent}.`
      : "Couldn't reach your agent.";
    fix = { kind: "connection", label: "Check the connection" };
    log = [
      `step 8/12 connect_agent`,
      `POST ${target}  attempt 1/3  timeout after 30s`,
      `POST ${target}  attempt 2/3  timeout after 30s`,
      `POST ${target}  attempt 3/3  ECONNREFUSED`,
      `halted before run · sandbox released`,
    ];
  } else {
    sentence = "An issue occurred while building. Nothing you set up was lost.";
    log = [
      `step 11/12 upload_artifacts`,
      `PUT artifacts/run-bundle.tar.zst  503 Service Unavailable  (3 retries)`,
      `object store unavailable · halted before cleanup`,
    ];
  }

  /* What's kept: the phases it finished, said with what they made. */
  const doneSteps = build.steps.slice(0, at.index);
  const did = (no) => doneSteps.some((s) => s.no === no);
  const keptParts = [];
  if (did(5))
    keptParts.push(
      tables.length
        ? `The world and its ${plural(tables.length, "table")} are kept`
        : `The world and its ${plural(tools.length, "tool")} are kept`,
    );
  else if (did(3))
    keptParts.push(
      `Your design and the ${plural(tools.length, "tool")} built so far are kept`,
    );
  else keptParts.push("Your design is kept");
  if (did(7)) keptParts.push(`its ${plural(nScn, "scenario")} too`);
  else if (did(6) && step.no === 7)
    keptParts.push(`${Math.max(0, nScn - 2)} of ${nScn} scenarios passed`);
  const kept = `${keptParts.join(", and ")}.`;

  /* Each phase: done (kept), the one that stopped, or not run. */
  const phases = BUILD_PHASES.map((p, i) => {
    const steps = build.steps.filter((s) => phaseOfStep(s) === p.id);
    const nos = steps.map((s) => s.no);
    const range = nos.length ? `${Math.min(...nos)}–${Math.max(...nos)}` : "";
    if (i < phaseAt) {
      const note =
        p.id === "world"
          ? [
              plural(tools.length, "tool"),
              tables.length &&
                `${plural(tables.length, "table")}, ${rows.toLocaleString()} rows`,
            ]
              .filter(Boolean)
              .join(" · ")
          : p.id === "scenarios"
            ? `${plural(nScn, "scenario")}, all three checks passed`
            : "";
      return { id: p.id, label: p.label, range, state: "done", note };
    }
    if (i === phaseAt) {
      const before = steps.filter((s) => s.no < step.no).length;
      return {
        id: p.id,
        label: p.label,
        range,
        state: "stopped",
        note: `Stopped at ${step.label} · ${before} of ${steps.length} steps done`,
      };
    }
    return { id: p.id, label: p.label, range, state: "next", note: "Not run" };
  });

  return {
    case: kase,
    step,
    phase,
    where,
    sentence,
    kept,
    spent: `Spent ${plural(spent, "credit")} before it stopped.`,
    phases,
    retry: {
      label: `Try again from ${phase.short}`,
      tip: `Resumes at ${step.label}, step ${step.no} of ${BUILD_STEP_TOTAL}. What's finished is kept.`,
    },
    /* Once the builder made its change, the way on is the retry alone. */
    fix: fixed ? null : fix,
    fixed,
    log: log.join("\n"),
  };
}

/** The reason Run simulation is off while a build is stopped. */
export const stoppedRunReason = (build) => {
  const step = stoppedStepOf(build);
  return step
    ? `The build stopped at ${step.label} — try again to finish it before running`
    : "";
};

/**
 * A first build that stops at `kase` (?build=fail-*), already stopped: the
 * environment's own world and scenarios, timed so it has just reached the
 * step it stops at. Shown on any environment for the demo.
 */
export function stoppedBuildOf({
  env,
  envState,
  kase,
  tables = null,
  now = Date.now(),
}) {
  const no = BUILD_STOP_CASES[kase];
  if (!no) return null;
  const build = firstBuildOf({
    tables: tables || env?.seed?.tables || [],
    tools: env?.tools || [],
    rules: (envState?.ruleChanges || [])
      .filter((r) => r.kind === "added")
      .map((r) => r.id),
    scenarios: (envState?.scenarios || [])
      .filter((r) => !r.proposed && !r.archived)
      .map((r) => r.id),
    stop: { case: kase, no },
  });
  const at = stopPointOf(build);
  return { ...build, at: now - at.atMs - 1, stoppedAt: now };
}

/**
 * Try again: the same build, resumed from the step that stopped — what it
 * finished is kept, so the clock starts at that step and nothing stops it.
 */
export function resumedBuildOf(build, now = Date.now()) {
  const at = stopPointOf(build);
  if (!at) return build;
  const before = build.steps
    .slice(0, at.index)
    .reduce((n, s) => n + (s.ms || 3500), 0);
  const { stop, stoppedAt, ...rest } = build; // eslint-disable-line no-unused-vars
  return {
    ...rest,
    stop: null,
    at: now - before,
    resumedFrom: stop?.no || null,
  };
}

/** True when the build's clock has reached its stop and the stop isn't stored yet. */
export const reachedStop = (build, now = Date.now()) =>
  !!build?.stop &&
  !build?.stoppedAt &&
  !!versionBuildProgress(build, now)?.stopped;
