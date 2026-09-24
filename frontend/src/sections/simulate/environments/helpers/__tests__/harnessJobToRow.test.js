import { describe, it, expect } from "vitest";
import { harnessJobToRow, harnessEnvToRow, buildStatusFor } from "../harnessJobToRow";
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
    ["completed", ENV_STATUS.COMPLETED],
    ["failed", ENV_STATUS.FAILED],
    ["canceled", ENV_STATUS.FAILED],
    ["running", ENV_STATUS.RUNNING],
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

  it("falls back to text when no voice connector is detected", () => {
    expect(harnessJobToRow(item({ connectors: ["http"] })).agentType).toBe(
      "text",
    );
    expect(harnessJobToRow(item({ connectors: [] })).agentType).toBe("text");
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
    expect(row.agentType).toBe("text");
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
      status: ENV_STATUS.COMPLETED,
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

  it("maps every in-progress stage → building", () => {
    ["queued", "running", "generating_environment", undefined].forEach((s) =>
      expect(buildStatusFor(s)).toBe(BUILD_STATUS.BUILDING),
    );
  });
});
