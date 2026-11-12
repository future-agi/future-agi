import { describe, expect, it } from "vitest";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";
import {
  INVALID_PILL_LABEL,
  invalidFilter,
  partitionInboundFilters,
  rowIdsFilter,
  rowIdsPillLabel,
  sourceKindFilter,
  sourceKindPillLabel,
  sourceProjectFilter,
  sourceProjectPillLabel,
  systemFilters,
} from "../dataGridView";

describe("R2 system filters", () => {
  it("build the system:row_failed shape", () => {
    expect(rowIdsFilter(["r1", "r2", "r1"])).toEqual({
      column_id: "system:row_ids",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: ["r1", "r2"],
      },
    });
    expect(
      rowIdsFilter(["r1"], { label: "Failure mode: wrong order id" })
        .filter_config.label,
    ).toBe("Failure mode: wrong order id");
    expect(sourceKindFilter("session")).toEqual({
      column_id: "system:source_kind",
      filter_config: {
        filter_type: "categorical",
        filter_op: "equals",
        filter_value: "session",
      },
    });
    expect(
      sourceProjectFilter("P1", { name: "support-prod" }).filter_config,
    ).toMatchObject({
      filter_value: "P1",
      label: "support-prod",
    });
    expect(invalidFilter()).toEqual({
      column_id: "system:invalid",
      filter_config: {
        filter_type: "boolean",
        filter_op: "equals",
        filter_value: true,
      },
    });
  });

  it("systemFilters stays the same without R2 fields and adds them in order", () => {
    expect(systemFilters({ failedOnly: true })).toHaveLength(1);
    const all = systemFilters({
      failedOnly: true,
      rowIds: ["r1"],
      sourceKind: "trace",
      sourceProject: "P1",
      sourceProjectName: "support-prod",
      invalidOnly: true,
    });
    expect(all.map((f) => f.column_id)).toEqual([
      "system:row_failed",
      "system:row_ids",
      "system:source_kind",
      "system:source_project",
      "system:invalid",
    ]);
  });

  it("API filters carry no pill labels, so the real serializer accepts them", () => {
    const api = systemFilters({
      rowIds: ["r1", "r2"],
      rowIdsLabel: "Failure mode: wrong order id",
      sourceKind: "session",
      sourceProject: "P1",
      sourceProjectName: "support-prod",
      invalidOnly: true,
    });
    api.forEach((f) => expect(f.filter_config).not.toHaveProperty("label"));
    expect(() => serializeFilterListForApi(api)).not.toThrow();
  });

  it("parses them back from a drill-down", () => {
    const parts = partitionInboundFilters([
      rowIdsFilter(["r1", "r2"], { label: "Failure mode: x" }),
      sourceKindFilter("session"),
      sourceProjectFilter("P1", { name: "support-prod" }),
      invalidFilter(),
      {
        column_id: "C1",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: "a",
        },
      },
    ]);
    expect(parts).toMatchObject({
      rowIds: ["r1", "r2"],
      rowIdsLabel: "Failure mode: x",
      sourceKind: "session",
      sourceProject: "P1",
      sourceProjectName: "support-prod",
      invalidOnly: true,
    });
    expect(parts.columnFilters).toHaveLength(1);
  });

  it("names the pills", () => {
    expect(rowIdsPillLabel(["a", "b", "c", "d", "e", "f"])).toBe("Rows (6)");
    expect(
      rowIdsPillLabel(Array(13).fill("x"), "Failure mode: wrong order id"),
    ).toBe("Failure mode: wrong order id (13)");
    expect(sourceKindPillLabel("session")).toBe("Source: Session");
    expect(sourceKindPillLabel("simulation")).toBe("Source: Simulation");
    expect(sourceProjectPillLabel("P1", "support-prod")).toBe(
      "Project: support-prod",
    );
    expect(INVALID_PILL_LABEL).toBe("Invalid rows only");
  });
});
