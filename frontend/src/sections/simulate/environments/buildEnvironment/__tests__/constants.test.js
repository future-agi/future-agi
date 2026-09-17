import { describe, it, expect } from "vitest";

import { BUILD_TONES } from "../buildTones";
import { ORIGIN_KINDS, ORIGIN_ID } from "../provenance.constants";
import {
  BUILD_PIPELINE,
  PIPELINE_PHASE,
  pipelineStatus,
  pipelineSummary,
} from "../buildPipeline.constants";
import {
  READ_SECTIONS,
  MOCK_SECTION_ISSUES,
  READ_AUDIT_COPY,
} from "../readAudit.constants";
import { BUILDING_TABS, DERIVING_LABEL } from "../build.constants";

describe("provenance constants", () => {
  const ids = ["code", "config", "prompt", "doc", "callGraph", "policy", "fixture", "inferred"];

  it("declares the eight origin kinds", () => {
    expect(Object.keys(ORIGIN_KINDS)).toEqual(ids);
  });

  it("every kind is self-keyed and fully described with a tone colour", () => {
    const tones = Object.values(BUILD_TONES);
    ids.forEach((id) => {
      const kind = ORIGIN_KINDS[id];
      expect(kind.id).toBe(id);
      expect(ORIGIN_ID[Object.keys(ORIGIN_ID).find((k) => ORIGIN_ID[k] === id)]).toBe(id);
      expect(typeof kind.short).toBe("string");
      expect(typeof kind.label).toBe("string");
      expect(typeof kind.note).toBe("string");
      expect(tones).toContain(kind.color);
    });
  });
});

describe("build pipeline constants", () => {
  it("has twelve steps split into setup and run phases", () => {
    expect(BUILD_PIPELINE).toHaveLength(12);
    expect(BUILD_PIPELINE.filter((s) => s.phase === PIPELINE_PHASE.SETUP).map((s) => s.id)).toEqual([
      "understand",
      "generate-env",
      "build-env",
      "validate-env",
      "generate-data",
      "generate-scenarios",
      "validate-scenarios",
    ]);
    expect(BUILD_PIPELINE.filter((s) => s.phase === PIPELINE_PHASE.RUN).map((s) => s.id)).toEqual([
      "connect-agent",
      "run",
      "grade",
      "upload",
      "completed",
    ]);
  });

  it("derives step status from the done set and running flag", () => {
    const idle = pipelineStatus([], false);
    expect(idle.every((s) => s.status === "pending")).toBe(true);

    const started = pipelineStatus([], true);
    expect(started[0].status).toBe("running");
    expect(started.slice(1).every((s) => s.status === "pending")).toBe(true);

    const oneDone = pipelineStatus(["understand"], true);
    expect(oneDone.find((s) => s.id === "understand").status).toBe("done");
    expect(oneDone.find((s) => s.id === "generate-env").status).toBe("running");

    const setupDone = pipelineStatus(["understand", "build", "scenarios"], false);
    expect(setupDone.filter((s) => s.status === "done")).toHaveLength(7);
    expect(setupDone.filter((s) => s.phase === "run").every((s) => s.status === "pending")).toBe(true);

    // After 7/7, a chat message flips running:true but must NOT relight a run-phase
    // step — the pill stays "Ready to run", not "Connecting agent".
    const setupDoneRunning = pipelineStatus(["understand", "build", "scenarios"], true, "setup");
    expect(setupDoneRunning.some((s) => s.status === "running")).toBe(false);
    expect(pipelineSummary(setupDoneRunning).label).toBe("7 of 12 complete");

    const failed = pipelineStatus(["understand"], true, "setup", { stepId: "build-env" });
    expect(failed.find((s) => s.id === "build-env").status).toBe("failed");
    const buildEnvIdx = BUILD_PIPELINE.findIndex((s) => s.id === "build-env");
    expect(failed.slice(buildEnvIdx + 1).every((s) => s.status === "pending")).toBe(true);
  });

  it("summarises the pipeline into a human label", () => {
    expect(pipelineSummary(pipelineStatus(["understand"], true)).label).toBe("Running · 1 of 12");
    expect(pipelineSummary(pipelineStatus(["understand", "build", "scenarios"], false)).label).toBe(
      "7 of 12 complete",
    );
    expect(
      pipelineSummary(pipelineStatus(["understand"], true, "setup", { stepId: "build-env" })).label,
    ).toBe("Failed at building environment");
  });
});

describe("read-audit constants", () => {
  it("declares the four read sections in order", () => {
    expect(READ_SECTIONS.map((s) => s.key)).toEqual(["tools", "rules", "data", "behavior"]);
  });

  it("mocks issues only on rules and data", () => {
    expect(Object.keys(MOCK_SECTION_ISSUES)).toEqual(["rules", "data"]);
  });

  it("carries the read-audit copy", () => {
    expect(READ_AUDIT_COPY.build).toBe("Build the environment");
  });
});

describe("build stage constants", () => {
  it("labels the five building tabs (Runs added in Phase-3)", () => {
    expect(BUILDING_TABS.map((t) => t.label)).toEqual([
      "Contract",
      "Scenarios",
      "Evaluations",
      "Summary",
      "Runs",
    ]);
  });

  it("uses the designer deriving copy", () => {
    expect(DERIVING_LABEL.understand).toBe("Reading your agent — extracting tools and rules");
  });
});
