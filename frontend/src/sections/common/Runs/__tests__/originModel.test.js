// W1-6 (notify E06): a run's details name where it came from.
import { describe, expect, it } from "vitest";
import { originLine } from "../originModel";

describe("W1-6 · origin line", () => {
  it("a first run names where it was created; a later run what started it and who", () => {
    expect(originLine({ metadata: { source: "ui" } })).toBe(
      "Created from the app",
    );
    // serial merge M5 (S3 review S6): the source reads as words
    expect(originLine({ metadata: { source: "sdk" } })).toBe(
      "Created from the SDK",
    );
    expect(originLine({ metadata: { source: "ci" } })).toBe("Created from CI");
    expect(
      originLine({
        origin: "rerun",
        trigger: { by: { name: "Maya Chen" }, action: "rerun_failed" },
      }),
    ).toBe("Rerun · failed rows by Maya Chen");
    expect(
      originLine({
        origin: "rerun",
        trigger: {
          by: { name: "Asha" },
          action: "edit",
          at: "2026-10-23T17:42:00Z",
        },
      }),
    ).toMatch(/^Edit experiment by Asha · /);
  });
});
