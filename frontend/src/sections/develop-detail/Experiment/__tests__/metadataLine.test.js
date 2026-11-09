import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { metadataObject } from "../experimentFormValues";
import { metadataLine } from "../metadataLine";

const reviewStep = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "StepperComponentExperiment",
    "ReviewStep.jsx",
  ),
  "utf8",
);

describe("review metadata line", () => {
  it("lists exactly the entries the create request sends", () => {
    const rows = [
      { key: "ticket", value: "TH-1" },
      { key: " batch ", value: "17" },
      { key: "source", value: "sdk" },
      { key: "", value: "dropped" },
    ];
    expect(metadataObject(rows)).toEqual({ ticket: "TH-1", batch: "17" });
    expect(metadataLine(rows)).toBe("ticket: TH-1 · batch: 17");
  });

  it("shows nothing when the run has no metadata", () => {
    expect(metadataLine([])).toBeNull();
    expect(metadataLine(undefined)).toBeNull();
    expect(metadataLine([{ key: "source", value: "ui" }])).toBeNull();
  });

  it("never shows a metadata entry the server does not store", () => {
    expect(reviewStep).not.toContain('"source: ui"');
    expect(reviewStep).not.toMatch(/value="ui"/);
    expect(reviewStep).toMatch(
      /\{scopeShown && metaLine \? \(\s*<Line>\s*<Typography typography="s2" color="text\.secondary" noWrap>\s*\{metaLine\}/,
    );
  });
});
