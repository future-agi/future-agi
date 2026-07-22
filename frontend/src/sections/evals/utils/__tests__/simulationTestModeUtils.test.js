import { describe, expect, it } from "vitest";

import {
  NEVER_PICKABLE_TOPLEVEL,
  VOICE_ONLY_METRICS,
  isHiddenPickerPath,
  isTextCallDetail,
  translateDeepScenarioColumn,
} from "../simulationTestModeUtils";

describe("isTextCallDetail", () => {
  it("returns true for chat / prompt sims on simulation.call_type", () => {
    expect(isTextCallDetail({ simulation: { call_type: "text" } })).toBe(true);
    expect(isTextCallDetail({ simulation: { call_type: "chat" } })).toBe(true);
    expect(isTextCallDetail({ simulation: { call_type: "prompt" } })).toBe(true);
  });

  it("returns false for voice sims", () => {
    expect(isTextCallDetail({ simulation: { call_type: "voice" } })).toBe(false);
  });

  it("falls back to call_type / simulation_call_type when nested missing", () => {
    expect(isTextCallDetail({ call_type: "text" })).toBe(true);
    expect(isTextCallDetail({ simulation_call_type: "text" })).toBe(true);
    expect(isTextCallDetail({ call_type: "voice" })).toBe(false);
  });

  it("returns false for null / undefined / empty detail", () => {
    expect(isTextCallDetail(null)).toBe(false);
    expect(isTextCallDetail(undefined)).toBe(false);
    expect(isTextCallDetail({})).toBe(false);
  });

  it("does not treat direction (Inbound/Outbound) as text call", () => {
    // regression: an earlier version had call_type precedence over
    // simulation_call_type, so "Inbound" (direction) shadowed "text" (modality)
    expect(isTextCallDetail({ simulation: { call_type: "Inbound" } })).toBe(false);
  });

  it("prefers simulation_call_type over call_type when both are populated", () => {
    // catch-fallback path carries both keys on the same row
    expect(
      isTextCallDetail({ call_type: "Inbound", simulation_call_type: "text" }),
    ).toBe(true);
    expect(
      isTextCallDetail({ call_type: "outboundPhoneCall", simulation_call_type: "text" }),
    ).toBe(true);
    expect(
      isTextCallDetail({ call_type: "Inbound", simulation_call_type: "voice" }),
    ).toBe(false);
  });
});

describe("isHiddenPickerPath", () => {
  it("blocks NEVER_PICKABLE regardless of modality", () => {
    for (const root of NEVER_PICKABLE_TOPLEVEL) {
      expect(isHiddenPickerPath(root, true)).toBe(true);
      expect(isHiddenPickerPath(root, false)).toBe(true);
      expect(isHiddenPickerPath(`${root}.nested.field`, false)).toBe(true);
    }
  });

  it("blocks VOICE_ONLY_METRICS only on text sims", () => {
    for (const root of VOICE_ONLY_METRICS) {
      expect(isHiddenPickerPath(root, true)).toBe(true);
      expect(isHiddenPickerPath(root, false)).toBe(false);
      expect(isHiddenPickerPath(`${root}.sub`, true)).toBe(true);
    }
  });

  it("does not block unrelated fields", () => {
    expect(isHiddenPickerPath("agent.name", true)).toBe(false);
    expect(isHiddenPickerPath("call.transcript", true)).toBe(false);
    expect(isHiddenPickerPath("scenario_columns.persona.value", true)).toBe(false);
  });

  it("substring safety: does not block `customer_number_display` when only `customer_number` is hidden", () => {
    expect(isHiddenPickerPath("customer_number_display", true)).toBe(false);
  });
});

describe("translateDeepScenarioColumn", () => {
  it("rewrites deep paths to walker form", () => {
    expect(translateDeepScenarioColumn("scenario.columns.persona.personality")).toBe(
      "scenario_columns.persona.value.personality",
    );
    expect(
      translateDeepScenarioColumn("scenario.columns.Ideal Outcome.name"),
    ).toBe("scenario_columns.Ideal Outcome.value.name");
  });

  it("returns null for the top-level column reference (no subpath)", () => {
    expect(translateDeepScenarioColumn("scenario.columns.persona")).toBeNull();
  });

  it("returns null for unrelated keys", () => {
    expect(translateDeepScenarioColumn("agent.name")).toBeNull();
    expect(translateDeepScenarioColumn("scenario.info.name")).toBeNull();
    expect(translateDeepScenarioColumn("")).toBeNull();
  });

  it("preserves multi-segment subpaths", () => {
    expect(
      translateDeepScenarioColumn("scenario.columns.persona.a.b.c"),
    ).toBe("scenario_columns.persona.value.a.b.c");
  });
});
