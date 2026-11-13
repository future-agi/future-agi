import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { listAttachments, resolveAttachments } from "../client";
import { grant, occurrence, occurrenceId, parent, projectId } from "./fixtures";
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
const items = [
  { occurrence_id: occurrenceId, parent, representation: "playable" },
];
beforeEach(() => vi.resetAllMocks());
const respond = (results) =>
  axios.post.mockImplementation(async (path) =>
    path.endsWith("attachment-media-sessions")
      ? { status: 204 }
      : { data: { items: results } },
  );

describe("authorized attachment resolver", () => {
  it("uses scoped read POST and never requests source bytes", async () => {
    respond([grant]);
    const signal = new AbortController().signal;
    await expect(resolveAttachments(projectId, items, signal)).resolves.toEqual(
      [grant],
    );
    expect(axios.post).toHaveBeenCalledWith(
      `/tracer/v1/projects/${projectId}/attachments/resolve`,
      { items },
      expect.objectContaining({ signal, withCredentials: true }),
    );
    expect(axios.get).not.toHaveBeenCalled();
  });
  it.each([401, 403, 404, 500])(
    "does not disclose existence or raw server exceptions for %i",
    async (statusCode) => {
      axios.post.mockRejectedValue({
        statusCode,
        message: "canary signed source URL and filename",
      });
      await expect(resolveAttachments(projectId, items)).rejects.toThrow(
        "Attachment unavailable",
      );
      expect(axios.post).toHaveBeenCalledTimes(1);
    },
  );
  it("rejects a mismatched, extra or stale grant", async () => {
    for (const results of [
      [{ ...grant, occurrence_id: projectId }],
      [grant, grant],
      [{ ...grant, expires_at: "2000-01-01T00:00:00Z" }],
    ]) {
      respond(results);
      await expect(resolveAttachments(projectId, items)).rejects.toThrow(
        "Attachment unavailable",
      );
    }
  });
  it("validates IDs and parent before any network call", async () => {
    await expect(resolveAttachments("../foreign", items)).rejects.toThrow();
    await expect(
      resolveAttachments(projectId, [
        { ...items[0], parent: { ...parent, span_id: "0" } },
      ]),
    ).rejects.toThrow();
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("does not reuse grants across calls or viewers", async () => {
    respond([grant]);
    await resolveAttachments(projectId, items);
    respond([{ occurrence_id: occurrenceId, status: "not_found" }]);
    expect((await resolveAttachments(projectId, items))[0].status).toBe(
      "not_found",
    );
    expect(axios.post).toHaveBeenCalledTimes(4);
  });
  it("lists one bounded page without resolving or preloading bytes", async () => {
    axios.get.mockResolvedValue({
      data: { items: [occurrence], next_cursor: "next" },
    });
    expect(await listAttachments(projectId, parent, null)).toEqual({
      items: [occurrence],
      next_cursor: "next",
    });
    expect(axios.get).toHaveBeenCalledWith(
      `/tracer/v1/projects/${projectId}/attachments/occurrences`,
      expect.objectContaining({ params: { ...parent, limit: 25 } }),
    );
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("denies wrong-parent/project pages and source metadata", async () => {
    for (const item of [
      { ...occurrence, parent: { ...parent, span_id: "ffffffffffffffff" } },
      {
        ...occurrence,
        scope: { ...occurrence.scope, project_id: occurrenceId },
      },
      { ...occurrence, filename: "canary-secret.wav" },
    ]) {
      axios.get.mockResolvedValue({
        data: { items: [item], next_cursor: null },
      });
      await expect(listAttachments(projectId, parent, null)).rejects.toThrow(
        "Attachment unavailable",
      );
    }
  });
});
