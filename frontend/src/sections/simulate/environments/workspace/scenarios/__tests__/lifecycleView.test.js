import { describe, it, expect } from "vitest";

import {
  citationState,
  countsLine,
  isBeingValidated,
  isNotMeasured,
  isRunnable,
  lifecycleChip,
  markTitle,
  stateText,
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

  it.each([
    ["proof_failed", "danger"],
    ["unproved", "neutral"],
    ["ours", "neutral"],
  ])("marks a case held by %s as %s", (held_by, tone) => {
    expect(lifecycleChip({ state: "draft", held_by }).tone).toBe(tone);
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

describe("the server's sentence, as sent", () => {
  it.each([
    [
      {
        state: "kept_as_written",
        warning_text:
          "Kept as written: only an AI judge grades this case, and nothing has shown it fails a wrong answer.",
      },
      "Kept as written: only an AI judge grades this case, and nothing has shown it fails a wrong answer.",
      "Runs, but not proved. Kept as written: only an AI judge grades this case, and nothing has shown it fails a wrong answer.",
    ],
    [
      { state: "archived", reason_text: "Archived on 24 Nov 2026: Duplicate" },
      "Archived on 24 Nov 2026: Duplicate",
      "Archived on 24 Nov 2026: Duplicate",
    ],
    [
      { state: "draft", reason_text: "Not validated on these versions yet." },
      "Not validated on these versions yet.",
      "Not validated on these versions yet.",
    ],
    [{ state: "archived" }, "Archived", "Archived"],
  ])("%j", (lifecycle, mark, line) => {
    expect(markTitle(lifecycleChip(lifecycle))).toBe(mark);
    expect(stateText(lifecycle)).toBe(line);
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
  it("marks the named scenarios, or every saved one but the archived when none are named", () => {
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
    expect(
      isBeingValidated(
        { lifecycle: { state: "draft", reason: "in_draft" } },
        { job_id: "j", scenario_keys: null },
      ),
    ).toBe(false);
    expect(isBeingValidated(row("draft"), { job_id: null })).toBe(false);
  });
});
