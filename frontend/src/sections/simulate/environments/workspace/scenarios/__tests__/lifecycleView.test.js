import { describe, it, expect } from "vitest";

import {
  citationState,
  countsLine,
  isBeingValidated,
  isNotMeasured,
  isRunnable,
  lifecycleChip,
  wrongWayRows,
} from "../lifecycleView";

const control = (over) => ({
  name: "Two refunds",
  why: "",
  ran: true,
  failed: true,
  assumed: [],
  ...over,
});

describe("wrongWayRows", () => {
  it("reads each ran example as caught, not caught or not run", () => {
    const rows = wrongWayRows({
      proof: {
        controls: [
          control(),
          control({ failed: false }),
          control({ ran: false, failed: null }),
          control({ assumed: ["refund"] }),
        ],
      },
    });
    expect(rows.map((one) => one.result)).toEqual([
      "caught",
      "not_caught",
      "not_run",
      "not_run",
    ]);
  });

  it("lists the scenario's own examples as not run when the proof ran none", () => {
    const negative_controls = [{ name: "A" }, { name: "B" }];
    expect(
      wrongWayRows({ proof: { controls: [] }, negative_controls }),
    ).toHaveLength(2);
    expect(
      wrongWayRows({ proof: null, negative_controls }).map((one) => one.result),
    ).toEqual(["not_run", "not_run"]);
    expect(wrongWayRows({ proof: null, negative_controls: [] })).toEqual([]);
  });
});

describe("lifecycleChip and isRunnable", () => {
  it("marks no state that needs nothing, and a case without validation as running", () => {
    expect(lifecycleChip({ state: "active" })).toBeNull();
    expect(lifecycleChip({ state: "validated" })).toBeNull();
    const unvalidated = { state: "draft", reason: "validation_unavailable" };
    expect(lifecycleChip(unvalidated).key).toBe("runs_unvalidated");
    expect(isRunnable(unvalidated)).toBe(true);
    expect(isRunnable({ state: "draft", reason: "not_validated" })).toBe(false);
  });

  it("gives an older kept-as-written case a reason even when the server sends none", () => {
    const chip = lifecycleChip({
      state: "kept_as_written",
      legacy: true,
      reason_text: null,
      warning_text: null,
    });
    expect(chip.hover).not.toBe("");
  });
});

describe("countsLine", () => {
  it("adds the no-wrong-way count only when there is one", () => {
    const counts = {
      proved: 6,
      kept_as_written: 0,
      not_proved: 4,
      legacy_without_control: 0,
    };
    expect(countsLine(counts)).toBe(
      "6 proved · 0 kept as written · 4 not proved",
    );
    expect(countsLine({ ...counts, legacy_without_control: 3 })).toBe(
      "6 proved · 0 kept as written · 4 not proved · 3 without a wrong-way example",
    );
    expect(countsLine(null)).toBeNull();
  });
});

describe("citationState", () => {
  it("reads a cited rule or file as confirmed, changed since, or not confirmed", () => {
    const lifecycle = { source_changed: ["R6"] };
    expect(
      citationState({ kind: "rule", ref: "R2", resolved: true }, lifecycle),
    ).toBe("confirmed");
    expect(
      citationState({ kind: "rule", ref: "R6", resolved: true }, lifecycle),
    ).toBe("changed");
    expect(
      citationState(
        { kind: "file", ref: "file:policy.pdf", resolved: false },
        lifecycle,
      ),
    ).toBe("unconfirmed");
    expect(
      citationState(
        { kind: "requirement", ref: "", resolved: true },
        lifecycle,
      ),
    ).toBeNull();
  });
});

describe("isNotMeasured", () => {
  it("flags only a check reading evidence the agent doesn't give", () => {
    expect(
      isNotMeasured({ state: "active", warning: "evidence_not_captured" }),
    ).toBe(true);
    expect(
      isNotMeasured({
        state: "kept_as_written",
        warning: "no_negative_control",
      }),
    ).toBe(false);
  });
});

describe("isBeingValidated", () => {
  const row = (state, key = "a") => ({
    scenarioKey: key,
    lifecycle: { state },
  });
  it("marks the named scenarios, or every one but the archived when none are named", () => {
    expect(
      isBeingValidated(row("active", "a"), {
        job_id: "j",
        scenario_keys: ["a"],
      }),
    ).toBe(true);
    expect(
      isBeingValidated(row("draft", "b"), {
        job_id: "j",
        scenario_keys: ["a"],
      }),
    ).toBe(false);
    expect(
      isBeingValidated(row("draft"), { job_id: "j", scenario_keys: null }),
    ).toBe(true);
    expect(
      isBeingValidated(row("active"), { job_id: "j", scenario_keys: null }),
    ).toBe(true);
    expect(
      isBeingValidated(row("archived"), { job_id: "j", scenario_keys: null }),
    ).toBe(false);
    expect(isBeingValidated(row("draft"), { job_id: null })).toBe(false);
  });
});
