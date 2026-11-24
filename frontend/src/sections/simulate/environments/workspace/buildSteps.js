import {
  canceledProgress,
  stages,
} from "src/pages/dashboard/harness/harnessShared";
import { failureHeadline } from "../buildEnvironment/failureWords";
import { BUILD_STOPPED_COPY } from "./workspace.constants";
import { isConnectOnly, worldTableCount } from "./world/world.constants";

export const BUILD_STEP_TOTAL = 12;

const AGENT_PHASE = {
  id: "agent",
  label: "Reading the agent",
  short: "the start",
};

const BUILD_PHASES = [
  { id: "world", label: "Writing the world", short: "the world" },
  {
    id: "scenarios",
    label: "Writing and checking scenarios",
    short: "the scenarios",
  },
  { id: "checks", label: "Running the release checks", short: "the checks" },
];

const buildPhases = (readsAgent) =>
  readsAgent ? [AGENT_PHASE, ...BUILD_PHASES] : BUILD_PHASES;

const BUILD_STEPS = [
  {
    no: 1,
    phase: "agent",
    stage: "understanding_agent",
    label: "Understanding agent",
  },
  {
    no: 2,
    phase: "world",
    stage: "generating_environment",
    label: "Generating environment",
  },
  {
    no: 3,
    phase: "world",
    stage: "building_environment",
    label: "Building environment",
  },
  { no: 4, phase: "world", stage: "generating_data", label: "Generating data" },
  {
    no: 5,
    phase: "scenarios",
    stage: "generating_scenarios",
    label: "Generating scenarios",
  },
  {
    no: 6,
    phase: "scenarios",
    stage: "validating_environment",
    label: "Validating environment",
  },
  {
    no: 7,
    phase: "scenarios",
    stage: "validating_scenarios",
    label: "Validating scenarios",
  },
  {
    no: 8,
    phase: "checks",
    stage: "connecting_agent",
    label: "Connecting agent",
  },
  { no: 9, phase: "checks", stage: "running", label: "Running" },
  { no: 10, phase: "checks", stage: "grading", label: "Grading" },
  {
    no: 11,
    phase: "checks",
    stage: "uploading_artifacts",
    label: "Uploading artifacts",
  },
  { no: 12, phase: "checks", stage: "cleaning_up", label: "Cleaning up" },
];

const BEFORE_FIRST_STEP = new Set([
  "queued",
  "admitted",
  "received",
  "acquiring_source",
]);

const STAGE_ALIAS = { finalizing: "uploading_artifacts" };

export function stepOfStage(stage, { readsAgent = true } = {}) {
  const named = STAGE_ALIAS[stage] || stage;
  const step = BEFORE_FIRST_STEP.has(named)
    ? BUILD_STEPS[0]
    : BUILD_STEPS.find((one) => one.stage === named) || null;
  return step && !readsAgent && step.no < 2 ? BUILD_STEPS[1] : step;
}

export const phaseOfStep = (step) =>
  [AGENT_PHASE, ...BUILD_PHASES].find((phase) => phase.id === step?.phase) ||
  null;

const stepNo = (no) => BUILD_STEPS.find((step) => step.no === no);

function producedStep(outputs) {
  const kinds = new Set((outputs || []).map((output) => output?.kind));
  if (kinds.has("scenarios")) return stepNo(8);
  if (kinds.has("environment")) return stepNo(5);
  if (kinds.has("contract")) return stepNo(2);
  return null;
}

export function stoppedStep(status, { events, outputs, designApproved } = {}) {
  let step = null;
  if (status?.stage === "failed") step = stepOfStage(status.failure?.stage);
  else if (status?.stage === "canceled") {
    const { stoppedAt } = canceledProgress(events || []);
    step = stoppedAt >= 0 ? stepOfStage(stages[stoppedAt]) : null;
  } else return null;
  step = step || producedStep(outputs) || BUILD_STEPS[0];
  return stepOfStage(step.stage, { readsAgent: !designApproved });
}

const plural = (n, one, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

const madeNote = (phaseId, counts) => {
  if (phaseId === "world")
    return [
      counts.tools ? plural(counts.tools, "tool") : null,
      counts.tables ? plural(counts.tables, "table") : null,
    ]
      .filter(Boolean)
      .join(" · ");
  if (phaseId === "scenarios")
    return counts.scenarios
      ? `${plural(counts.scenarios, "scenario")}, all three checks passed`
      : "";
  return "";
};

const rangeOf = (steps) =>
  steps.length > 1
    ? `Steps ${steps[0].no}–${steps[steps.length - 1].no}`
    : `Step ${steps[0].no}`;

export function phaseRows(
  step,
  { stopped = false, readsAgent = false, counts = {} } = {},
) {
  const phases = buildPhases(readsAgent || step.phase === "agent");
  const at = phases.findIndex((phase) => phase.id === step.phase);
  return phases.map((phase, index) => {
    const steps = BUILD_STEPS.filter((one) => one.phase === phase.id);
    const base = { id: phase.id, label: phase.label, range: rangeOf(steps) };
    if (index < at) {
      const made = madeNote(phase.id, counts);
      const word = stopped ? "Kept" : "Done";
      return {
        ...base,
        state: "done",
        note: made ? `${word} · ${made}` : word,
      };
    }
    if (index === at) {
      const before = steps.filter((one) => one.no < step.no).length;
      const progress = before
        ? ` · ${before} of ${steps.length} steps done`
        : "";
      return {
        ...base,
        state: stopped ? "stopped" : "now",
        note: `${stopped ? "Stopped at" : "Now:"} ${step.label}${progress}`,
      };
    }
    return {
      ...base,
      state: "next",
      note: stopped ? "Not run" : "Not started",
    };
  });
}

export function keptLine(step, counts = {}, designApproved = false) {
  const done = (phaseId) =>
    BUILD_STEPS.filter((one) => one.phase === phaseId).every(
      (one) => one.no < step.no,
    );
  const parts = [];
  if (done("world"))
    parts.push(
      counts.tables
        ? `The world and its ${plural(counts.tables, "table")} are kept`
        : counts.tools
          ? `The world and its ${plural(counts.tools, "tool")} are kept`
          : "The world is kept",
    );
  else if (designApproved) parts.push("Your design is kept");
  if (done("scenarios") && counts.scenarios)
    parts.push(`its ${plural(counts.scenarios, "scenario")} too`);
  return parts.length ? `${parts.join(", and ")}.` : "";
}

export const spentLine = (credits) =>
  typeof credits === "number" && credits > 0
    ? `Spent ${plural(Math.round(credits), "credit")} before it stopped.`
    : "";

const TAB_WRITTEN_BY = {
  contract: "agent",
  world: "world",
  actors: "scenarios",
  scenarios: "scenarios",
  evals: "scenarios",
  versions: "checks",
};

const OPENS_WHEN_LANDED = new Set(["versions"]);

const WORLD_SEALED_STEP = 6;

export const landedOf = (env, step, { versions = false } = {}) => ({
  contract: Boolean(env?.contractReady),
  world:
    isConnectOnly(env) ||
    worldTableCount(env) > 0 ||
    (step?.no || 0) > WORLD_SEALED_STEP,
  versions: Boolean(versions),
});

export function tabWaits(
  step,
  { stopped = false, reason = null, landed = {} } = {},
) {
  if (!step) return {};
  const waits = {};
  Object.entries(TAB_WRITTEN_BY).forEach(([tab, phaseId]) => {
    const steps = BUILD_STEPS.filter((one) => one.phase === phaseId);
    const written = steps.every((one) => one.no < step.no);
    if (OPENS_WHEN_LANDED.has(tab) && landed[tab] !== false) return;
    if (written && landed[tab] !== false) return;
    const phase = phaseOfStep(written ? step : steps[0]);
    waits[tab] = {
      phase: phase.label,
      now: !stopped && (written || step.phase === phaseId),
      stopped,
      step: step.label,
      reason: stopped ? reason : null,
    };
  });
  return waits;
}

export function stopCase(status, step) {
  if (status?.stage === "canceled") return "canceled";
  const failure = status?.failure || {};
  if (failure.domain === "infrastructure") return "ours";
  if (step.phase === "checks")
    return step.stage === "connecting_agent" || failure.domain === "agent"
      ? "checks"
      : "checksOther";
  if (step.phase === "scenarios") return "scenarios";
  return "world";
}

const REJECTED_TOOL = /certification rejected:\s*([A-Za-z0-9_.-]+)/;

const rejectedTool = (failure) =>
  REJECTED_TOOL.exec(failure?.message || "")?.[1] || null;

export function stopSentence(status, step) {
  const kind = stopCase(status, step);
  const words = BUILD_STOPPED_COPY.sentence;
  if (kind === "checks")
    return failureHeadline(status?.failure) || words.checks;
  if (
    (kind === "world" || kind === "scenarios") &&
    step.stage === "validating_environment"
  ) {
    const tool = rejectedTool(status?.failure);
    return tool ? words.toolUntruthful(tool) : words.worldCheck;
  }
  return words[kind];
}
