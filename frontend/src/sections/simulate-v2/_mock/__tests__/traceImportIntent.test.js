import { describe, expect, it } from "vitest";
import {
  TRACE_GOALS,
  draftIntentOf,
  productionTracesOf,
  retitleForShows,
  traceDraftsOf,
} from "../traceImport";

const ENV = { id: "env-voice-support", name: "Customer Support Line" };
/* Real production traces, with the outcome each test needs. */
const all = productionTracesOf(ENV, {});
const trace = (i, outcome) => ({ ...all[i], outcome });

describe("trace import drafts say what they are", () => {
  it("offers every goal — none waits for later", () => {
    expect(TRACE_GOALS.some((g) => g.later)).toBe(false);
  });

  it("a passed trace under Reproduce is kept as a regression", () => {
    expect(draftIntentOf("reproduce", "passed")).toBe("preserve");
    expect(draftIntentOf("reproduce", "failed")).toBe("reproduce");
    const [a, b] = [trace(0, "failed"), trace(1, "passed")];
    const rows = traceDraftsOf([a, b], "reproduce", ENV, { id: "imp_1" });
    expect(rows.map((r) => r.title)).toEqual([
      `${a.topic} — reproduces ${a.traceId}`,
      `${b.topic} — keeps ${b.traceId} as a regression`,
    ]);
    expect(rows.map((r) => r.traceImport.goal)).toEqual([
      "reproduce",
      "preserve",
    ]);
  });

  it("drafts replay and pattern imports", () => {
    const ts = [trace(0, "failed"), trace(1, "failed")];
    expect(traceDraftsOf(ts, "replay", ENV, { id: "i" })).toHaveLength(2);
    const [p] = traceDraftsOf(ts, "pattern", ENV, { id: "i" });
    expect(p.traceImport.sourceIds).toEqual(ts.map((t) => t.traceId));
  });

  it("retitles a draft when the review says its record shows a success", () => {
    const row = {
      title: "Refund for a late parcel — reproduces T-102",
      summary:
        "Reproduces production trace T-102 — the situation and the failure, judged against the policy",
      traceImport: { goal: "reproduce", sourceIds: ["T-102"] },
    };
    const kept = retitleForShows(row, "success");
    expect(kept.title).toBe(
      "Refund for a late parcel — keeps T-102 as a regression",
    );
    expect(kept.summary).toBe(
      "Keeps what worked in production trace T-102 as a regression",
    );
    expect(retitleForShows(kept, "failure").title).toBe(row.title);
    expect(retitleForShows(row, "partial")).toBe(row);
    /* A title the import didn't write is left alone. */
    const own = { ...row, title: "Clinic DOB check" };
    expect(retitleForShows(own, "success")).toBe(own);
  });
});
