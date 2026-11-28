import { describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { apiMutator } from "../openapi-mutator";
import { validateContractedResponse } from "../openapi-contract";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
const url = "/tracer/v1/attachment-media/session/occurrence/grant";
const validate = (status, data) =>
  validateContractedResponse({ status, data, config: { url, method: "GET" } });

describe("attachment media binary response", () => {
  it("receives bytes without Axios decoding them as text", async () => {
    const data = new Blob([new Uint8Array([0, 255])]);
    axios.get.mockResolvedValueOnce({ data });
    expect(
      (await apiMutator(url, { method: "GET", responseType: "blob" })).data,
    ).toBe(data);
    expect(axios.get).toHaveBeenCalledWith(url, { responseType: "blob" });
  });
  it.each([200, 206])(
    "accepts binary but not a JSON error for %s",
    (status) => {
      expect(validate(status, new Blob(["bytes"])).ok).toBe(true);
      expect(validate(status, { detail: "error" }).ok).toBe(false);
    },
  );
  it.each([304, 404, 416, 429])(
    "requires an empty response for %s",
    (status) => {
      expect(validate(status, new Blob([])).ok).toBe(true);
      expect(validate(status, new Blob(["unexpected"])).ok).toBe(false);
    },
  );
});
