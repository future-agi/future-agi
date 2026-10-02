import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";

import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(
  resolve(process.cwd(), "src/sections/evals/components/EvalFilterPanel.jsx"),
  "utf8",
);

describe("EvalFilterPanel AI failure state", () => {
  it("offers retry or manual filtering without claiming a local fallback", () => {
    expect(SOURCE).toContain(
      "AI filter unavailable. Retry or build the filter manually.",
    );
    expect(SOURCE).not.toContain("using local parser");
  });
});
