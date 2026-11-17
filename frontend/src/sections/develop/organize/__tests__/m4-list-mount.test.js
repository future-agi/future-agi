// M4 (serial merge, W9 list mount): the pure pieces DevelopView adds on top
// of integration's primitives: Group by ⇄ column ids, the rail's counts and the
// folder actions' subtree read (every page, never the visible one).
import { describe, expect, it, vi } from "vitest";
import {
  groupColumnOf,
  groupOfColumn,
} from "src/sections/develop/organize/datasetListView";
import {
  folderCondition,
  railCounts,
} from "src/sections/develop/organize/folderRailModel";
import { fetchFolderSubtree } from "src/sections/develop/organize/useFolderActions";
import { DATASET_ORG_COLUMNS, metadataKeyColumns } from "../../datasetList";

describe("Group by and the list's columns are one setting", () => {
  it("maps each groupable column to its Group by field and back", () => {
    const columns = [
      ...DATASET_ORG_COLUMNS.filter((c) => c.groupable),
      ...metadataKeyColumns(["tier", "domain"]),
    ];
    columns.forEach((c) => {
      const group = groupOfColumn(c.id);
      expect(group).not.toBe("");
      expect(groupColumnOf(group)).toBe(c.id);
    });
    expect(groupOfColumn("agents")).toBe("agent");
    expect(groupOfColumn("meta:tier")).toBe("metadata.tier");
    expect(groupColumnOf("metadata.tier")).toBe("meta:tier");
  });

  it("has no column for fields that are not list columns", () => {
    expect(groupColumnOf("owner")).toBeNull();
    expect(groupColumnOf("")).toBeNull();
    expect(groupOfColumn("name")).toBe("");
    expect(groupOfColumn(null)).toBe("");
  });
});

describe("railCounts", () => {
  it("counts all, starred and no folder from one answer", () => {
    const answer = {
      total: 9,
      facets: {
        starred: [
          { value: false, count: 7 },
          { value: true, count: 2 },
        ],
      },
      folders: [
        { path: "evals", count: 1, total: 3 },
        { path: "evals/tools", count: 1, total: 2 },
        { path: "evals/tools/v2", count: 1, total: 1 },
        { path: "support", count: 1, total: 1 },
      ],
    };
    expect(railCounts(answer)).toEqual({
      allCount: 9,
      starredCount: 2,
      noFolderCount: 5,
    });
  });

  it("reads zero before the answer arrives", () => {
    expect(railCounts(undefined)).toEqual({
      allCount: 0,
      starredCount: 0,
      noFolderCount: 0,
    });
  });
});

describe("fetchFolderSubtree", () => {
  it("reads every page of `folder is under <path>`", async () => {
    const pages = [
      {
        datasets: [
          { id: "a", name: "A", folder: "evals" },
          { id: "b", name: "B", folder: "evals/tools" },
        ],
        total_pages: 2,
      },
      { datasets: [{ id: "c", name: "C", folder: "evals/x" }], total_pages: 2 },
    ];
    const get = vi.fn(async (_url, { params }) => ({
      data: { result: pages[params.page] },
    }));
    const out = await fetchFolderSubtree("evals", get);
    expect(out.map((d) => d.id)).toEqual(["a", "b", "c"]);
    expect(get).toHaveBeenCalledTimes(2);
    const params = get.mock.calls[0][1].params;
    expect(params.page_size).toBe(100);
    expect(params.starred_first).toBe("false");
    expect(params.search_text).toBeUndefined();
    expect(JSON.parse(params.filters)).toEqual([folderCondition("evals")]);
  });

  it("stops after an empty answer", async () => {
    const get = vi.fn(async () => ({
      data: { result: { datasets: [], total_pages: 0 } },
    }));
    expect(await fetchFolderSubtree("none", get)).toEqual([]);
    expect(get).toHaveBeenCalledTimes(1);
  });
});
