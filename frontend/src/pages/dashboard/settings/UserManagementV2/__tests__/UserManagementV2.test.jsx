import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";

import { render } from "src/utils/test-utils";
import UserManagementV2 from "../UserManagementV2";

const mocks = vi.hoisted(() => ({ grid: vi.fn(), get: vi.fn() }));

vi.mock("../GridTable", () => ({
  default: React.forwardRef(function GridTable(props, _ref) {
    mocks.grid(props);
    return null;
  }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    rbac: {
      workspaceMemberList: (id) => `/accounts/workspace/${id}/members/`,
    },
  },
}));
vi.mock("src/utils/logger", () => ({ default: { warn: vi.fn() } }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({}) }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-1" }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
vi.mock("../UserManagementStore", () => ({
  useUserManagementStore: () => ({ setUsersList: vi.fn() }),
}));
vi.mock("../AllActionForm", () => ({ default: () => null }));
vi.mock("../UserHeaders", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const renderGrid = (page) => {
  const client = new QueryClient();
  render(
    <HelmetProvider>
      <QueryClientProvider client={client}>{page}</QueryClientProvider>
    </HelmetProvider>,
  );
  return mocks.grid.mock.lastCall[0];
};

const orgRoleColumn = (grid) =>
  grid.columnDefs.find(({ colId }) => colId === "org_level");

describe("UserManagementV2 Organisation Role sorting", () => {
  beforeEach(() => vi.clearAllMocks());

  it("disables sorting in workspace scope", () => {
    const grid = renderGrid(<UserManagementV2 workspaceScope />);
    expect(orgRoleColumn(grid).sortable).toBe(false);
  });

  it("keeps sorting enabled in organisation scope", () => {
    const grid = renderGrid(<UserManagementV2 />);
    expect(orgRoleColumn(grid).sortable).not.toBe(false);
  });

  it("requests the default sort for a workspace org_level sort", async () => {
    mocks.get.mockResolvedValueOnce({ data: { results: [], total: 0 } });
    const grid = renderGrid(<UserManagementV2 workspaceScope />);
    const api = { setGridOption: vi.fn() };
    grid.onGridReady({ api });
    const [, datasource] = api.setGridOption.mock.calls.find(
      ([option]) => option === "serverSideDatasource",
    );
    const success = vi.fn();
    await datasource.getRows({
      request: {
        startRow: 0,
        sortModel: [{ colId: "org_level", sort: "asc" }],
      },
      api,
      success,
      fail: vi.fn(),
    });

    expect(mocks.get).toHaveBeenCalledWith(
      "/accounts/workspace/ws-1/members/",
      expect.objectContaining({
        params: expect.objectContaining({ sort: "-created_at" }),
      }),
    );
    expect(success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
  });
});
