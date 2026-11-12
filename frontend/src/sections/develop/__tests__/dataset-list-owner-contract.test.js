import { describe, expect, it } from "vitest";
import { ModelHubDevelopsGetDatasetsListResponse } from "src/generated/api-contracts/api.zod";

const response = (owner) => ({
  status: true,
  result: {
    datasets: [
      {
        id: "bc523a55-4378-4090-a0bd-d7041ca94082",
        name: "Dataset without a recorded owner",
        description: "",
        tags: [],
        starred: false,
        folder: "",
        folder_id: null,
        number_of_datapoints: 0,
        number_of_experiments: 0,
        number_of_optimisations: 0,
        derived_datasets: 0,
        created_at: "2026-10-28 12:00",
        dataset_type: "generative_llm",
        created_by: owner,
        created_by_name: owner === null ? null : "Dataset owner",
        source: "build",
        updated_at: "2026-10-28T12:00:00+00:00",
      },
    ],
    total_pages: 1,
    total_count: 1,
  },
});

describe("generated dataset owner contract", () => {
  it("accepts an explicitly missing recorded owner", () => {
    expect(
      ModelHubDevelopsGetDatasetsListResponse.safeParse(response(null)).success,
    ).toBe(true);
  });
  it("accepts the exact OFF payload without fabricating metadata", () => {
    const input = response(null);
    for (const key of [
      "created_by",
      "created_by_name",
      "source",
      "updated_at",
    ]) {
      delete input.result.datasets[0][key];
    }
    const parsed = ModelHubDevelopsGetDatasetsListResponse.parse(input);
    expect(parsed).toEqual(input);
  });
  it("retains ON metadata rather than silently stripping unknown fields", () => {
    const input = response("bc523a55-4378-4090-a0bd-d7041ca94083");
    expect(ModelHubDevelopsGetDatasetsListResponse.parse(input)).toEqual(input);
  });
  it("preserves a recorded folder UUID", () => {
    const input = response(null);
    input.result.datasets[0].folder = "Research";
    input.result.datasets[0].folder_id = "bc523a55-4378-4090-a0bd-d7041ca94084";
    expect(ModelHubDevelopsGetDatasetsListResponse.parse(input)).toEqual(input);
  });
  it("rejects an invalid or omitted folder identifier", () => {
    const input = response(null);
    input.result.datasets[0].folder_id = "not-a-folder-id";
    expect(
      ModelHubDevelopsGetDatasetsListResponse.safeParse(input).success,
    ).toBe(false);
    delete input.result.datasets[0].folder_id;
    expect(
      ModelHubDevelopsGetDatasetsListResponse.safeParse(input).success,
    ).toBe(false);
  });
  it("still validates recorded owner identifiers", () => {
    expect(
      ModelHubDevelopsGetDatasetsListResponse.safeParse(
        response("bc523a55-4378-4090-a0bd-d7041ca94083"),
      ).success,
    ).toBe(true);
    expect(
      ModelHubDevelopsGetDatasetsListResponse.safeParse(response("not-an-id"))
        .success,
    ).toBe(false);
  });
});
