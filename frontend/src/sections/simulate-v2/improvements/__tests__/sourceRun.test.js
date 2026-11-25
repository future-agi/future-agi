import { describe, expect, it } from "vitest";
import { sourceRunOf } from "../improvementRows";

const runs = [
  { id: "r1", ordinal: 1, finishedAt: "2026-11-20T10:00:00Z" },
  { id: "r2", ordinal: 2, finishedAt: "2026-11-25T10:00:00Z" },
];

describe("sourceRunOf", () => {
  it("drops a recorded run that came after the epoch", () => {
    expect(
      sourceRunOf(
        { kind: "run", fromRunId: "r2", createdAt: "2026-10-21T00:00:00Z" },
        runs,
      ),
    ).toBeNull();
  });
  it("keeps a recorded run that existed", () => {
    expect(
      sourceRunOf(
        { kind: "run", fromRunId: "r1", createdAt: "2026-11-22T00:00:00Z" },
        runs,
      )?.id,
    ).toBe("r1");
  });
  it("falls back to the latest run finished before", () => {
    expect(
      sourceRunOf({ kind: "goal", createdAt: "2026-11-24T00:00:00Z" }, runs)
        ?.id,
    ).toBe("r1");
    expect(
      sourceRunOf({ kind: "goal", createdAt: "2026-10-01T00:00:00Z" }, runs),
    ).toBeNull();
  });
  it("an Error Feed issue is its own source", () => {
    expect(
      sourceRunOf(
        {
          kind: "error-feed",
          fromRunId: "r1",
          createdAt: "2026-11-24T00:00:00Z",
        },
        runs,
      ),
    ).toBeNull();
  });
});
