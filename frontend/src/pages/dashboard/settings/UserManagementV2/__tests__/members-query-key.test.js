import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    rbac: {
      memberList: "/accounts/organization/members/",
      workspaceMemberList: (id) => `/accounts/workspace/${id}/members/`,
    },
  },
}));

import { endpoints } from "src/utils/axios";
import { getUserQueryKey, getUserQueryOptions } from "../getUserQueryOptions";

const params = {
  pageNumber: 0,
  sort: "-created_at",
  search: "teammate",
  filterStatus: ["Active"],
  filterRole: [],
  workspaceId: "workspace-1",
};
const workspaceEndpoint = endpoints.rbac.workspaceMemberList(params.workspaceId);

const optionsFor = (scope) =>
  getUserQueryOptions({
    ...params,
    endpoint: scope === "workspace" ? workspaceEndpoint : undefined,
  });

describe("Members query-key scope", () => {
  it("Q01: organization and workspace endpoints have different keys for the same workspace", () => {
    expect(optionsFor("organization").queryKey).not.toEqual(
      optionsFor("workspace").queryKey,
    );
  });

  it.each([
    ["Q02", "organization"],
    ["Q03", "workspace"],
  ])("%s: identical %s scope and params produce the same key", (_id, scope) => {
    const first = optionsFor(scope);
    const repeated = getUserQueryOptions({
      ...params,
      filterStatus: [...params.filterStatus],
      filterRole: [...params.filterRole],
      endpoint: scope === "workspace" ? workspaceEndpoint : undefined,
    });
    expect(repeated.queryKey).toEqual(first.queryKey);
  });

  it("Q04: implicit and explicit organization URLs share the canonical organization key", () => {
    const explicit = getUserQueryOptions({
      ...params,
      endpoint: endpoints.rbac.memberList,
    });
    expect(explicit.queryKey).toEqual(optionsFor("organization").queryKey);
  });

  it("Q05: the exported key helper includes the endpoint and preserves prefix invalidation", () => {
    const keyArgs = [
      params.pageNumber,
      params.sort,
      params.search,
      params.filterStatus,
      params.filterRole,
      params.workspaceId,
    ];
    const orgKey = getUserQueryKey(...keyArgs);
    const workspaceKey = getUserQueryKey(...keyArgs, workspaceEndpoint);
    expect(orgKey).not.toEqual(workspaceKey);
    expect(orgKey).toEqual(optionsFor("organization").queryKey);
    expect(workspaceKey).toEqual(optionsFor("workspace").queryKey);
    expect(orgKey[0]).toBe("user-detail");
    expect(workspaceKey[0]).toBe("user-detail");
  });

  it.each([
    ["Q06", "organization", "workspace"],
    ["Q07", "workspace", "organization"],
  ])("%s: fresh %s data cannot satisfy a %s fetch on the shared client", async (_id, firstScope, secondScope) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    const responses = {
      organization: { data: { result: { results: [{ id: "org-only-member" }] } } },
      workspace: { data: { result: { results: [{ id: "workspace-member" }] } } },
    };
    mocks.get.mockReset();
    mocks.get.mockImplementation(async (url) => {
      if (url === endpoints.rbac.memberList) return responses.organization;
      if (url === workspaceEndpoint) return responses.workspace;
      throw new Error(`Unexpected member endpoint: ${url}`);
    });

    try {
      expect(await client.fetchQuery(optionsFor(firstScope))).toEqual(
        responses[firstScope],
      );
      expect(await client.fetchQuery(optionsFor(secondScope))).toEqual(
        responses[secondScope],
      );
      expect(mocks.get).toHaveBeenCalledTimes(2);
      expect(mocks.get.mock.calls.map(([url]) => url)).toEqual([
        firstScope === "organization" ? endpoints.rbac.memberList : workspaceEndpoint,
        secondScope === "organization" ? endpoints.rbac.memberList : workspaceEndpoint,
      ]);
    } finally {
      client.clear();
    }
  });
});
