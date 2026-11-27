import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  warn: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({ default: { warn: mocks.warn } }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    rbac: {
      memberList: "/accounts/organization/members/",
    },
  },
}));

import {
  ensureCanonicalMemberListSort,
  getUserQueryOptions,
} from "../getUserQueryOptions";
import {
  gridSortModelToMemberListSort,
  safeGridSortModelToMemberListSort,
} from "../memberListGridQuery";

describe("getUserQueryOptions", () => {
  beforeEach(() => {
    mocks.get.mockClear();
  });

  it("serializes member filters as repeated canonical snake_case params", async () => {
    mocks.get.mockResolvedValueOnce({
      data: { result: { results: [], total: 0 } },
    });

    const options = getUserQueryOptions({
      pageNumber: 0,
      sort: "-created_at",
      search: "",
      filterStatus: ["Active", "Pending"],
      filterRole: ["org_8"],
    });

    await options.queryFn();

    const config = mocks.get.mock.calls[0][1];
    expect(config.paramsSerializer.serialize(config.params)).toBe(
      "page=1&sort=-created_at&search=&limit=20&filter_status=Active&filter_status=Pending&filter_role=org_8",
    );
  });

  it("converts AG Grid sort models at the UI boundary", async () => {
    expect(gridSortModelToMemberListSort([])).toBe("-created_at");
    expect(
      gridSortModelToMemberListSort([{ colId: "email", sort: "asc" }]),
    ).toBe("email");
    expect(
      gridSortModelToMemberListSort([{ colId: "created_at", sort: "desc" }]),
    ).toBe("-created_at");
    expect(
      gridSortModelToMemberListSort([{ colId: "ws_level", sort: "asc" }], {
        workspaceScope: true,
      }),
    ).toBe("ws_level");
  });

  it("keeps API query options on backend-shaped sort params only", async () => {
    expect(ensureCanonicalMemberListSort("org_level")).toBe("org_level");
    expect(ensureCanonicalMemberListSort("")).toBe("-created_at");
    expect(() => ensureCanonicalMemberListSort([])).toThrow(
      "backend sort query string",
    );
  });

  it("rejects unsupported grid sort fields before reaching the API", async () => {
    expect(() =>
      gridSortModelToMemberListSort([{ colId: "wsRole", sort: "desc" }], {
        workspaceScope: true,
      }),
    ).toThrow("Unsupported member list sort field");
    expect(() =>
      gridSortModelToMemberListSort([{ colId: "ws_level", sort: "desc" }]),
    ).toThrow("Unsupported member list sort field");
  });

  it("falls back to the default sort for unsupported grid sort fields", () => {
    const orgRoleAsc = [{ colId: "org_level", sort: "asc" }];
    const orgRoleDesc = [{ colId: "org_level", sort: "desc" }];
    mocks.warn.mockClear();
    expect(
      safeGridSortModelToMemberListSort(orgRoleAsc, { workspaceScope: true }),
    ).toBe("-created_at");
    expect(mocks.warn).toHaveBeenCalledTimes(1);
    expect(safeGridSortModelToMemberListSort(orgRoleDesc)).toBe("-org_level");
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });

  it("sends canonical sort params from query options", async () => {
    mocks.get.mockResolvedValueOnce({
      data: { result: { results: [], total: 0 } },
    });

    const options = getUserQueryOptions({
      pageNumber: 0,
      sort: "-org_level",
      search: "",
      filterStatus: [],
      filterRole: [],
    });

    await options.queryFn();

    const config = mocks.get.mock.calls[0][1];
    expect(config.params.sort).toBe("-org_level");
    expect(config.paramsSerializer.serialize(config.params)).toBe(
      "page=1&sort=-org_level&search=&limit=20",
    );
  });
});
