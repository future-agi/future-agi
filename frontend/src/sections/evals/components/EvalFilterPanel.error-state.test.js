import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";

import { describe, expect, it } from "vitest";

const read = (path) => readFileSync(resolve(process.cwd(), path), "utf8");

describe("EvalFilterPanel AI failure state", () => {
  it("uses the shared panel, which shows the failure and offers manual filtering", () => {
    const wrapper = read("src/sections/evals/components/EvalFilterPanel.jsx");
    const panel = read("src/sections/projects/LLMTracing/TraceFilterPanel.jsx");

    expect(wrapper).toContain("showAi");
    expect(panel).toContain("AI unavailable, use filters below");
    // No local parser quietly standing in for a failed AI request.
    expect(wrapper).not.toContain("parseNaturalLanguage");
    expect(panel).not.toContain("using local parser");
  });
});
