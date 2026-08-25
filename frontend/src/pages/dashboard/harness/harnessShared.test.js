import { describe, expect, it } from "vitest";

import { STATUS_TYPES } from "src/utils/statusUtils";
import {
  agentTypeIcon,
  jobProgress,
  readable,
  canceledProgress,
  errorMessage,
  eventTime,
  STAGE_STATE,
  stageState,
  stageStatus,
  stages,
} from "./harnessShared";

describe("stageStatus", () => {
  it("maps the terminal outcomes explicitly", () => {
    expect(stageStatus("completed")).toBe(STATUS_TYPES.PASS);
    expect(stageStatus("failed")).toBe(STATUS_TYPES.ERROR);
    expect(stageStatus("canceled")).toBe(STATUS_TYPES.CANCELED);
  });

  it("treats everything still in flight as running", () => {
    expect(stageStatus("queued")).toBe(STATUS_TYPES.RUNNING);
    expect(stageStatus("understanding_agent")).toBe(STATUS_TYPES.RUNNING);
    expect(stageStatus("cleaning_up")).toBe(STATUS_TYPES.RUNNING);
  });
});

describe("stages", () => {
  // A stage missing here indexes to -1, which strands the checklist and the progress bar.
  it("covers every non-terminal stage the runner reports", () => {
    ["queued", "acquiring_source", "cleaning_up"].forEach((stage) => {
      expect(stages).toContain(stage);
    });
  });

  it("keeps outcomes out of the pipeline", () => {
    expect(stages).not.toContain("failed");
    expect(stages).not.toContain("canceled");
  });
});

describe("agentTypeIcon", () => {
  it("prefers a voice transport over http when the agent serves both", () => {
    const item = { credentials: { detected_connectors: ["http", "livekit"] } };
    expect(agentTypeIcon(item).label).toBe("Voice agent");
  });

  it("falls back to chat for a plain http agent", () => {
    const item = { credentials: { detected_connectors: ["http"] } };
    expect(agentTypeIcon(item).label).toBe("Chat agent");
  });

  it("does not treat a non-transport signal as a type", () => {
    const item = { credentials: { detected_connectors: ["mcp"] } };
    expect(agentTypeIcon(item).label).toBe("Not detected");
  });

  it("survives a job with no credentials block", () => {
    expect(agentTypeIcon(undefined).label).toBe("Not detected");
  });
});

describe("jobProgress", () => {
  it("reports a completed run as full regardless of list position", () => {
    expect(jobProgress({ stage: "completed" })).toBe(100);
  });

  it("keeps a floor so an early run still shows a sliver", () => {
    expect(jobProgress({ stage: "queued" })).toBeGreaterThanOrEqual(2);
  });

  it("advances as the run moves through the pipeline", () => {
    const early = jobProgress({ stage: "understanding_agent" });
    const late = jobProgress({ stage: "grading" });
    expect(late).toBeGreaterThan(early);
  });

  it("does not collapse on an unrecognised stage", () => {
    expect(jobProgress({ stage: "not_a_stage" })).toBeGreaterThanOrEqual(2);
  });
});

describe("readable", () => {
  it("turns a snake_case stage into a sentence", () => {
    expect(readable("uploading_artifacts")).toBe("Uploading artifacts");
  });
});

describe("stageState", () => {
  const at = (stage) => stages.indexOf(stage);

  it("marks the stage a run is in as active, not done", () => {
    const status = { stage: "generating_environment" };
    expect(stageState(status, at("understanding_agent"))).toBe(STAGE_STATE.DONE);
    expect(stageState(status, at("generating_environment"))).toBe(
      STAGE_STATE.ACTIVE,
    );
    expect(stageState(status, at("running"))).toBe(STAGE_STATE.PENDING);
  });

  it("splits a failed run around failure.stage", () => {
    const status = {
      stage: "failed",
      failure: { stage: "validating_environment" },
    };
    expect(stageState(status, at("building_environment"))).toBe(
      STAGE_STATE.DONE,
    );
    expect(stageState(status, at("validating_environment"))).toBe(
      STAGE_STATE.FAILED,
    );
    expect(stageState(status, at("generating_data"))).toBe(STAGE_STATE.PENDING);
  });

  it("completes every stage once the run completes", () => {
    const status = { stage: "completed" };
    stages.forEach((_, index) => {
      expect(stageState(status, index)).toBe(STAGE_STATE.DONE);
    });
  });

  it("claims no progress when there is nothing to anchor on", () => {
    // A cancel names no stage, and a failure stage outside the vocabulary cannot be placed.
    const canceled = { stage: "canceled" };
    const unknown = { stage: "failed", failure: { stage: "not_a_stage" } };
    const missing = { stage: "failed", failure: null };
    stages.forEach((_, index) => {
      expect(stageState(canceled, index)).toBe(STAGE_STATE.PENDING);
      expect(stageState(unknown, index)).toBe(STAGE_STATE.PENDING);
      expect(stageState(missing, index)).toBe(STAGE_STATE.PENDING);
    });
  });

  it("survives a missing status", () => {
    expect(stageState(undefined, 0)).toBe(STAGE_STATE.PENDING);
  });
});

describe("canceledProgress", () => {
  const started = (stage) => ({
    type: "harness.stage.started",
    payload: { stage },
  });
  const completed = (stage) => ({
    type: "harness.stage.completed",
    payload: { stage },
  });

  it("credits nothing when there are no events", () => {
    const { doneStages, stoppedAt } = canceledProgress([]);
    expect(doneStages.size).toBe(0);
    expect(stoppedAt).toBe(-1);
  });

  it("stops in the group that started and never completed", () => {
    const { doneStages, stoppedAt } = canceledProgress([started("understand")]);
    expect(stages[stoppedAt]).toBe("understanding_agent");
    expect(doneStages.has("queued")).toBe(true);
    expect(doneStages.has("acquiring_source")).toBe(true);
    expect(doneStages.has("understanding_agent")).toBe(false);
  });

  it("credits every stage of a group that completed", () => {
    const { doneStages } = canceledProgress([
      started("environment"),
      completed("environment"),
    ]);
    ["generating_environment", "building_environment", "validating_environment", "generating_data"].forEach(
      (stage) => expect(doneStages.has(stage)).toBe(true),
    );
  });

  it("never credits a group that only started", () => {
    const { doneStages } = canceledProgress([started("environment")]);
    ["building_environment", "validating_environment", "generating_data"].forEach(
      (stage) => expect(doneStages.has(stage)).toBe(false),
    );
  });

  // The runner emits cleaning_up BEFORE uploading_artifacts, the reverse of the declared
  // stage order. Deriving progress from list position credited artifact upload that had
  // not happened, and dropped the later event entirely.
  it("handles the runner emitting stages out of list order", () => {
    const { doneStages, stoppedAt } = canceledProgress([
      started("cleaning_up"),
      completed("cleaning_up"),
      started("uploading_artifacts"),
    ]);
    expect(doneStages.has("cleaning_up")).toBe(true);
    expect(doneStages.has("uploading_artifacts")).toBe(false);
    expect(stages[stoppedAt]).toBe("uploading_artifacts");
  });
});

describe("stageState for a canceled run", () => {
  const status = { stage: "canceled" };
  const events = [
    { type: "harness.stage.started", payload: { stage: "understand" } },
  ];
  const at = (stage) => stages.indexOf(stage);

  it("shows how far the run got instead of blanking", () => {
    expect(stageState(status, at("queued"), events)).toBe(STAGE_STATE.DONE);
    expect(stageState(status, at("acquiring_source"), events)).toBe(
      STAGE_STATE.DONE,
    );
    expect(stageState(status, at("understanding_agent"), events)).toBe(
      STAGE_STATE.STOPPED,
    );
    expect(stageState(status, at("running"), events)).toBe(STAGE_STATE.PENDING);
  });

  it("falls back to pending with no events to anchor on", () => {
    stages.forEach((_, index) => {
      expect(stageState(status, index, [])).toBe(STAGE_STATE.PENDING);
    });
  });
});

describe("eventTime", () => {
  it("shows time only for something that happened today", () => {
    const today = new Date();
    today.setHours(16, 43, 56, 0);
    expect(eventTime(today.toISOString())).not.toMatch(/[A-Z][a-z]{2}/);
  });

  it("adds the date once the event is not from today", () => {
    // Without this, a run crossing midnight reads as though it went backwards.
    const earlier = new Date();
    earlier.setDate(earlier.getDate() - 1);
    expect(eventTime(earlier.toISOString())).toMatch(/\d{1,2} [A-Z][a-z]{2},/);
  });

  it("renders nothing for a missing or unparseable value", () => {
    expect(eventTime(undefined)).toBe("");
    expect(eventTime("")).toBe("");
    expect(eventTime("not-a-date")).toBe("");
  });
});

describe("errorMessage", () => {
  // The axios interceptor spreads response.data flat and adds statusCode; it never
  // produces a nested .response, so reading through one silently loses every message.
  it("reads a bare detail, which is what the harness views return", () => {
    expect(errorMessage({ detail: "job not found", statusCode: 404 })).toBe(
      "job not found",
    );
  });

  it("prefers detail on the platform envelope", () => {
    expect(
      errorMessage({
        status: false,
        detail: "Authentication credentials were not provided.",
        message: "Authentication credentials were not provided.",
        statusCode: 401,
      }),
    ).toBe("Authentication credentials were not provided.");
  });

  it("unwraps DRF field errors", () => {
    expect(errorMessage({ detail: { source_id: ["This field is required."] } })).toBe(
      "This field is required.",
    );
  });

  it("falls back to message when there was no response at all", () => {
    expect(errorMessage({ message: "Network Error" })).toBe("Network Error");
  });

  it("never returns an empty string", () => {
    expect(errorMessage({})).toBe("Something went wrong");
    expect(errorMessage(undefined)).toBe("Something went wrong");
  });
});
