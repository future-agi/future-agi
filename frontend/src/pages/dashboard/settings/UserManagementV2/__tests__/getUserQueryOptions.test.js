import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    rbac: {
      memberList: "/accounts/organization/members/",
    },
  },
}));

import { getUserQueryOptions } from "../getUserQueryOptions";

describe("getUserQueryOptions", () => {
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
});
