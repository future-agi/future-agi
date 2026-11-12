// Regressions found when the attachment UI first ran in Chromium against the
// app's real axios instance (dev-mode request contracts) and the media types
// the gateway now validates and serves.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import SpanAttachments from "../SpanAttachments";
import { AttachmentUnavailable, resolveAttachments } from "../client";
import {
  grant,
  occurrenceId,
  parent,
  projectId,
  readyGrant,
  spanImage,
} from "./fixtures";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("../client", async (importOriginal) => ({
  ...(await importOriginal()),
  resolveAttachments: vi.fn(),
}));

const traceId = "30000000-0000-0000-0000-000000000003";
const spanId = "4000000000000004";
const span = (attachments) => ({
  id: spanId,
  trace: traceId,
  name: "llm",
  start_time: "2026-11-11T10:00:00Z",
  attachments,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});

describe("media-session exchange request contract", () => {
  it("sends a body the generated request contract accepts for every call", async () => {
    axios.post.mockImplementation(async (path) =>
      path.endsWith("attachment-media-sessions")
        ? { status: 204 }
        : { data: { items: [grant] } },
    );
    const actual = await vi.importActual("../client");
    await actual.resolveAttachments(projectId, [
      { occurrence_id: occurrenceId, parent, representation: "playable" },
    ]);
    expect(axios.post).toHaveBeenCalledTimes(2);
    for (const [url, data] of axios.post.mock.calls) {
      const result = validateContractedRequestConfig({
        url,
        method: "post",
        data,
        headers: { "Content-Type": "application/json" },
      });
      expect(result.error?.message).toBeUndefined();
      expect(result.ok).toBe(true);
    }
    // An absent body is what the development interceptor rejected.
    expect(
      validateContractedRequestConfig({
        url: "/tracer/v1/attachment-media-sessions",
        method: "post",
        data: undefined,
        headers: {},
      }).ok,
    ).toBe(false);
  });
});

describe("span attachments with the resolver mocked", () => {
  const resolve = resolveAttachments;
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  });

  it("shows a failed image batch as unavailable with a retry, never an endless skeleton", async () => {
    resolve.mockRejectedValueOnce(new AttachmentUnavailable());
    resolve.mockImplementation(async (_, items) =>
      items.map((item) => readyGrant(item.occurrence_id)),
    );
    const { container } = render(
      <SpanAttachments
        projectId={projectId}
        span={span([spanImage(0)])}
        allSpans={[]}
      />,
    );
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    expect(container.querySelector(".MuiSkeleton-root")).toBeNull();
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(container.querySelector("img")?.getAttribute("src")).toMatch(
        /^\/tracer\/v1\/attachment-media\//,
      ),
    );
  });

  it.each([
    ["audio/ogg", "Ogg"],
    ["audio/mpeg", "MP3"],
  ])(
    "streams %s (%s) through the playable representation on request",
    async (mimeType) => {
      resolve.mockImplementation(async (_, items) =>
        items.map((item) => readyGrant(item.occurrence_id)),
      );
      const row = spanImage(0, {
        reference: {
          modality: "audio",
          mime_type: mimeType,
          direction: "output",
        },
      });
      const { container } = render(
        <SpanAttachments
          projectId={projectId}
          span={span([row])}
          allSpans={[]}
        />,
      );
      expect(resolve).not.toHaveBeenCalled();
      expect(container.querySelector("audio")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: /Load audio/ }));
      await waitFor(() =>
        expect(container.querySelector("audio")?.getAttribute("src")).toMatch(
          /^\/tracer\/v1\/attachment-media\//,
        ),
      );
      expect(resolve.mock.calls[0][1][0].representation).toBe("playable");
      expect(container.querySelector("audio").getAttribute("preload")).toBe(
        "metadata",
      );
    },
  );
});
