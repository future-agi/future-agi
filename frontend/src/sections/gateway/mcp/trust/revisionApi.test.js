import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  decideRevision,
  listRevisions,
  readPinConfig,
  writePin,
} from "./revisionApi";
import { revision } from "./revisionFixture";
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
beforeEach(() => {
  vi.clearAllMocks();
});
describe("revision transport boundaries", () => {
  it("bounds every read and write with the existing gateway 30-second request budget", async () => {
    vi.mocked(axios.get).mockResolvedValue({
      data: { status: true, result: { revisions: [], gateway_synced: true } },
    });
    vi.mocked(axios.post).mockResolvedValue({
      data: { status: true, result: { ...revision, status: "approved" } },
    });
    await listRevisions("org-a");
    await readPinConfig("gw-1", "org-a");
    await decideRevision("approve", revision, "org-a");
    await writePin("gw-1", "org-a", { pins: {} });
    for (const [, options] of vi.mocked(axios.get).mock.calls)
      expect(options).toMatchObject({
        timeout: 30000,
        headers: { "X-Organization-Id": "org-a" },
      });
    for (const [, , options] of vi.mocked(axios.post).mock.calls)
      expect(options).toEqual({
        timeout: 30000,
        headers: { "X-Organization-Id": "org-a" },
      });
  });
  it("does not send unscoped writes or unknown decisions", async () => {
    await expect(writePin("gw-1", null, {})).rejects.toThrow(
      "Select an organization",
    );
    await expect(decideRevision("delete", revision, "org-a")).rejects.toThrow(
      "Unknown review action",
    );
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("rejects a mismatched revision ID even in a successful envelope", async () => {
    vi.mocked(axios.post).mockResolvedValue({
      data: {
        status: true,
        result: { ...revision, id: 99, status: "approved" },
      },
    });
    await expect(decideRevision("approve", revision, "org-a")).rejects.toThrow(
      "did not confirm",
    );
  });
  it("does not reinterpret a malformed revision list as empty", async () => {
    vi.mocked(axios.get).mockResolvedValue({
      data: { status: true, result: { revisions: null, gateway_synced: true } },
    });
    await expect(listRevisions("org-a")).rejects.toThrow("did not confirm");
  });
});
