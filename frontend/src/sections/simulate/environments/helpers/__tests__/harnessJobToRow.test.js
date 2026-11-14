import { describe, it, expect } from "vitest";
import {
  harnessJobToRow,
  harnessEnvToRow,
  buildStatusFor,
  jobStatusFor,
  envStatusFor,
} from "../harnessJobToRow";
import { ENV_STATUS, BUILD_STATUS } from "../../myEnvironments.constants";

const item = ({ stage, connectors, metadata, jobId = "job-1", updatedAt } = {}) => ({
  job: { job_id: jobId, metadata },
  status: { stage, updated_at: updatedAt },
  credentials: { detected_connectors: connectors },
});

describe("harnessJobToRow", () => {
  it("maps the id, name and updated timestamp from the job/status", () => {
    const row = harnessJobToRow(
      item({
        stage: "completed",
        metadata: { name: "Support Line" },
        updatedAt: "2026-09-15T09:00:00Z",
      }),
    );
    expect(row.id).toBe("job-1");
    expect(row.name).toBe("Support Line");
    expect(row.updatedAt).toBe("2026-09-15T09:00:00Z");
  });

  it.each([
    ["completed", ENV_STATUS.READY],
    ["failed", ENV_STATUS.FAILED],
    ["canceled", ENV_STATUS.CANCELLED],
    ["running", ENV_STATUS.RUNNING],
    ["cleaning_up", ENV_STATUS.FINALIZING],
    ["finalizing", ENV_STATUS.FINALIZING],
    ["queued", ENV_STATUS.BUILDING],
    ["generating_environment", ENV_STATUS.BUILDING],
  ])("maps stage %s to status %s", (stage, status) => {
    expect(harnessJobToRow(item({ stage })).status).toBe(status);
  });

  it("reads voice from a detected voice connector, even alongside http", () => {
    expect(
      harnessJobToRow(item({ connectors: ["http", "vapi"] })).agentType,
    ).toBe("voice");
  });

  it("reads text from a detected non-voice connector", () => {
    expect(harnessJobToRow(item({ connectors: ["http"] })).agentType).toBe(
      "text",
    );
  });

  it("leaves the agent type unidentified (null) when nothing is detected", () => {
    // Nothing detected — we can't tell the modality, so don't misreport it as
    // Chat. The table renders null as "Not identified".
    expect(harnessJobToRow(item({ connectors: [] })).agentType).toBeNull();
  });

  it("supplies null/zero placeholders for the fields the list cannot fill", () => {
    const row = harnessJobToRow(item({ stage: "completed" }));
    expect(row.description).toBeNull();
    expect(row.tools).toBeNull();
    expect(row.scenarios).toBeNull();
    expect(row.subgoals).toBeNull();
    expect(row.buildProgress).toBeNull();
    expect(row.runsTotal).toBe(0);
  });

  it("is null-safe on an empty item", () => {
    const row = harnessJobToRow(undefined);
    expect(row.id).toBeUndefined();
    expect(row.status).toBe(ENV_STATUS.BUILDING);
    expect(row.agentType).toBeNull();
    expect(row.updatedAt).toBeNull();
  });
});

describe("harnessEnvToRow", () => {
  const env = (over = {}) => ({
    id: "env-1",
    name: "Support Line",
    description: "Handles inbound billing calls",
    source_kind: "provider",
    agent_type: "voice",
    status: "completed",
    stage: "completed",
    scenario_count: 12,
    tools_count: 4,
    last_updated: "2026-09-15T09:00:00Z",
    created_at: "2026-09-10T09:00:00Z",
    ...over,
  });

  it("maps the real description, tool and scenario counts and the status pill", () => {
    const row = harnessEnvToRow(env());
    expect(row).toMatchObject({
      id: "env-1",
      name: "Support Line",
      description: "Handles inbound billing calls",
      status: ENV_STATUS.READY,
      agentType: "voice",
      tools: 4,
      scenarios: 12,
      updatedAt: "2026-09-15T09:00:00Z",
    });
  });

  it("maps chat to the text agent type and falls back to created_at", () => {
    const row = harnessEnvToRow(
      env({ agent_type: "chat", last_updated: null }),
    );
    expect(row.agentType).toBe("text");
    expect(row.updatedAt).toBe("2026-09-10T09:00:00Z");
  });

  it("keeps sub-goals, runs and domain null when the backend omits them", () => {
    const row = harnessEnvToRow(env());
    expect(row.subgoals).toBeNull();
    expect(row.runsTotal).toBeNull();
    expect(row.domain).toBeNull();
    expect(row.buildProgress).toBeNull();
  });

  it("reads the §1 domain, sub_goals_count and runs_count once served", () => {
    const row = harnessEnvToRow(
      env({ domain: "billing", sub_goals_count: 3, runs_count: 7 }),
    );
    expect(row.domain).toBe("billing");
    expect(row.subgoals).toBe(3);
    expect(row.runsTotal).toBe(7);
  });

  it("passes a null description and tool count through unchanged", () => {
    const row = harnessEnvToRow(env({ description: null, tools_count: null }));
    expect(row.description).toBeNull();
    expect(row.tools).toBeNull();
  });

  it("is null-safe on an empty item and invents nothing", () => {
    const row = harnessEnvToRow(undefined);
    expect(row.id).toBeUndefined();
    // No fabricated status / agent type — a missing value passes through as
    // undefined rather than a plausible-looking "building" / "chat".
    expect(row.status).toBeUndefined();
    expect(row.agentType).toBeUndefined();
    expect(row.updatedAt).toBeNull();
  });
});

describe("buildStatusFor", () => {
  it("maps completed → ready", () => {
    expect(buildStatusFor("completed")).toBe(BUILD_STATUS.READY);
  });

  it("maps failed and canceled → failed (the bug: they used to read as building)", () => {
    expect(buildStatusFor("failed")).toBe(BUILD_STATUS.FAILED);
    expect(buildStatusFor("canceled")).toBe(BUILD_STATUS.FAILED);
  });

  // "running" is stage 10 of 14 in `stages` — after validating_scenarios and
  // connecting_agent — so by then the world is fully derived and the agent is
  // attached; what is running is the calls. The backend agrees: status_for()
  // reports RUNNING/FINALIZING/CLEANING_UP as "running" and only the states
  // before them as "building". A build spinner here would never stop.
  it("maps running → ready: the environment is built, the calls are running", () => {
    expect(buildStatusFor("running")).toBe(BUILD_STATUS.READY);
  });

  it("maps every stage before the agent connects → building", () => {
    ["queued", "acquiring_source", "generating_environment", "generating_scenarios", undefined].forEach(
      (s) => expect(buildStatusFor(s)).toBe(BUILD_STATUS.BUILDING),
    );
  });
});

describe("jobStatusFor", () => {
  const cancel = "2026-09-25T08:00:00Z";

  it.each([
    ["cleaning_up", cancel, ENV_STATUS.CANCELLING],
    ["running", cancel, ENV_STATUS.CANCELLING],
    ["generating_scenarios", cancel, ENV_STATUS.CANCELLING],
    ["canceled", cancel, ENV_STATUS.CANCELLED],
    ["completed", cancel, ENV_STATUS.READY],
    ["failed", cancel, ENV_STATUS.FAILED],
    ["cleaning_up", null, ENV_STATUS.FINALIZING],
    ["running", null, ENV_STATUS.RUNNING],
  ])("stage %s with cancel_requested_at=%s reads %s", (stage, cancelAt, expected) => {
    expect(jobStatusFor({ stage, cancel_requested_at: cancelAt })).toBe(expected);
  });

  it("drives the jobs-list row, so a cancel in flight reads Cancelling there too", () => {
    const row = harnessJobToRow({
      job: { job_id: "job-1" },
      status: { stage: "cleaning_up", cancel_requested_at: cancel },
    });
    expect(row.status).toBe(ENV_STATUS.CANCELLING);
  });
});

describe("envStatusFor", () => {
  it.each([
    ["cleaning_up", ENV_STATUS.RUNNING, ENV_STATUS.FINALIZING],
    ["finalizing", ENV_STATUS.RUNNING, ENV_STATUS.FINALIZING],
    ["canceled", ENV_STATUS.FAILED, ENV_STATUS.CANCELLED],
    ["running", ENV_STATUS.RUNNING, ENV_STATUS.RUNNING],
    ["failed", ENV_STATUS.FAILED, ENV_STATUS.FAILED],
    ["generating_scenarios", ENV_STATUS.BUILDING, ENV_STATUS.BUILDING],
    ["completed", "completed", ENV_STATUS.READY],
  ])("stage %s with backend status %s reads %s", (stage, status, expected) => {
    expect(envStatusFor(stage, status)).toBe(expected);
  });
});

describe("harnessEnvToRow cleanup and cancel stages", () => {
  const row = (stage, status) =>
    harnessEnvToRow({ id: "env-1", name: "Support Line", agent_type: "voice", stage, status });

  it("shows Finalizing for a build in cleanup, where the backend reports running", () => {
    expect(row("cleaning_up", "running").status).toBe(ENV_STATUS.FINALIZING);
  });

  it("shows Cancelled for a cancelled build, where the backend reports failed", () => {
    expect(row("canceled", "failed").status).toBe(ENV_STATUS.CANCELLED);
  });

  it("keeps a real failure as Failed", () => {
    expect(row("failed", "failed").status).toBe(ENV_STATUS.FAILED);
  });

  it.each([0, 3])(
    "shows Ready, never Completed, for a built environment with %i runs",
    (runs) => {
      const built = harnessEnvToRow({
        id: "env-1",
        name: "Support Line",
        agent_type: "voice",
        stage: "completed",
        status: "completed",
        runs_count: runs,
      });
      expect(built.status).toBe(ENV_STATUS.READY);
    },
  );
});
