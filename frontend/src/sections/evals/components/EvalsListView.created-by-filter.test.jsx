/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalsListView from "./EvalsListView";

// W11-EVALS-LIST-SYSTEM-PLUS-PERSON: picking "System" together with people in
// Created By must ask the list API for System rows and those people's rows,
// and keep System in the filter the panel shows.
const state = vi.hoisted(() => ({
  listCalls: [],
  panel: null,
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
      throw new Error("Unexpected HTTP call in eval list filter test");
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
  DataTable: () => null,
  DataTablePagination: () => null,
}));
vi.mock("src/components/filter", async (importOriginal) => ({
  ...(await importOriginal()),
  ObjectFilterPanel: (props) => {
    state.panel = props;
    return null;
  },
}));
vi.mock("./DisplayOptionsPopover", () => ({
  default: () => null,
  DEFAULT_COLUMN_ORDER: ["name", "createdByName"],
}));
vi.mock("src/sections/project/VolumeBarChart", () => ({ default: () => null }));
vi.mock("./ErrorRateSparkline", () => ({ default: () => null }));

const PERSON = "Ada Author";

const applyFilter = (result) => {
  act(() => {
    state.panel.onApply(result);
  });
};

const lastListCall = () => state.listCalls.at(-1);

beforeEach(() => {
  state.listCalls = [];
  state.panel = null;
  localStorage.clear();
});
afterEach(cleanup);

describe("eval list Created By filter", () => {
  it.each([
    ["Basic tab", { owner: ["System", PERSON] }],
    [
      "Query tab",
      [{ field: "owner", operator: "is", value: ["System", PERSON] }],
    ],
  ])(
    "%s: System plus a person asks for System rows and the person's rows",
    (_tab, result) => {
      render(<EvalsListView />);
      applyFilter(result);

      expect(lastListCall().ownerFilter).toBe("all");
      expect(lastListCall().filters.created_by).toEqual(
        expect.arrayContaining(["System", PERSON]),
      );
      expect(lastListCall().filters.created_by).toHaveLength(2);
      expect(state.panel.currentFilters.owner).toEqual(
        expect.arrayContaining(["System", PERSON]),
      );
      expect(state.panel.currentFilters.owner).toHaveLength(2);
    },
  );

  it("sends System as the server shows it when typed in lower case", () => {
    render(<EvalsListView />);
    applyFilter([
      { field: "owner", operator: "is", value: ["system", PERSON] },
    ]);

    expect(lastListCall().ownerFilter).toBe("all");
    expect(lastListCall().filters.created_by).toEqual(
      expect.arrayContaining(["System", PERSON]),
    );
    expect(lastListCall().filters.created_by).not.toContain("system");
  });

  it("keeps System alone as the system owner scope", () => {
    render(<EvalsListView />);
    applyFilter({ owner: ["System"] });

    expect(lastListCall().ownerFilter).toBe("system");
    expect(lastListCall().filters?.created_by).toBeUndefined();
    expect(state.panel.currentFilters.owner).toEqual(["System"]);
  });

  it("keeps people alone as the user owner scope", () => {
    render(<EvalsListView />);
    applyFilter({ owner: [PERSON] });

    expect(lastListCall().ownerFilter).toBe("user");
    expect(lastListCall().filters.created_by).toEqual([PERSON]);
    expect(state.panel.currentFilters.owner).toEqual([PERSON]);
  });

  it("excludes System and the person when both are negated", () => {
    render(<EvalsListView />);
    applyFilter({ owner_not: ["System", PERSON] });

    expect(lastListCall().ownerFilter).toBe("user");
    expect(lastListCall().filters.created_by_not).toEqual([PERSON]);
    expect(state.panel.currentFilters.owner_not).toEqual(
      expect.arrayContaining(["System", PERSON]),
    );
  });
});
