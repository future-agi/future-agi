// K-2 (RT-09): the one Record rule (create fields, expiry, the footer list).
import { describe, expect, it } from "vitest";
import {
  NOT_RECORDED_HELP,
  NOT_RECORDED_SUMMARY,
  RECORD_HELP,
  UNRECORDED_RETENTION_HOURS,
  UNRECORDED_RETENTION_MS,
  childRecordBaseOf,
  expiryText,
  isExpired,
  isQuickTestRun,
  isUnrecorded,
  notRecordedOf,
  recordFieldsOf,
  recordedText,
  recordsOf,
} from "../recordModel";
import { quickTestsOf } from "src/sections/experiment-detail/iterate/useQuickTests";

// Local times, so the copy reads the same in any time zone.
const at = (d, h, m) => new Date(2026, 9, d, h, m, 0, 0).getTime();

describe("recordFieldsOf", () => {
  it("Record on (or absent): listed and recorded, no expiry", () => {
    expect(recordFieldsOf({}, at(23, 14, 5))).toEqual({
      listed: true,
      recorded: true,
      expires_at: null,
    });
    expect(recordFieldsOf({ record: true }, 0).recorded).toBe(true);
    expect(recordFieldsOf(null, 0).listed).toBe(true);
    expect(recordsOf(undefined)).toBe(true);
  });

  it("Record off: off the leaderboard, deleted the retention after it starts", () => {
    const start = at(23, 14, 5);
    const f = recordFieldsOf({ record: false }, start);
    expect(f.listed).toBe(false);
    expect(f.recorded).toBe(false);
    expect(Date.parse(f.expires_at) - start).toBe(
      UNRECORDED_RETENTION_HOURS * 3600 * 1000,
    );
    expect(UNRECORDED_RETENTION_MS).toBe(24 * 3600 * 1000);
  });
});

describe("isExpired", () => {
  const run = recordFieldsOf({ record: false }, at(23, 14, 5));
  it("is due at the expiry, not before", () => {
    expect(isExpired(run, at(24, 14, 4))).toBe(false);
    expect(isExpired(run, at(24, 14, 5))).toBe(true);
    expect(isExpired(run, at(25, 9, 0))).toBe(true);
  });
  it("never for a recorded run or a run without the field", () => {
    expect(isExpired({ ...run, recorded: true }, at(30, 0, 0))).toBe(false);
    expect(isExpired({ expires_at: run.expires_at }, at(30, 0, 0))).toBe(false);
    expect(isExpired({ recorded: false, expires_at: null }, at(30, 0, 0))).toBe(
      false,
    );
  });
  it("never for a listed run or a quick test that copied Record off (review MF-3)", () => {
    const late = at(30, 0, 0);
    expect(isExpired({ ...run, listed: true }, late)).toBe(false);
    expect(
      isExpired(
        { ...run, source: { kind: "quick_test" }, label: "Quick test" },
        late,
      ),
    ).toBe(false);
    expect(isExpired({ ...run, kind: "quick_test" }, late)).toBe(false);
  });
});

describe("isQuickTestRun", () => {
  it("is the quickTestsOf test (iterate/useQuickTests.js)", () => {
    const runs = [
      { run_id: "q1", listed: false, kind: "quick_test" },
      { run_id: "q2", listed: false, source: { kind: "quick_test" } },
      {
        run_id: "rp",
        listed: false,
        label: "Replay",
        source: { kind: "quick_test" },
      },
      { run_id: "u", listed: false, recorded: false, source: { kind: "ui" } },
      { run_id: "s", listed: true, kind: "agent", source: { kind: "iterate" } },
      { run_id: "n", listed: false },
    ];
    expect(
      runs
        .filter((r) => r.listed === false && isQuickTestRun(r))
        .map((r) => r.run_id),
    ).toEqual(quickTestsOf(runs).map((r) => r.run_id));
    expect(isQuickTestRun(null)).toBe(false);
  });
});

describe("childRecordBaseOf (M-C7)", () => {
  const off = {
    id: "p",
    listed: false,
    ...recordFieldsOf({ record: false }, at(23, 14, 5)),
    name: "Scratch",
  };
  it("a listed child of an unrecorded run is recorded, with no deletion time", () => {
    expect(childRecordBaseOf(off, { listed: true })).toEqual({
      id: "p",
      listed: false,
      name: "Scratch",
      recorded: true,
      expires_at: null,
    });
  });
  it("a quick test carries no record state at all", () => {
    const q = childRecordBaseOf(off, { listed: false });
    expect("recorded" in q).toBe(false);
    expect("expires_at" in q).toBe(false);
    expect(q.name).toBe("Scratch");
  });
  it("a run without record state passes through unchanged", () => {
    const seeded = { id: "s", listed: true, name: "Seeded" };
    expect(childRecordBaseOf(seeded, { listed: true })).toEqual(seeded);
    expect(childRecordBaseOf(seeded, { listed: false })).toEqual(seeded);
    expect(childRecordBaseOf(null, { listed: true })).toEqual({});
  });
});

describe("expiryText", () => {
  const run = recordFieldsOf({ record: false }, at(23, 14, 5));
  it("`deleted at 14:05 tomorrow` the day it starts", () => {
    expect(expiryText(run, at(23, 14, 6))).toBe("deleted at 14:05 tomorrow");
  });
  it("`today` on the day it is due, a date further out", () => {
    expect(expiryText(run, at(24, 9, 0))).toBe("deleted at 14:05 today");
    expect(expiryText(run, at(22, 9, 0))).toBe("deleted at 14:05 on Oct 24");
  });
  it("once due, and without a time", () => {
    expect(expiryText(run, at(24, 15, 0))).toBe("deleted on the next refresh");
    expect(expiryText({}, at(24, 15, 0))).toBeNull();
  });
});

describe("notRecordedOf", () => {
  it("lists runs started with Record off only (not quick tests, not recorded)", () => {
    const runs = [
      { run_id: "a", listed: false, recorded: false },
      { run_id: "q", listed: false, kind: "quick_test" },
      { run_id: "r", listed: true, recorded: true },
      { run_id: "s", listed: true },
      // copied Record off from an unrecorded parent (review MF-3)
      {
        run_id: "qc",
        listed: false,
        recorded: false,
        label: "Quick test",
        source: { kind: "quick_test" },
      },
      { run_id: "lc", listed: true, recorded: false },
    ];
    expect(notRecordedOf(runs).map((r) => r.run_id)).toEqual(["a"]);
    expect(runs.filter(isUnrecorded).map((r) => r.run_id)).toEqual(["a"]);
    expect(notRecordedOf(null)).toEqual([]);
  });
});

describe("copy", () => {
  it("says Record is always on, and claims no deletion (W7)", () => {
    expect(RECORD_HELP).toBe(
      "Keeping a run off the leaderboard is not available in this release.",
    );
    expect(NOT_RECORDED_HELP).toBe(
      "These runs stay off the leaderboard until you record them.",
    );
    expect(NOT_RECORDED_SUMMARY).toBe("Not recorded · off the leaderboard");
    [RECORD_HELP, NOT_RECORDED_HELP, NOT_RECORDED_SUMMARY].forEach((text) =>
      expect(text).not.toMatch(/deleted|24 hours/),
    );
    expect(recordedText("#9A")).toBe("#9A recorded · now on the leaderboard");
  });
});
