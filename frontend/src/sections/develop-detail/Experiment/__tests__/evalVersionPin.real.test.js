// L4 (ver:pin-card, editor:version-select): an eval card's version pin is a
// real backend field. The V2 create / edit store config.version_pin (or
// pinned_version_id) on the eval, and the detail read returns
// pinned_version_id and pinned_version_number, so a reopened experiment's
// card names the version that runs.
import { describe, expect, it } from "vitest";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import { versionPinOf } from "src/sections/common/EvalPicker/allowNaModel";
import { valuesFromExperiment } from "../experimentFormValues";

const V2 = "3f1c2b9a-8d7e-4c6b-9a5f-0e1d2c3b4a52";
const TEMPLATE = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";

const detailWith = (metric) => ({
  name: "Pinned judge run",
  column_id: null,
  prompt_configs: [],
  runs: [],
  user_eval_metrics: [
    {
      id: "uem-1",
      template_id: TEMPLATE,
      name: "Correctness",
      config: { mapping: { output: "c1" }, version_pin: "v2" },
      model: "turing_small",
      ...metric,
    },
  ],
});

describe("L4 eval version pin: detail to form", () => {
  it("a pinned eval comes back as the card's pin and 'V2 · pinned' chip", () => {
    const values = valuesFromExperiment(
      detailWith({ pinned_version_id: V2, pinned_version_number: 2 }),
      { mode: "edit" },
    );
    const [card] = values.userEvalMetrics;
    expect(card.pinnedVersionId).toBe(V2);
    expect(card.version).toBe("V2 · pinned");
    // The editor reopens on the same version (it reads config.version_pin).
    expect(card.config.version_pin).toBe("v2");
    expect(card.actualEvalCreatedId).toBe("uem-1");
    // The chip is the one the editor's version select writes for V2.
    expect(card.version).toBe(
      versionPinOf(V2, [{ id: V2, version_number: 2 }]).versionPinLabel,
    );
  });

  it("an unpinned eval carries no pin and no chip", () => {
    const values = valuesFromExperiment(
      detailWith({
        pinned_version_id: null,
        pinned_version_number: null,
        config: { mapping: { output: "c1" } },
      }),
      { mode: "edit" },
    );
    const [card] = values.userEvalMetrics;
    expect(card.pinnedVersionId).toBeUndefined();
    expect(card.version).toBeUndefined();
  });

  it("a duplicate keeps the pin but not the eval's id", () => {
    const values = valuesFromExperiment(
      detailWith({ pinned_version_id: V2, pinned_version_number: 2 }),
      { mode: "duplicate" },
    );
    const [card] = values.userEvalMetrics;
    expect(card.pinnedVersionId).toBe(V2);
    expect(card.version).toBe("V2 · pinned");
    expect(card.actualEvalCreatedId).toBeUndefined();
  });
});

describe("L4 eval version pin: the API contract carries it", () => {
  const create = (entry) => ({
    url: "/model-hub/experiments/v2/",
    method: "post",
    data: {
      name: "Pinned judge run",
      dataset_id: "0b7c5a52-6f0e-4a8e-9a43-2a1f0e1d3c11",
      prompt_config: [],
      user_eval_metrics: [
        {
          template_id: TEMPLATE,
          name: "Correctness",
          config: { mapping: { output: "c1" }, version_pin: "v2" },
          ...entry,
        },
      ],
    },
  });

  it("an eval entry may name its version by id, or clear it with null", () => {
    expect(validateContractedRequestConfig(create({})).ok).toBe(true);
    expect(
      validateContractedRequestConfig(create({ pinned_version_id: V2 })).ok,
    ).toBe(true);
    expect(
      validateContractedRequestConfig(create({ pinned_version_id: null })).ok,
    ).toBe(true);
  });

  it("a pinned_version_id that is not a version id is refused", () => {
    expect(
      validateContractedRequestConfig(create({ pinned_version_id: "v2" })).ok,
    ).toBe(false);
  });
});
