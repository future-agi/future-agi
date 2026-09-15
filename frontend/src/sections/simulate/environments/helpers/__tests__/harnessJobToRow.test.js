import { describe, it, expect } from "vitest";
import { harnessJobToRow } from "../harnessJobToRow";
import { ENV_STATUS } from "../../myEnvironments.constants";

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
