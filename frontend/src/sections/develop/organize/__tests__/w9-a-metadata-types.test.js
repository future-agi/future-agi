// W9 A2 review fix: metadata keys are typed over every listed dataset (the
// query's `metadata_types`), so while a search narrows the facets the
// toolbar keeps each key with the same type: `metadata.tier` stays a
// number field, `metadata.pii` true/false, even when no searched dataset
// holds them.
import { describe, expect, it } from "vitest";
import {
  allDatasetFields,
  metadataFields,
  metadataTypesOf,
} from "../datasetFields";
import { listFields } from "../datasetListView";
import { datasetQueryResult } from "../../hooks/useDatasetListQuery";

const byId = (fields) => Object.fromEntries(fields.map((f) => [f.id, f]));

describe("metadataTypesOf", () => {
  it("types each key by every value it holds across the items", () => {
    expect(
      metadataTypesOf([
        { metadata: { tier: 2, pii: false, region: "eu" } },
        { metadata: { tier: 3, region: "us" } },
        { metadata: {} },
        {},
      ]),
    ).toEqual({ pii: "boolean", region: "categorical", tier: "number" });
  });

  it("a key holding a number on one item and text on another is text", () => {
    expect(
      metadataTypesOf([{ metadata: { tier: 2 } }, { metadata: { tier: "x" } }]),
    ).toEqual({ tier: "categorical" });
    expect(metadataTypesOf(null)).toEqual({});
  });
});

describe("metadataFields with the list's types", () => {
  it("keeps a key the facets lack, typed by the list", () => {
    const fields = byId(metadataFields({}, { tier: "number", pii: "boolean" }));
    expect(fields["metadata.tier"]).toMatchObject({
      type: "number",
      label: "tier",
      metadata: true,
      groupable: true,
    });
    expect(fields["metadata.pii"].type).toBe("boolean");
  });

  it("the list's type wins over the facet values; facets alone still type", () => {
    const facets = { tier: [{ value: "2", count: 1 }], region: [] };
    const typed = byId(metadataFields(facets, { tier: "number" }));
    expect(typed["metadata.tier"].type).toBe("number");
    expect(typed["metadata.region"].type).toBe("categorical");
    const untyped = byId(metadataFields({ n: [{ value: 4, count: 1 }] }));
    expect(untyped["metadata.n"].type).toBe("number");
    expect(allDatasetFields(null, { tier: "number" }).at(-1).id).toBe(
      "metadata.tier",
    );
  });
});

describe("the toolbar under a search", () => {
  it("listFields offers metadata.tier as a number while the searched facets hold none", () => {
    const facets = { metadata: {} }; // the search's facets: no tier anywhere
    const fields = byId(listFields(facets, { tier: "number" }));
    expect(fields["metadata.tier"].type).toBe("number");
    expect(byId(listFields(facets))["metadata.tier"]).toBeUndefined();
  });

  it("the query result carries metadata_types as metadataTypes", () => {
    const res = datasetQueryResult({
      datasets: [],
      facets: { metadata: {} },
      metadata_types: { tier: "number" },
    });
    expect(res.metadataTypes).toEqual({ tier: "number" });
    // An answer without types keeps the shape it had; listFields then
    // types from the facets.
    expect(datasetQueryResult({})).not.toHaveProperty("metadataTypes");
  });
});
