import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { apiMutator } from "../openapi-mutator";

vi.mock("src/utils/axios", () => ({
  default: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

describe("OpenAPI mutator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("routes generated GET requests through axios.get", async () => {
    axios.get.mockResolvedValueOnce({ data: { ok: true } });

    await expect(
      apiMutator("/accounts/user-info/", { method: "GET" }),
    ).resolves.toEqual({ data: { ok: true } });

    expect(axios.get).toHaveBeenCalledWith("/accounts/user-info/", undefined);
  });

  it("routes generated JSON mutation requests with parsed body and headers", async () => {
    const abortController = new AbortController();
    axios.patch.mockResolvedValueOnce({ data: { status: true } });

    await apiMutator("/model-hub/annotation-queues/queue-1/", {
      method: "PATCH",
      signal: abortController.signal,
      headers: new Headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name: "Queue" }),
    });

    expect(axios.patch).toHaveBeenCalledWith(
      "/model-hub/annotation-queues/queue-1/",
      { name: "Queue" },
      {
        signal: abortController.signal,
        headers: { "content-type": "application/json" },
      },
    );
  });

  it("routes generated bodyless DELETE requests without silently treating unknown methods as DELETE", async () => {
    axios.delete.mockResolvedValueOnce({ data: { status: true } });

    await apiMutator("/model-hub/annotation-queues/queue-1/", {
      method: "DELETE",
    });

    expect(axios.delete).toHaveBeenCalledWith(
      "/model-hub/annotation-queues/queue-1/",
      undefined,
    );
  });

  it("fails closed for unsupported methods", async () => {
    await expect(
      apiMutator("/model-hub/annotation-queues/queue-1/", {
        method: "OPTIONS",
      }),
    ).rejects.toThrow("Unsupported OpenAPI method: OPTIONS");

    expect(axios.delete).not.toHaveBeenCalled();
  });
});
