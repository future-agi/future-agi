/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalsListView from "./EvalsListView";

// W10-EVALS-U07: sorting by "Created By" must ask the list API to order by
// the creator the column shows, not by creation date.
const state = vi.hoisted(() => ({
  listCalls: [],
  columns: [],
  list: {
    items: [{ id: "eval-1", name: "User evaluation", owner: "user" }],
    total: 1,
  },
}));
vi.mock("../hooks/useEvalsList", () => ({
  useEvalsList: (params) => {
    state.listCalls.push(params);
    return { data: state.list, isLoading: false };
  },
  useEvalsListCharts: () => ({ data: null, isLoading: false }),
  useBulkDeleteEvals: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: () => ({ data: [] }),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(() => {
      throw new Error("Unexpected HTTP call in eval list sort test");
    }),
  },
  endpoints: { develop: { eval: { getEvalNames: "/eval-names/" } } },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, onSortingChange }) => {
    state.columns = columns;
    return (
      <>
        <button
          type="button"
          onClick={() =>
            onSortingChange([{ id: "createdByName", desc: false }])
          }
        >
          Sort by creator ascending
        </button>
        <button
          type="button"
          onClick={() => onSortingChange([{ id: "createdByName", desc: true }])}
        >
          Sort by creator descending
        </button>
      </>
    );
  },
  DataTablePagination: () => null,
}));
// EvalsListView renders ObjectFilterPanel from src/components/filter; the real
// panel reads the property catalog through react-query, which this test does
// not provide.
vi.mock("src/components/filter", async (importOriginal) => ({
  ...(await importOriginal()),
  ObjectFilterPanel: () => null,
}));
vi.mock("./DisplayOptionsPopover", () => ({
  default: () => null,
  DEFAULT_COLUMN_ORDER: ["name", "createdByName"],
}));
vi.mock("src/sections/project/VolumeBarChart", () => ({ default: () => null }));
vi.mock("./ErrorRateSparkline", () => ({ default: () => null }));

beforeEach(() => {
  state.listCalls = [];
  state.columns = [];
  localStorage.clear();
});
afterEach(cleanup);

describe("eval list Created By sort", () => {
  it("keeps the Created By column sortable", () => {
    render(<EvalsListView />);
    const column = state.columns.find(({ id }) => id === "createdByName");
    expect(column?.header).toBe("Created By");
    expect(column?.enableSorting).toBe(true);
  });

  it.each([
    ["Sort by creator ascending", "asc"],
    ["Sort by creator descending", "desc"],
  ])(
    "%s asks the list API for the creator sort key",
    (buttonName, sortOrder) => {
      render(<EvalsListView />);
      fireEvent.click(screen.getByRole("button", { name: buttonName }));
      const lastCall = state.listCalls.at(-1);
      expect(lastCall.sortBy).toBe("created_by_name");
      expect(lastCall.sortOrder).toBe(sortOrder);
    },
  );
});
