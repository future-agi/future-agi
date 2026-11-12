import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SpanAttachments from "../SpanAttachments";
import ScreenshotFilmstrip from "../ScreenshotFilmstrip";
import { resolveAttachments } from "../client";
import { collectScreenshots } from "../attachmentPlacement";
import { projectId, readyGrant, spanImage } from "./fixtures";

vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));

const traceId = "30000000-0000-0000-0000-000000000003";
const spanId = "4000000000000004";
const parent = { trace_id: traceId, span_id: spanId };
const span = (attachments, extra = {}) => ({
  id: spanId,
  trace: traceId,
  name: "llm",
  start_time: "2026-11-11T10:00:00Z",
  attachments,
  ...extra,
});
const grantAll = () =>
  resolveAttachments.mockImplementation(async (_, items) =>
    items.map((item) => readyGrant(item.occurrence_id)),
  );
const shot = (index, extra = {}) =>
  spanImage(index, {
    reference: { purpose: "computer_screenshot", direction: "output" },
    ...extra,
  });

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  grantAll();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
  vi.useRealTimers();
});

describe("contextual span attachments (U01)", () => {
  it("renders nothing and requests nothing when the projection is absent", () => {
    const { container } = render(
      <SpanAttachments
        projectId={projectId}
        span={span(undefined)}
        allSpans={[]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(resolveAttachments).not.toHaveBeenCalled();
  });

  it("resolves every ready image of a span in ONE original request and places them by message", async () => {
    const items = [
      spanImage(0),
      spanImage(1),
      spanImage(2, { reference: { direction: "output", message_index: 1 } }),
    ];
    render(
      <SpanAttachments
        projectId={projectId}
        span={span(items)}
        allSpans={[]}
      />,
    );
    const images = await screen.findAllByRole("img");
    expect(images).toHaveLength(3);
    expect(resolveAttachments).toHaveBeenCalledTimes(1);
    const [calledProject, requests] = resolveAttachments.mock.calls[0];
    expect(calledProject).toBe(projectId);
    expect(requests).toEqual(
      items.map((item) => ({
        occurrence_id: item.reference.occurrence_id,
        parent,
        representation: "original",
      })),
    );
    expect(images[0].getAttribute("src")).toMatch(
      /^\/tracer\/v1\/attachment-media\//,
    );
    expect(images[0].getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(screen.getByText("Input · message 1 · part 1")).toBeInTheDocument();
    expect(screen.getByText("Output · message 2 · part 3")).toBeInTheDocument();
  });

  it("shows coarse states for pending, partial and failed images without resolving them", async () => {
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([
          spanImage(0, { status: "pending_upload" }),
          spanImage(1, { status: "partial", capture_complete: false }),
          spanImage(2, { status: "failed" }),
        ])}
        allSpans={[]}
      />,
    );
    expect(screen.getByText("Uploading")).toBeInTheDocument();
    expect(screen.getByText("Incomplete capture")).toBeInTheDocument();
    expect(screen.getByText("Could not be prepared")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(resolveAttachments).not.toHaveBeenCalled();
  });

  it("shows a resolver denial as unavailable without disclosing why", async () => {
    resolveAttachments.mockResolvedValue([
      {
        occurrence_id: spanImage(0).reference.occurrence_id,
        status: "not_found",
      },
    ]);
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([spanImage(0)])}
        allSpans={[]}
      />,
    );
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("replaces a broken image with a retry that re-resolves", async () => {
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([spanImage(0)])}
        allSpans={[]}
      />,
    );
    fireEvent.error(await screen.findByRole("img"));
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(resolveAttachments).toHaveBeenCalledTimes(2));
  });

  it("zooms with a FRESH grant and returns focus to the image", async () => {
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([spanImage(0)])}
        allSpans={[]}
      />,
    );
    const trigger = await screen.findByRole("button", {
      name: /Open Input · message 1 · part 1 image at full size/,
    });
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(resolveAttachments).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close image" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("never renders SVG or unknown image types even if a row claims ready", () => {
    const row = spanImage(0);
    row.reference.mime_type = "image/svg+xml";
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([row, spanImage(1)])}
        allSpans={[]}
      />,
    );
    // The SVG row is invalid under F01 and dropped; only the PNG is requested.
    expect(resolveAttachments.mock.calls[0][1]).toHaveLength(1);
  });

  it("rejects an invalid project or span id before any request", () => {
    const { container } = render(
      <SpanAttachments
        projectId="not-a-uuid"
        span={span([spanImage(0)])}
        allSpans={[]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([spanImage(0)], { id: "x" })}
        allSpans={[]}
      />,
    );
    expect(resolveAttachments).not.toHaveBeenCalled();
  });
});

describe("video, audio and file attachments (U04)", () => {
  const video = spanImage(0, {
    reference: {
      modality: "video",
      mime_type: "video/mp4",
      direction: "output",
    },
  });
  const pdf = spanImage(1, {
    reference: {
      modality: "document",
      mime_type: "application/pdf",
      purpose: "document",
    },
  });

  it("does not resolve or fetch media until the viewer asks, then streams from the gateway", async () => {
    const { container } = render(
      <SpanAttachments
        projectId={projectId}
        span={span([video])}
        allSpans={[]}
      />,
    );
    expect(resolveAttachments).not.toHaveBeenCalled();
    expect(container.querySelector("video")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Load video/ }));
    await waitFor(() =>
      expect(container.querySelector("video")?.getAttribute("src")).toMatch(
        /^\/tracer\/v1\/attachment-media\//,
      ),
    );
    expect(resolveAttachments.mock.calls[0][1][0].representation).toBe(
      "playable",
    );
    expect(container.querySelector("video").getAttribute("preload")).toBe(
      "metadata",
    );
  });

  it("downloads a PDF through a same-origin link and never embeds it", async () => {
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const { container } = render(
      <SpanAttachments
        projectId={projectId}
        span={span([pdf])}
        allSpans={[]}
      />,
    );
    expect(
      screen.getByText("Preview unavailable · download to inspect"),
    ).toBeInTheDocument();
    expect(container.querySelector("iframe, object, embed")).toBeNull();
    expect(resolveAttachments).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Download/ }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const link = click.mock.instances[0];
    expect(link.getAttribute("href")).toMatch(
      /^\/tracer\/v1\/attachment-media\//,
    );
    expect(link.download).toMatch(/^attachment-d0000000\.pdf$/);
    expect(resolveAttachments.mock.calls[0][1][0].representation).toBe(
      "original",
    );
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("keeps a denied download visible as unavailable and lets the viewer retry", async () => {
    resolveAttachments.mockResolvedValue([
      { occurrence_id: pdf.reference.occurrence_id, status: "deleted" },
    ]);
    render(
      <SpanAttachments
        projectId={projectId}
        span={span([pdf])}
        allSpans={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Download/ }));
    expect(await screen.findByText("Deleted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Download/ }));
    await waitFor(() => expect(resolveAttachments).toHaveBeenCalledTimes(2));
  });
});

describe("computer-use filmstrip (U03)", () => {
  const traceStart = "2026-11-11T10:00:00Z";
  const thousand = Array.from({ length: 1000 }, (_, i) => ({
    observation_span: {
      id: `5${String(i + 1).padStart(15, "0")}`,
      trace: traceId,
      name: `computer step ${i + 1}`,
      start_time: new Date(Date.parse(traceStart) + i * 1000).toISOString(),
      attachments: [shot(i)],
    },
    children: [],
  }));

  it("mounts a bounded window and resolves only the selected original for 1,000 steps", async () => {
    const frames = collectScreenshots(thousand);
    render(
      <ScreenshotFilmstrip
        projectId={projectId}
        frames={frames}
        traceStartTime={traceStart}
      />,
    );
    await screen.findByRole("img");
    expect(screen.getAllByRole("button", { name: /^Step \d+/ })).toHaveLength(
      15,
    );
    expect(resolveAttachments).toHaveBeenCalledTimes(1);
    expect(resolveAttachments.mock.calls[0][1]).toEqual([
      {
        occurrence_id: frames[0].item.reference.occurrence_id,
        parent: frames[0].parent,
        representation: "original",
      },
    ]);
    expect(screen.getByText("Step 1 of 1000")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Screenshots captured at each step, not a continuous recording.",
      ),
    ).toBeInTheDocument();
  });

  it("navigates by keyboard and resolves only where the selection settles", async () => {
    const frames = collectScreenshots(thousand);
    render(
      <ScreenshotFilmstrip
        projectId={projectId}
        frames={frames}
        traceStartTime={traceStart}
      />,
    );
    await screen.findByRole("img");
    const region = screen.getByRole("region", {
      name: "Computer-use screenshots",
    });
    for (let i = 0; i < 5; i += 1)
      fireEvent.keyDown(region, { key: "ArrowRight" });
    expect(screen.getByText("Step 6 of 1000")).toBeInTheDocument();
    await waitFor(() => expect(resolveAttachments).toHaveBeenCalledTimes(2));
    expect(resolveAttachments.mock.calls[1][1][0].occurrence_id).toBe(
      frames[5].item.reference.occurrence_id,
    );
    fireEvent.keyDown(region, { key: "End" });
    expect(screen.getByText("Step 1000 of 1000")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Next screenshot" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Step 1000/ })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByText(/\+999\.0 s/)).toBeInTheDocument();
  });

  it("links a step back to its source span", async () => {
    const onSelectSpan = vi.fn();
    const frames = collectScreenshots(thousand.slice(0, 3));
    render(
      <ScreenshotFilmstrip
        projectId={projectId}
        frames={frames}
        initialIndex={1}
        currentSpanId={frames[1].parent.span_id}
        onSelectSpan={onSelectSpan}
      />,
    );
    expect(screen.queryByRole("button", { name: "View span" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next screenshot" }));
    fireEvent.click(screen.getByRole("button", { name: "View span" }));
    expect(onSelectSpan).toHaveBeenCalledWith(frames[2].parent.span_id);
  });

  it("shows hidden or deleted steps as coarse states and does not resolve them", async () => {
    const frames = collectScreenshots([
      {
        observation_span: {
          id: spanId,
          trace: traceId,
          start_time: traceStart,
          attachments: [shot(0, { status: "failed" })],
        },
      },
    ]);
    vi.useFakeTimers();
    render(<ScreenshotFilmstrip projectId={projectId} frames={frames} />);
    await act(async () => vi.advanceTimersByTime(500));
    expect(screen.getByText("Could not be prepared")).toBeInTheDocument();
    expect(resolveAttachments).not.toHaveBeenCalled();
  });

  it("appears in the span pane for spans that own screenshots, starting at their first step", async () => {
    const tree = thousand.slice(0, 4);
    const own = tree[2].observation_span;
    render(
      <SpanAttachments
        projectId={projectId}
        span={own}
        allSpans={tree}
        traceStartTime={traceStart}
      />,
    );
    expect(await screen.findByText("Step 3 of 4")).toBeInTheDocument();
    // Screenshots are not duplicated as inline images.
    expect(screen.queryByText("Attachments")).toBeNull();
  });
});
