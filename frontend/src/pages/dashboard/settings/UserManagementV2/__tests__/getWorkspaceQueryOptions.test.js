import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    workspace: {
      workspaceList: "/accounts/workspace/list/",
    },
  },
}));

import { getWorkspaceQueryOptions } from "../getWorkspaceQueryOptions";

const mockWorkspaceList = (count) => {
  const workspaces = Array.from({ length: count }, (_, index) => ({
    id: `w${index + 1}`,
    display_name: `Workspace ${index + 1}`,
  }));

  // Model the backend's one-based page/limit pagination, not a fixed response.
  mocks.get.mockImplementation(async (_url, { params: { page, limit } }) => ({
    data: {
      count: workspaces.length,
      results: workspaces.slice((page - 1) * limit, page * limit),
    },
  }));

  return workspaces;
};

// GridTable requests 20-row blocks and WorkSpaceManagement maps startRow / 20
// to pageNumber. Exercise the real query helper against a paginated API stub.
describe("H15-C25-001 workspace directory pagination", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("supplies all 15 advertised workspaces in the first 20-row block", async () => {
    const workspaces = mockWorkspaceList(15);
    const options = getWorkspaceQueryOptions({
      pageNumber: 0,
      sort: [],
      search: "",
    });

    const { data } = await options.queryFn();

    expect(data.count).toBe(15);
    expect(data.results).toEqual(workspaces);
    expect(data.results).toHaveLength(data.count);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/accounts/workspace/list/", {
      params: { page: 1, limit: 20, sort: [], search: "" },
    });
  });

  it.each([0, 1, 2])(
    "supplies the exact 20-row block for zero-based page %i, including the final partial block",
    async (pageNumber) => {
      const workspaces = mockWorkspaceList(45);
      const { data } = await getWorkspaceQueryOptions({
        pageNumber,
        sort: [],
        search: "",
      }).queryFn();

      expect(data.count).toBe(45);
      expect(data.results).toEqual(
        workspaces.slice(pageNumber * 20, (pageNumber + 1) * 20),
      );
      expect(mocks.get).toHaveBeenCalledTimes(1);
      expect(mocks.get).toHaveBeenCalledWith("/accounts/workspace/list/", {
        params: { page: pageNumber + 1, limit: 20, sort: [], search: "" },
      });
    },
  );
});
