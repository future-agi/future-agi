import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { SURFACE_BY_ID } from "src/launch/launchSurfaces";
import { datasetListColumns } from "../datasetListColumns";
import {
  datasetListCatalog,
  sortingFromParam,
  sortParamFrom,
} from "../datasetList";
import { datasetCaps } from "../realBackendCaps";
import {
  realDatasetListParams,
  realDatasetListResult,
} from "../hooks/useDatasetListQuery";
import { OwnerCell, SourceCell } from "../DatasetListCells";

const state = (enabled) =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: enabled ? ["dataset.list.columns"] : [],
  });
const extras = ["owner", "source", "updatedAt"];
const deferred = ["version", "baseline", "bestRun", "lastExperiment"];
afterEach(() => setLaunchOverrideForTest(null));

describe("real dataset list metadata columns", () => {
  it("has an independent hidden capability owned by ORG", () => {
    expect(SURFACE_BY_ID["dataset.list.columns"]).toMatchObject({
      ships: "hidden",
      madeRealBy: "ORG",
    });
    state(false);
    expect(datasetCaps(true).listColumns).toBe(false);
    state(true);
    expect(datasetCaps(true).listColumns).toBe(true);
  });

  it("adds only backed optional columns when claimed and removes stale layout doors otherwise", () => {
    for (const enabled of [false, true]) {
      state(enabled);
      // Only column metadata is read; cell callbacks never consume the omitted props.
      const columns = datasetListColumns(
        /** @type {Parameters<typeof datasetListColumns>[0]} */ ({
          real: true,
          rowActions: {},
        }),
      );
      const catalog = datasetListCatalog([], true);
      for (const id of extras) {
        expect(columns.some((column) => column.id === id)).toBe(enabled);
        expect(catalog.some((column) => column.id === id)).toBe(enabled);
      }
      for (const id of deferred) {
        expect(columns.some((column) => column.id === id)).toBe(false);
        expect(catalog.some((column) => column.id === id)).toBe(false);
      }
      expect(
        catalog
          .filter((column) => column.defaultVisible !== false)
          .map((column) => column.id),
      ).toEqual(["name", "numberOfDatapoints", "createdAt"]);
    }
  });

  it("keeps updated sorting on the real request and round-trips URL state", () => {
    state(true);
    for (const direction of ["asc", "desc"]) {
      const value = `updated_at:${direction}`;
      expect(sortParamFrom(sortingFromParam(value))).toBe(value);
      expect(
        JSON.parse(
          // The adapter accepts an absent folderPath (typeof guard at its boundary).
          realDatasetListParams(
            /** @type {Parameters<typeof realDatasetListParams>[0]} */ ({
              sortBy: "updated_at",
              sortOrder: direction,
            }),
          ).sort,
        ),
      ).toEqual([
        {
          column_id: "updated_at",
          type: direction === "asc" ? "ascending" : "descending",
        },
      ]);
    }
  });

  it("keeps the int6 newest-created fallback for stale Updated sorts while unclaimed", () => {
    state(false);
    for (const direction of ["asc", "desc"]) {
      expect(
        JSON.parse(
          // The adapter accepts an absent folderPath (typeof guard at its boundary).
          realDatasetListParams(
            /** @type {Parameters<typeof realDatasetListParams>[0]} */ ({
              sortBy: "updated_at",
              sortOrder: direction,
            }),
          ).sort,
        ),
      ).toEqual([{ column_id: "created_at", type: "descending" }]);
    }
  });

  it("preserves actual metadata without inventing an owner or a version", () => {
    const raw = {
      id: "dataset",
      created_by: "owner-id",
      created_by_name: "Dataset owner",
      source: "build",
      updated_at: "2026-10-28T12:00:00+00:00",
    };
    const [row] = realDatasetListResult({ datasets: [raw] }).items;
    expect(row).toMatchObject(raw);
    expect(row.latest_version).toBeUndefined();
    render(<OwnerCell row={row} />);
    expect(screen.getByText("Dataset owner")).toBeVisible();
    expect(screen.getByText("Dataset owner")).toHaveAttribute(
      "title",
      "Dataset owner",
    );
  });

  it("labels the stored build origin without pretending every dataset was uploaded", () => {
    render(<SourceCell value="build" />);
    expect(screen.getByText("Build")).toBeVisible();
    expect(screen.queryByText("Upload")).not.toBeInTheDocument();
  });
});
