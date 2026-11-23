import { describe, it, expect } from "vitest";

import {
  countsLine,
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
