// RED at 7e05e449: no binary loader or progress/abort/withdrawal handling exists.
import { afterEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { loadHubDeploymentSource } from "../api/deploymentSource";
import { fixture, PIN } from "./hubBundleFixtures";
vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
afterEach(() => vi.resetAllMocks());

describe("Hub download refusal and cancellation", () => {
  it.each(["https://invalid.example/archive", "aa/name@1.0.0\n", "aa/name"])(
    "rejects %s without any network request",
    async (ref) => {
      await expect(loadHubDeploymentSource(ref)).rejects.toThrow();
      expect(axios.get).not.toHaveBeenCalled();
    },
  );
  it("aborts the transport when progress exceeds declared bytes", async () => {
    const { metadata } = await fixture();
    axios.get
      .mockResolvedValueOnce({ data: metadata })
      .mockImplementationOnce(async (_, options) => {
        options.onDownloadProgress({ loaded: metadata.bundleBytes + 1 });
        expect(options.signal.aborted).toBe(true);
        throw new Error("cancelled transport");
      });
    await expect(loadHubDeploymentSource(PIN)).rejects.toThrow(/limit|size/i);
    expect(axios.get).toHaveBeenCalledTimes(2);
  });
  it("passes external cancellation to an in-flight authenticated request", async () => {
    const controller = new AbortController();
    axios.get.mockImplementationOnce(async (_, options) => {
      controller.abort();
      expect(options.signal.aborted).toBe(true);
      throw new Error("transport cancelled");
    });
    await expect(
      loadHubDeploymentSource(PIN, { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(axios.get).toHaveBeenCalledTimes(1);
  });
  it("refuses the yank response header even when the earlier metadata was approved", async () => {
    const data = await fixture();
    axios.get
      .mockResolvedValueOnce({ data: data.metadata })
      .mockResolvedValueOnce({
        data: data.archive,
        headers: { "x-omega-hub-yanked": "" },
      });
    await expect(loadHubDeploymentSource(PIN)).rejects.toThrow(/yanked/i);
  });
  it("does not silently accept mutation of an exact pin during transfer", async () => {
    const data = await fixture();
    axios.get
      .mockResolvedValueOnce({ data: data.metadata })
      .mockResolvedValueOnce({ data: data.archive, headers: {} })
      .mockResolvedValueOnce({
        data: { ...data.metadata, digest: `sha256:${"0".repeat(64)}` },
      });
    await expect(loadHubDeploymentSource(PIN)).rejects.toThrow(/changed/i);
  });
});
