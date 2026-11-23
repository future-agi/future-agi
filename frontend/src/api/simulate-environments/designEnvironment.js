import { designSummary } from "src/sections/simulate/environments/design/design";
import { turnsOf } from "src/sections/simulate/environments/design/designTurns";
import { useDesignDraftStore } from "src/sections/simulate/environments/design/useDesignDraftStore";
import {
  BUILD_STATUS,
  ENV_STATUS,
} from "src/sections/simulate/environments/myEnvironments.constants";
import { designBuildOf } from "./designBuild";

// Stub of the environment an approved design builds, until the design job
// creates a real one: the workspace opens a draft's id as an
// environment, read from the design draft store in this browser. The fetchers
// in versions.js and draftReview.js answer from here for these ids, so nothing
// is sent. See UI_ONLY_FEATURES.designBuild and connectAgent.

/** Whether an environment id is a design draft's, not a real environment. */
export const isDesignEnvId = (id) =>
  typeof id === "string" && id.startsWith("design-");

const draftOf = (envId) => useDesignDraftStore.getState().drafts[envId];

// The parts each build stage writes, shown in the tabs once it's done.
const PART_STAGE = {
  records: "records",
  tools: "tools",
  rules: "checks",
  scenarios: "scenarios",
};

const scenarioRowsOf = (draft, scenarios) =>
  scenarios.map((s, i) => ({
    id: `${draft.id}-s${i + 1}`,
    name: s.text,
    title: s.text,
    task: s.text,
    situation: s.text,
    useCase: s.src,
    expected: "",
    persona: null,
  }));

/**
 * The workspace's view of a design draft at `now`: { env, envState, build },
 * or null for an unknown draft. Each part appears as its build stage finishes;
 * envState is null until the scenarios are written, since the workspace takes
 * it once.
 */
export function designEnvironmentOf(draft, now = Date.now()) {
  if (!draft) return null;
  const build = designBuildOf(draft, now);
  const summary = designSummary({ ...draft, readUntil: 0 }, now);
  const done = (part) =>
    build?.stages.find((s) => s.id === PART_STAGE[part])?.status === "done";
  const agent = draft.connectedAgent || draft.agent;
  const voice = summary.brief.channel === "Voice";
  const env = {
    id: draft.id,
    name: draft.name,
    surface: voice ? "voice" : "chat",
    agentType: voice ? "voice" : "text",
    description: draft.description,
    tagline: "Built from your design",
    status: ENV_STATUS.READY,
    buildStatus: BUILD_STATUS.READY,
    platform: {},
    versioning: { enabled: true, nudges_enabled: false },
    seed: {
      tables: done("records")
        ? summary.records.map(({ name, rows, note }) => ({ name, rows, note }))
        : [],
    },
    // World & tools reads the tables from here, as the environment detail sends them.
    worldTables: done("records")
      ? summary.records.map(({ name, rows, note }) => ({
          name,
          rows,
          note,
          fields: [],
        }))
      : [],
    worldTotalRows: done("records")
      ? summary.records.reduce((n, t) => n + t.rows, 0)
      : 0,
    tools: done("tools")
      ? summary.tools.map(({ name, desc }) => ({ name, desc }))
      : [],
    rules: done("rules") ? summary.rules.map((r) => r.text) : [],
    testSubject: agent
      ? { id: agent.id, name: agent.name, active_version: agent.version }
      : null,
    designBuild: build,
    // The header pill while the build runs; Ready once its checks pass.
    buildingLabel: ["building", "validating"].includes(build?.status)
      ? "Building v1"
      : undefined,
    // What the design will make, counted before the scenarios are written.
    designScenarioCount: summary.scenarios?.length ?? 0,
    // The design's builder chat, read only, with how the build went.
    designChat: turnsOf(draft, summary, build),
  };
  const envState = done("scenarios")
    ? {
        agent: null,
        scenarios: scenarioRowsOf(draft, summary.scenarios),
        scenarioSource: "design",
      }
    : null;
  return { env, envState, build, summary };
}

/** The scenarios the build wrote, as the scenarios list API's rows. */
export function designScenarioRowsOf(envId) {
  const view = designEnvironmentOf(draftOf(envId));
  return (view?.envState?.scenarios || []).map((s, i) => ({
    id: s.id,
    scenario_key: s.id,
    number: i + 1,
    name: s.name,
    instruction: s.task,
    use_case: s.useCase,
    tests: "",
    branch: s.useCase,
    sub_goals: [],
    actors: [],
    keywords: [],
    faults: [],
    persona: null,
    coverage: { task: s.useCase, overlay: "none" },
  }));
}

const iso = (ms) => new Date(ms).toISOString();

// The versions list (GET .../versions/): v1 once saved.
export function designVersionsOf(envId) {
  const draft = draftOf(envId);
  const release = draft?.release;
  const agent = draft?.connectedAgent || draft?.agent;
  const agentRows = agent
    ? [
        {
          id: `${envId}-agent-${agent.id}`,
          label: agent.version || "v1",
          status: "active",
          is_default: true,
          pickable: true,
          summary: agent.name,
        },
      ]
    : [];
  if (!release) return { agent: agentRows, environment: [], suite: [] };
  const view = designEnvironmentOf(draft);
  const row = (piece, extra = {}) => ({
    id: `${envId}-${piece}-v1`,
    label: "v1",
    status: "active",
    is_default: true,
    created_at: iso(release.savedAt),
    origin: "build",
    summary: release.name || "Built from the approved design",
    ...extra,
  });
  return {
    agent: agentRows,
    environment: [row("environment")],
    suite: [
      row("suite", { scenario_count: view.envState?.scenarios.length ?? 0 }),
    ],
  };
}

// The open drafts (GET .../drafts/): the checked build until it's saved.
export function designDraftsOf(envId) {
  const draft = draftOf(envId);
  if (designBuildOf(draft)?.status !== "ready") return {};
  const row = (piece) => ({
    id: `${envId}-${piece}-draft`,
    label: null,
    status: "draft",
    revision: 1,
    parent_id: null,
    updated_at: iso(draft.build.startedAt),
  });
  return { environment: row("environment"), suite: row("suite") };
}

const change = (fields) => ({
  decision: null,
  fields: [],
  patch: [],
  affects: [],
  sources: [
    {
      origin: "chat",
      agent: { kind: "harness" },
      at: new Date().toISOString(),
      draft_revision: 1,
      note: "From the approved design",
    },
  ],
  depends_on: [],
  graded: false,
  revalidate: [],
  base_digest: null,
  draft_digest: null,
  action: "added",
  piece: "environment",
  ...fields,
});

/**
 * What the build made, as a draft review shaped like GET .../drafts/review/,
 * every part waiting for Accept or Reject. Empty once saved.
 */
export function designReviewOf(envId) {
  const draft = draftOf(envId);
  const view = designEnvironmentOf(draft);
  const ready = view?.build?.status === "ready";
  const { env, envState, summary } = view || {};
  const changes = ready
    ? [
        ...env.seed.tables.map((t) =>
          change({
            id: `world_state:${t.name}`,
            kind: "world_state",
            key: `world/seed/${t.name}.json`,
            document: `world/seed/${t.name}.json`,
            summary: `Table ${t.name} · ${t.rows.toLocaleString()} rows${t.note ? `, ${t.note}` : ""}`,
            rows: [{ table: t.name, added: t.rows, removed: 0, changed: 0 }],
            affects: "all",
          }),
        ),
        ...env.tools.map((t) =>
          change({
            id: `tool:${t.name}`,
            kind: "tool",
            key: t.name,
            document: "tools.json",
            summary: `Tool ${t.name} added: ${t.desc}`,
          }),
        ),
        ...env.rules.map((text, i) =>
          change({
            id: `rule:r${i + 1}`,
            kind: "rule",
            key: `r${i + 1}`,
            document: "contract.json",
            summary: `Rule R${i + 1} added: ${text}`,
            affects: "all",
          }),
        ),
        ...summary.people.map((p) =>
          change({
            id: `actor:${p.name}`,
            kind: "actor",
            key: p.name,
            document: "actors.json",
            summary: `Actor ${p.name} added${p.note ? `: ${p.note}` : ""}`,
          }),
        ),
        ...envState.scenarios.map((s) =>
          change({
            id: `scenario:${s.id}`,
            piece: "suite",
            kind: "scenario",
            key: s.id,
            document: "scenarios.json",
            summary: `Scenario added: ${s.name}`,
            affects: [s.name],
          }),
        ),
      ]
    : [];
  return {
    drafts: ready
      ? {
          environment: {
            id: `${envId}-environment-draft`,
            revision: 1,
            parent_id: "",
          },
          suite: { id: `${envId}-suite-draft`, revision: 1, parent_id: "" },
        }
      : {},
    changes,
    failed: [],
    counts: {
      total: changes.length,
      accepted: 0,
      rejected: 0,
      undecided: changes.length,
    },
    review_digest: `design:${envId}`,
    can_save: false,
    sample_scenario_total: envState?.scenarios.length ?? 0,
  };
}

/** The agent picked on a saved design's environment, kept with the draft. */
export const connectDesignEnvAgent = (envId, agent) =>
  useDesignDraftStore.getState().connectDesignAgent(envId, {
    id: agent.id,
    name: agent.name,
    version: agent.latestLabel || null,
  });

// Writes a design's environment can't take until it's on the server.
export const designNotSaved = () =>
  Object.assign(
    new Error("UI only: this environment isn't on the server yet."),
    {
      statusCode: 409,
      error: "design_not_on_server",
    },
  );

/** Save (POST .../drafts/save/): the build becomes v1. Answers like the API. */
export function saveDesignDrafts(envId, name = "") {
  useDesignDraftStore.getState().saveDesignRelease(envId, name);
  return { versions: designVersionsOf(envId) };
}
