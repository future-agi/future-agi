// HO D11: get_evals_list carries pinned_version_id; the saved eval's line
// says the version is pinned (and stays as it was without one).
import { describe, expect, it } from "vitest";
import { evalDefinitionLine } from "../SavedEvalsList";

describe("HO D11 · evalDefinitionLine", () => {
  const base = { higher_is_better: true, output_type: "score", version: "v1" };

  it("marks a pinned version", () => {
    expect(
      evalDefinitionLine({ ...base, pinned_version_id: "ver-1" }).text,
    ).toBe("↑ Higher is better · v1 · pinned");
  });

  it("is unchanged without a pin", () => {
    expect(evalDefinitionLine(base).text).toBe("↑ Higher is better · v1");
    expect(evalDefinitionLine({ ...base, pinned_version_id: null }).text).toBe(
      "↑ Higher is better · v1",
    );
  });

  it("keeps the threshold and the newer-version note", () => {
    const out = evalDefinitionLine({
      ...base,
      pass_threshold: 0.7,
      latest_version: "v2",
      pinned_version_id: "ver-1",
    });
    expect(out.text).toBe("↑ Higher is better · Pass ≥ 0.70 · v1 · pinned");
    expect(out.newer).toBe("v1 → v2 available");
  });
});

describe("L7c g7 · newer version notice", () => {
  it("names a newer template version, never an older one", async () => {
    const { evalDefinitionLine: line, newestOf } = await import(
      "../SavedEvalsList"
    );
    expect(newestOf("v2", "v3")).toBe("v3");
    expect(newestOf("v3", "v2")).toBe("v3");
    expect(newestOf(undefined, "v3")).toBe("v3");
    expect(
      line({ higher_is_better: true, version: "v2", latest_version: "v3" })
        .newer,
    ).toBe("v2 → v3 available");
    expect(
      line({ higher_is_better: true, version: "v3", latest_version: "v2" })
        .newer,
    ).toBeNull();
  });
});

describe("L7c g7 · output chip", () => {
  it("a score eval reads Scoring, never Choices", async () => {
    const { outputChipOf } = await import("../SavedEvalsList");
    expect(outputChipOf("score").label).toBe("Scoring");
    expect(outputChipOf("percentage").label).toBe("Scoring");
    expect(outputChipOf("pass_fail").label).toBe("Pass/fail");
    expect(outputChipOf("deterministic").label).toBe("Choices");
    expect(outputChipOf(undefined)).toBeNull();
  });
});
