import axios from "axios";
import { describe, expect, it, vi } from "vitest";
import { assertContractedRequestConfig } from "src/api/contracts/openapi-contract";
import { toEvalUpdatePayload } from "../useEvalDetail";

vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));

// Match the real request interceptor in src/utils/axios.js. The adapter is the
// network boundary; a rejected contract must never reach it.
describe("Unit: evaluation save contract", () => {
  it("reproduces the old blank-model failure before PUT and accepts the dev payload repair", async () => {
    const adapter = vi
      .fn()
      .mockResolvedValue({
        status: 200,
        data: { result: {} },
        headers: {},
        config: {},
      });
    const client = axios.create({ adapter });
    client.interceptors.request.use(assertContractedRequestConfig);
    const url =
      "/model-hub/eval-templates/0b3c4c2e-5d0e-4c4e-9f43-3a9f3b8f2c11/update/";
    const payload = {
      eval_type: "code",
      model: "",
      tags: ["client-demo"],
      publish: true,
    };
    await expect(client.put(url, payload)).rejects.toMatchObject({
      name: "ApiContractValidationError",
    });
    expect(adapter).not.toHaveBeenCalled();
    await client.put(url, toEvalUpdatePayload(payload));
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(JSON.parse(adapter.mock.calls[0][0].data)).toEqual({
      eval_type: "code",
      tags: ["client-demo"],
      publish: true,
    });
  });
});
