import { describe, expect, it } from "vitest";
import { runScopeBody } from "../runScopeBody";
import { wireExperimentBody } from "../serverBody";

const id = "11111111-1111-4111-8111-111111111111";
const settings = {
  row_ids: [id],
  sample: { n: 1, seed: 17 },
  metadata: { purpose: "selection" },
};

describe("experiment row scope", () => {
  it("maps the sample count without mutating the wizard values", () => {
    const original = structuredClone(settings);
    expect(runScopeBody(settings)).toEqual({
      ...settings,
      sample: { size: 1, seed: 17 },
    });
    expect(settings).toEqual(original);
  });

  it("sends picked rows, sampling and metadata through the real contract", () => {
    const wire = wireExperimentBody(settings, {}, false);
    expect(wire.blocks).toEqual([]);
    expect(wire.body).toEqual({
      ...settings,
      sample: { size: 1, seed: 17 },
    });
  });

  it("sends row policies but still refuses dataset-version pinning", () => {
    const wire = wireExperimentBody(
      {
        dataset_version_id: id,
        error_policy: "exclude",
        limits: { tokens: 1 },
      },
      {},
      false,
    );
    expect(wire.blocks.map((block) => block.field).sort()).toEqual([
      "dataset_version_id",
    ]);
    expect(wire.body).toEqual({
      error_policy: "exclude",
      limits: { tokens: 1 },
    });
  });

  it("does not rewrite the prototype wire body", () => {
    expect(wireExperimentBody(settings, {}, true).body).toBe(settings);
  });
});
