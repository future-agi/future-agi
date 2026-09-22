import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { results: [] } })),
    delete: vi.fn(() => Promise.resolve({ data: null })),
    patch: vi.fn(() => Promise.resolve({ data: { id: "e1" } })),
  },
}));

import axios from "src/utils/axios";
import {
  listHarnessEnvironments,
  deleteHarnessEnvironment,
  getHarnessEnvironment,
  renameHarnessEnvironment,
  deleteAppliedEvaluation,
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

describe("getHarnessEnvironment (§6)", () => {
  beforeEach(() => axios.get.mockClear());

  it("GETs the environment detail by id (path already contracted)", async () => {
    await getHarnessEnvironment("env-6");
    expect(axios.get).toHaveBeenCalledWith(`${BASE}env-6/`);
  });
});

describe("renameHarnessEnvironment (§8)", () => {
  beforeEach(() => axios.patch.mockClear());

  it("PATCHes the environment with the new name only", async () => {
    await renameHarnessEnvironment("env-8", "Ride booking - voice");
    expect(axios.patch).toHaveBeenCalledWith(`${BASE}env-8/`, {
      name: "Ride booking - voice",
    });
  });
});

describe("deleteAppliedEvaluation (§9)", () => {
  // The evaluation path is new and not yet in the generated Swagger surface, so
  // apiPath() throws until the backend lands the endpoint and contracts:generate
  // runs. This documents that pending state; flip it to assert the DELETE once
  // the surface includes the path.
  it("throws until the eval path is in the generated contract", async () => {
    await expect(deleteAppliedEvaluation("env-9", "eval-1")).rejects.toThrow(
      /not in generated contract/,
    );
  });
});
