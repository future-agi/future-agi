import React from "react";
import axios from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";

import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  gridProps: null,
}));

vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => false,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: vi.fn() },
  endpoints: {
    workspace: { workspaceList: "/accounts/workspace/list/" },
    workspaces: { create: "/accounts/workspaces/" },
  },
}));

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));

vi.mock("../GridTable", () => ({
  default: React.forwardRef(function GridTable(props) {
    mocks.gridProps = props;
    return null;
  }),
}));
vi.mock("../AllActionForm", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import WorkSpaceManagement from "../WorkSpaceManagement";

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <WorkSpaceManagement />
      </QueryClientProvider>
    </HelmetProvider>,
  );
};

const requestWorkspaces = async (sortModel) => {
  renderPage();
  const api = { setGridOption: vi.fn(), showLoadingOverlay: vi.fn() };
  mocks.gridProps.onGridReady({ api });
  const [, datasource] = api.setGridOption.mock.calls.find(
    ([option]) => option === "serverSideDatasource",
  );
  const success = vi.fn();
  const fail = vi.fn();

  await datasource.getRows({
    request: { startRow: 20, sortModel },
    api,
    success,
    fail,
  });

  expect(fail).not.toHaveBeenCalled();
  expect(success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
  expect(mocks.get).toHaveBeenCalledTimes(1);
  const [url, config] = mocks.get.mock.calls[0];
  expect(url).toBe("/accounts/workspace/list/");

  // Exercise the real Axios serializer, not JSON.stringify or a hand-built
  // query. No request is sent; only the app's transport get() is mocked.
  const query = new URL(
    axios.getUri({ ...config, url }),
    "https://workspace.test",
  ).searchParams;
  expect(query.get("page")).toBe("2");
  expect(query.get("limit")).toBe("20");
  expect(query.get("search")).toBe("");
  return query;
};

describe("H15-C25-002 workspace directory sorting", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockResolvedValue({ data: { results: [], count: 0 } });
    mocks.gridProps = null;
  });

  it.each([
    ["display_name", "asc", "display_name"],
    ["display_name", "desc", "-display_name"],
    ["start_data", "asc", "created_at"],
    ["start_data", "desc", "-created_at"],
  ])("serializes %s %s as scalar sort=%s", async (colId, sort, expected) => {
    const query = await requestWorkspaces([{ colId, sort }]);

    // WorkspaceListRequestSerializer rejects all bracket-expanded sort keys.
    expect([...query.keys()].sort()).toEqual(["limit", "page", "search", "sort"]);
    expect(query.getAll("sort")).toEqual([expected]);
  });

  it.each([
    { label: "empty", sortModel: [] },
    { label: "missing", sortModel: undefined },
  ])("omits sort for a $label sort model", async ({ sortModel }) => {
    const query = await requestWorkspaces(sortModel);

    expect([...query.keys()].sort()).toEqual(["limit", "page", "search"]);
  });

  it.each(["admin_names.0.name", "last_update_date"])(
    "does not send unsupported ordering for %s",
    async (colId) => {
      const query = await requestWorkspaces([{ colId, sort: "asc" }]);

      expect([...query.keys()].sort()).toEqual(["limit", "page", "search"]);
    },
  );

  it("uses only the primary sort if a multi-column model is supplied", async () => {
    const query = await requestWorkspaces([
      { colId: "display_name", sort: "desc" },
      { colId: "start_data", sort: "asc" },
    ]);

    expect([...query.keys()].sort()).toEqual(["limit", "page", "search", "sort"]);
    expect(query.getAll("sort")).toEqual(["-display_name"]);
  });

  it("offers only supported single-column sorts", () => {
    renderPage();

    expect(
      Object.fromEntries(
        mocks.gridProps.columnDefs.map(({ field, sortable }) => [field, sortable]),
      ),
    ).toEqual({
      display_name: true,
      "admin_names.0.name": false,
      start_data: true,
      last_update_date: false,
    });
    expect(mocks.gridProps.otherGridOption.suppressMultiSort).toBe(true);
    expect(mocks.gridProps.otherGridOption.selectionColumnDef.sortable).toBe(false);
  });
});
