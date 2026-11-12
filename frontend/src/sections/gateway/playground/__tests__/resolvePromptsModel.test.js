import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolvePromptsModel } from "../resolvePromptsModel";
import { laterPageCatalogs, modelListResponse } from "./promptsModelFixtures";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));
beforeEach(() => get.mockReset());

describe("exact and complete Prompts model lookup", () => {
  it.each(laterPageCatalogs)("finds $id at substring-search position $position", async ({ id, rows, exact }) => {
    get.mockImplementation(async (_url, options) => modelListResponse(rows, options));
    await expect(resolvePromptsModel(id)).resolves.toBe(exact);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/model-hub/api/models_list/", expect.objectContaining({
      params: { name: id }, timeout: 15000, signal: expect.any(AbortSignal),
    }));
  });

  it("prefers the full exact ID over a same-provider bare name", async () => {
    const { exact } = laterPageCatalogs[0];
    const bare = { ...exact, model_name: "gpt-4" };
    get.mockImplementation(async (_url, options) => modelListResponse([bare, exact], options));
    await expect(resolvePromptsModel("azure/gpt-4")).resolves.toBe(exact);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("falls back to an exact bare name from the same provider", async () => {
    const row = { model_name: "gpt-4", providers: "openai" };
    get.mockImplementation(async (_url, options) => modelListResponse([row], options));
    await expect(resolvePromptsModel("openai/gpt-4")).resolves.toBe(row);
    expect(get.mock.calls.map(([, { params }]) => params)).toEqual([
      { name: "openai/gpt-4" },
      { name: "gpt-4" },
    ]);
  });

  it("does not match a bare name from another provider", async () => {
    get.mockImplementation(async (_url, options) =>
      modelListResponse([{ model_name: "gpt-4", providers: "openai" }], options),
    );
    await expect(resolvePromptsModel("azure/gpt-4")).resolves.toBeNull();
  });

  it("does not accept substring-only results", async () => {
    get.mockResolvedValue({ data: { results: [{ model_name: "azure/gpt-4-mini", providers: "azure" }] } });
    await expect(resolvePromptsModel("azure/gpt-4")).resolves.toBeNull();
  });

  it("returns absence only after both exact-name queries succeed", async () => {
    get.mockResolvedValue({ data: { results: [] } });
    await expect(resolvePromptsModel("azure/missing")).resolves.toBeNull();
    expect(get.mock.calls.map(([, { params }]) => params.name)).toEqual(["azure/missing", "missing"]);
  });

  it("checks a bare ID once and skips an empty ID", async () => {
    get.mockResolvedValue({ data: { results: [] } });
    await expect(resolvePromptsModel("")).resolves.toBeNull();
    expect(get).not.toHaveBeenCalled();
    await expect(resolvePromptsModel("missing")).resolves.toBeNull();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("propagates a lookup failure (fallback=%s)", async (fallback) => {
    const error = new Error("unavailable");
    get.mockRejectedValue(error);
    if (fallback) get.mockResolvedValueOnce({ data: { results: [] } });
    await expect(resolvePromptsModel("azure/gpt-4")).rejects.toBe(error);
  });

  it.each([undefined, {}, { results: null }, { results: {} }, { status: false, results: [] }])(
    "does not turn a malformed response into absence: %s",
    async (data) => {
      get.mockResolvedValue({ data });
      await expect(resolvePromptsModel("azure/gpt-4")).rejects.toThrow();
    },
  );
});
