import { describe, expect, it } from "vitest";
import {
  addSourceOf,
  datasetImportOf,
  routeOfLink,
} from "../addScenarioRoutes";
import { draftSourcesOf } from "../../../_mock/traceReview";

const params = (q) => new URLSearchParams(q);

describe("routeOfLink", () => {
  it("keeps old links working", () => {
    expect(routeOfLink("production", params(""))).toBe("error-feed");
    expect(routeOfLink("replay", params("add=replay&from=observe&ids=a"))).toBe(
      "observe",
    );
    expect(routeOfLink("replay", params("add=replay"))).toBe("files");
    expect(routeOfLink("dataset", params(""))).toBe("dataset");
    expect(routeOfLink(null, params(""))).toBe(null);
  });
});

describe("addSourceOf", () => {
  it("records imports under their provenance", () => {
    expect(addSourceOf("observe", [])).toBe("production");
    expect(
      addSourceOf("observe", [{ traceImport: { sourceKind: "simCall" } }]),
    ).toBe("simulation");
    expect(
      addSourceOf("files", [{ traceImport: { sourceKind: "dataset" } }]),
    ).toBe("dataset-import");
    expect(
      addSourceOf("files", [{ traceImport: { sourceKind: "transcript" } }]),
    ).toBe("transcript");
    expect(addSourceOf("generate", [])).toBe("generate");
  });
});

describe("datasetImportOf", () => {
  const rows = [
    {
      id: "e1-ds-r1",
      title: "Refund a double charge",
      task: "Refund a double charge",
      expected: "Refund once",
      datasetRow: { dataset: "d1", rowId: "r_1" },
    },
    {
      id: "e1-ds-r2",
      title: "Cancel an order",
      task: "Cancel an order",
      datasetRow: { dataset: "d1", rowId: "r_2" },
    },
  ];
  it("makes one reviewed import whose drafts name their row", () => {
    const out = datasetImportOf(
      rows,
      { name: "Support tickets" },
      {
        id: "imp_x",
        at: "2026-11-23T10:00:00.000Z",
      },
    );
    expect(out).toHaveLength(2);
    expect(out.every((r) => r.traceImport.id === "imp_x")).toBe(true);
    expect(out[0].traceImport).toMatchObject({
      sourceKind: "dataset",
      project: "Support tickets",
      ids: ["r_1", "r_2"],
      sourceIds: ["r_1"],
    });
    expect(out[0].proposed.source).toBe("dataset-import");
    /* The review finds each draft's evidence. */
    const src = draftSourcesOf(out[1], {}, {});
    expect(src).toHaveLength(1);
    expect(src[0].traceId).toBe("r_2");
    expect(src[0].turns[0].text).toBe("Cancel an order");
  });
});
