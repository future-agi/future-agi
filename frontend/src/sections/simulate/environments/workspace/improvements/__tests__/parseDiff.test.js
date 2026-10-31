import { describe, it, expect } from "vitest";

import { parseDiff } from "../parseDiff";

const DIFF = [
  "diff --git a/instructions.md b/instructions.md",
  "--- a/instructions.md",
  "+++ b/instructions.md",
  "@@ -1,3 +1,4 @@",
  " keep this",
  "-drop this",
  "+add this",
  "+and this",
  "diff --git a/tools.json b/tools.json",
  "--- a/tools.json",
  "+++ b/tools.json",
  "@@ -5,2 +5,2 @@",
  '-  "timeout": 5',
  '+  "timeout": 10',
  "",
].join("\n");

describe("parseDiff", () => {
  it("splits a unified diff into files with typed lines", () => {
    const files = parseDiff(DIFF);
    expect(files.map((f) => f.file)).toEqual(["instructions.md", "tools.json"]);
    const types = (f) => f.lines.map((l) => l.type);
    expect(types(files[0])).toEqual(["ctx", "remove", "add", "add"]);
    expect(types(files[1])).toEqual(["remove", "add"]);
    expect(files[0].lines[2]).toEqual({ type: "add", text: "add this" });
  });

  it("returns nothing for an empty patch", () => {
    expect(parseDiff("")).toEqual([]);
  });
});
