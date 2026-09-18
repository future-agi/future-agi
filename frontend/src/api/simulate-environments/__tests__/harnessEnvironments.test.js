import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { results: [] } })),
    delete: vi.fn(() => Promise.resolve({ data: null })),
  },
}));

import axios from "src/utils/axios";
import {
  listHarnessEnvironments,
  deleteHarnessEnvironment,
} from "../harnessEnvironments";

const BASE = "/simulate/api/harness-environments/";

describe("listHarnessEnvironments", () => {
  beforeEach(() => {
    axios.get.mockClear();
  });

  it("GETs the environments list with the paging params", async () => {
    await listHarnessEnvironments({ page: 2, limit: 50 });
    expect(axios.get).toHaveBeenCalledWith(BASE, {
      params: { page: 2, limit: 50 },
    });
  });

  it("omits paging params that are not given", async () => {
    await listHarnessEnvironments();
    expect(axios.get).toHaveBeenCalledWith(BASE, { params: {} });
  });

  it("returns the response data", async () => {
    axios.get.mockResolvedValueOnce({ data: { results: [{ id: "e1" }] } });
    const out = await listHarnessEnvironments();
    expect(out.results).toEqual([{ id: "e1" }]);
  });
});

describe("deleteHarnessEnvironment", () => {
  beforeEach(() => {
    axios.delete.mockClear();
  });

  it("DELETEs the environment by id", async () => {
    await deleteHarnessEnvironment("env-9");
    expect(axios.delete).toHaveBeenCalledWith(`${BASE}env-9/`);
  });
});
