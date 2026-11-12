import { describe, expect, it } from "vitest";
import { buildCatalogRows, catalogFacets, filterRows } from "./catalogRows";

const rows = buildCatalogRows([
  { id: "own/audio", vendor: "own", supported_parameters: ["seed", "logprobs"], input_modalities: ["text", "audio"] },
  { id: "own/structured", vendor: "own", supported_parameters: ["response_format"], input_modalities: ["text"] },
  { id: "own/unknown", vendor: "own" },
]);
const ids = (filters) => filterRows(rows, filters).map((row) => row.id);

describe("exact catalog metadata facets", () => {
  it("requires every selected reported parameter and input modality, without inferring support", () => {
    expect(ids({ parameters: ["seed", "logprobs"], inputModalities: ["audio"] })).toEqual(["own/audio"]);
    expect(ids({ parameters: ["structured_outputs"] })).toEqual([]);
    expect(ids({ parameters: ["response_format"] })).toEqual(["own/structured"]);
    expect(ids({ inputModalities: ["audio", "image"] })).toEqual([]);
    expect(ids({ inputModalities: ["__not_reported__"] })).toEqual(["own/unknown"]);
    expect(ids({ parameters: ["__not_reported__"] })).toEqual(["own/unknown"]);
  });

  it("builds searchable exact labels, intersection counts and a distinct Not reported choice", () => {
    const facets = catalogFacets(rows, { inputModalities: ["audio"] });
    const params = facets.find((group) => group.key === "parameters");
    expect(params).toMatchObject({ searchable: true, every: true });
    expect(params.options).toEqual(expect.arrayContaining([
      expect.objectContaining({ value: "seed", label: "seed", count: 1 }),
      expect.objectContaining({ value: "response_format", count: 0 }),
      expect.objectContaining({ value: "__not_reported__", label: "Not reported", count: 0 }),
    ]));
    const selected = catalogFacets(rows, { parameters: ["future_parameter"] }).find((group) => group.key === "parameters");
    expect(selected.options).toContainEqual(expect.objectContaining({ value: "future_parameter", count: 0 }));
  });
});
