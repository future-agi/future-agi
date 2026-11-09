// W5 B6 (review M3): task compare's Setup counts and lists exactly the
// config diff's changes (CompareRuns/setupRows over configRows), so two
// runs that differ only in max tokens read `Differences only (1)` here, as
// Explore › Config reads `1 difference`.
import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { configRows } from "src/sections/common/CompareRuns/configRows";
import SetupTab from "../SetupTab";

const config = (maxTokens) => ({
  kind: "prompt",
  model: {
    id: "gpt-4o-mini",
    params: { temperature: 0.3, max_tokens: maxTokens },
  },
  prompt: { id: "p1", name: "Support Reply", version: "V4", messages: [] },
  dataset: { version: "v5", rows: 238 },
  evals: [{ id: "tone", name: "Tone", version: "v1" }],
  trials: 1,
});
const runs = {
  a: { run_id: "a", label: "#9A", kind: "prompt", config: config(400) },
  b: { run_id: "b", label: "#9B", kind: "prompt", config: config(800) },
};
const lanes = [
  { id: "a", label: "#9A", color: { solid: "#123" } },
  { id: "b", label: "#9B", color: { solid: "#456" } },
];

describe("SetupTab · Differences only", () => {
  it("max tokens only: 1 difference, the one row in bold", () => {
    render(<SetupTab lanes={lanes} runOf={(id) => runs[id]} />);
    const want = configRows(runs.a, runs.b).count;
    expect(want).toBe(1);
    expect(screen.getByTestId("tc-setup").dataset.count).toBe(String(want));
    expect(screen.getByText(`Differences only (${want})`)).toBeTruthy();
    // all settings first: context rows are never bold
    const before = [...document.querySelectorAll("[data-setup-cell]")].map(
      (e) => e.dataset.setupCell,
    );
    expect(before).toContain("Temperature:same");
    expect(before.filter((c) => c.endsWith(":differs"))).toEqual([
      "Max tokens:differs",
    ]);
    fireEvent.click(screen.getByLabelText("Differences only"));
    const after = [...document.querySelectorAll("[data-setup-cell]")].map(
      (e) => `${e.dataset.setupCell}=${e.textContent}`,
    );
    expect(after).toEqual(["Max tokens:same=400", "Max tokens:differs=800"]);
  });
});
