import { describe, expect, it } from "vitest";
import {
  RUN_KINDS,
  defaultRunsTab,
  isArtifactsRun,
  isPreviewRun,
  kindLabel,
  runKindOf,
} from "../runKind";

// TS #2 runs (R2 STORY): A prompt + tools · B agent · C my code · D/E harness
const TS = {
  A: {
    run_id: "2A",
    config: { kind: "prompt", tools: [{ name: "lookup_order", mode: "mock" }] },
  },
  B: { run_id: "2B", config: { kind: "agent" } },
  C: { run_id: "2C", config: { kind: "code", code: { mode: "sdk_preview" } } },
  D: { run_id: "2D", config: { kind: "harness" } },
  E: { run_id: "2E", config: { kind: "harness" } },
};
// Hero runs as today (no config): #3A / #4A agent, #8A http, #5A upload.
const R3A = { run_id: "3A", target_type: "agent", target: { graph_id: "G" } };
const R4A = { run_id: "4A", target_type: "agent", target: { graph_id: "G" } };
const R8A = { run_id: "8A", target_type: "http" };
const R5A = { run_id: "5A", target_type: "external" };
const R1A = { run_id: "1A", target_type: "prompt" };

describe("runKindOf", () => {
  it("reads config / source / kind first", () => {
    expect(runKindOf(TS.A)).toBe("prompt");
    expect(runKindOf(TS.B)).toBe("agent");
    expect(runKindOf(TS.C)).toBe("code");
    expect(runKindOf(TS.D)).toBe("harness");
    expect(runKindOf({ source: { kind: "traces" } })).toBe("traces");
    expect(runKindOf({ kind: "upload" })).toBe("upload");
  });
  it("falls back to today's run fields", () => {
    expect(runKindOf(R3A)).toBe("agent");
    expect(runKindOf(R8A)).toBe("http");
    expect(runKindOf(R5A)).toBe("upload");
    expect(runKindOf(R1A)).toBe("prompt");
    expect(runKindOf(null)).toBe("prompt");
    expect(runKindOf({ source: { kind: "ui" } })).toBe("prompt");
  });
  it("lists eight kinds (R4 adds voice)", () => {
    expect(RUN_KINDS).toEqual([
      "prompt",
      "agent",
      "code",
      "harness",
      "voice",
      "http",
      "traces",
      "upload",
    ]);
    expect(runKindOf({ config: { kind: "voice" } })).toBe("voice");
  });
});

describe("kindLabel / preview / artifacts", () => {
  it("labels kinds", () => {
    expect(kindLabel("prompt")).toBe("Prompt");
    expect(kindLabel("prompt", { tools: TS.A.config.tools })).toBe(
      "Prompt + tools",
    );
    expect(kindLabel("prompt", { tools: [] })).toBe("Prompt");
    expect(
      ["agent", "code", "traces", "harness", "http", "upload"].map((k) =>
        kindLabel(k),
      ),
      // R4 (F3): `My code` → `Your code`, `HTTP endpoint` → `Endpoint`.
    ).toEqual([
      "Agent",
      "Your code",
      "Traces",
      "Harness",
      "Endpoint",
      "Upload",
    ]);
    expect(kindLabel("voice")).toBe("Voice agent");
  });
  it("marks harness and SDK code runs as Preview", () => {
    expect(isPreviewRun(TS.D)).toBe(true);
    expect(isPreviewRun(TS.C)).toBe(true);
    expect(
      isPreviewRun({ config: { kind: "code", code: { mode: "upload" } } }),
    ).toBe(false);
    expect(isPreviewRun(TS.B)).toBe(false);
  });
  it("gives harness and code runs artifacts", () => {
    expect([TS.A, TS.B, TS.C, TS.D].map(isArtifactsRun)).toEqual([
      false,
      false,
      true,
      true,
    ]);
    expect(isArtifactsRun(null)).toBe(false);
  });
});

describe("defaultRunsTab", () => {
  it("opens Data for runs of one kind (hero #3A, #4A)", () => {
    expect(defaultRunsTab([R3A, R4A])).toBe("data");
    expect(defaultRunsTab([TS.D, TS.E])).toBe("data");
    expect(defaultRunsTab([])).toBe("data");
    expect(defaultRunsTab(undefined)).toBe("data");
  });
  it("opens Matrix for 3+ runs of one kind (IT-R2 G2 P1)", () => {
    expect(defaultRunsTab([R3A, R4A, R3A])).toBe("matrix");
  });
  it("opens Matrix for runs of two or more kinds", () => {
    expect(defaultRunsTab([R4A, R8A])).toBe("matrix");
    expect(defaultRunsTab(Object.values(TS))).toBe("matrix");
  });
});
