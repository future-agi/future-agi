import { describe, expect, it } from "vitest";
import { isolateOptions } from "../isolate";
import runs from "./dsRuns.fixture.json";

const by = (label) => runs.find((r) => r.label === label);

describe("isolateOptions (DS #1A → #2B)", () => {
  const out = isolateOptions(by("#1A"), by("#2B"), runs);
  it("counts two changes", () => {
    expect(out.changed).toBe(2);
  });
  it("finds #2A for the prompt change", () => {
    const prompt = out.options.find((o) => o.axis === "prompt");
    expect(prompt.run.label).toBe("#2A");
    expect(prompt.text).toBe(
      'Prompt V3 → V4 alone: #2A, −2.1 pts (within noise). The model change carries most of the +7.1 pts; run "model only" to confirm.',
    );
  });
  it("offers to run the model change with an estimate", () => {
    const model = out.options.find((o) => o.axis === "model");
    expect(model.action).toBe("run");
    expect(model.text).toBe("Model only: not run yet");
    expect(model.estimate.total_usd).toBe(0.740001);
    expect(model.estimateText).toBe("≈ $0.740001 · about 40 s");
  });
  it("has nothing to isolate for one change or different kinds", () => {
    expect(isolateOptions(by("#1A"), by("#2A"), runs).options).toEqual([]);
    expect(isolateOptions(by("#2B"), by("#4A"), runs).options).toEqual([]);
  });
});
