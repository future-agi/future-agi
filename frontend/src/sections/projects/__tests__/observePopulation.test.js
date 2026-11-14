import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ readQuery: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => http.readQuery(...args),
  endpoints: { project: { getUsersList: () => "/users" } },
}));

import { collectPopulation } from "../observePopulation";

describe("collectPopulation of the Users view", () => {
  beforeEach(() => {
    http.readQuery.mockReset();
    http.readQuery.mockResolvedValue({
      data: {
        result: {
          table: [{ user_id: "u1", end_user_id: "e1" }],
          metadata: { total_rows: 1 },
        },
      },
    });
  });

  it("sends the view's search, as the Users list does", async () => {
    await collectPopulation({
      unit: "users",
      projectId: "p1",
      filters: [],
      search: "  ada ",
      limit: 10,
    });
    expect(http.readQuery.mock.calls[0][1].params).toMatchObject({
      project_id: "p1",
      search: "ada",
    });
  });

  it("sends no search when there is none", async () => {
    await collectPopulation({ unit: "users", projectId: "p1", limit: 10 });
    expect(http.readQuery.mock.calls[0][1].params).not.toHaveProperty("search");
  });
});
