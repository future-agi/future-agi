import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { results: [] } })),
    delete: vi.fn(() => Promise.resolve({ data: null })),
    patch: vi.fn(() => Promise.resolve({ data: { id: "e1" } })),
    post: vi.fn(() => Promise.resolve({ data: { id: "e1" } })),
  },
}));

import axios from "src/utils/axios";
import {
  listHarnessEnvironments,
  deleteHarnessEnvironment,
  getHarnessEnvironment,
  renameHarnessEnvironment,
  deleteAppliedEvaluation,
  getAvailableEvaluations,
  addEvaluation,
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
  beforeEach(() => axios.delete.mockClear());

  it("DELETEs the eval config by id (path now in the generated contract)", async () => {
    await deleteAppliedEvaluation("env-9", "cfg-1");
    expect(axios.delete).toHaveBeenCalledWith(`${BASE}env-9/evaluations/cfg-1/`);
  });
});

describe("getAvailableEvaluations (§10)", () => {
  beforeEach(() => axios.get.mockClear());

  it("GETs the available-evals catalogue for the environment", async () => {
    await getAvailableEvaluations("env-10");
    expect(axios.get).toHaveBeenCalledWith(`${BASE}env-10/evaluations/available/`);
  });
});

describe("addEvaluation (§10)", () => {
  beforeEach(() => axios.post.mockClear());

  it("POSTs the eval name only (mapping is resolved server-side)", async () => {
    await addEvaluation("env-10", "advice_authority_boundary");
    expect(axios.post).toHaveBeenCalledWith(`${BASE}env-10/evaluations/`, {
      name: "advice_authority_boundary",
    });
  });
});
