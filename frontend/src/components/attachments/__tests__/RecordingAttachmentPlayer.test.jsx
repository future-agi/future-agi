import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RecordingAttachmentPlayer from "../RecordingAttachmentPlayer";
import AttachmentsList from "../AttachmentsList";
import { listAttachments, resolveAttachments } from "../client";
import {
  grant,
  mixedManifest,
  occurrence,
  occurrenceId,
  parent,
  projectId,
  projection,
  stereoManifest,
} from "./fixtures";
vi.mock("../client", () => ({
  resolveAttachments: vi.fn(),
  listAttachments: vi.fn(),
}));

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  resolveAttachments.mockResolvedValue([grant]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
  vi.useRealTimers();
});

describe("framework recording component", () => {
  it("creates one lazy native player for a two-hour mix and publishes transcript transport", async () => {
    const onInstance = vi.fn();
    const { container } = render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={projection}
        onInstance={onInstance}
      />,
    );
    await screen.findByRole("button", { name: "Play recording" });
    expect(container.querySelectorAll("audio")).toHaveLength(1);
    expect(container.querySelector("audio").getAttribute("preload")).toBe(
      "none",
    );
    expect(container.querySelector("audio").getAttribute("src")).toBeNull();
    expect(
      screen.getByText("Recording does not confirm what participants heard"),
    ).toBeInTheDocument();
    const { multitrack } = onInstance.mock.calls[0][0];
    act(() => multitrack.setTime(7198));
    expect(
      screen.getByRole("slider", { name: "Recording position" }),
    ).toHaveAttribute("aria-valuenow", "7198");
    expect(multitrack.getDuration()).toBe(7200);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await screen.findByRole("button", { name: "Pause recording" });
    expect(container.querySelector("audio").getAttribute("src")).toBe(
      grant.gateway_path,
    );
  });
  it("uses server channels and actual roles, without browser stereo splitting", async () => {
    resolveAttachments.mockResolvedValue([
      grant,
      { ...grant, gateway_path: `${grant.gateway_path}2` },
    ]);
    const { container } = render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={{ ...projection, manifest: stereoManifest }}
      />,
    );
    await screen.findByRole("button", { name: "Play recording" });
    expect(screen.getByText("Assistant · User")).toBeInTheDocument();
    expect(container.querySelectorAll("audio")).toHaveLength(2);
    expect(
      resolveAttachments.mock.calls[0][1].map((item) => item.channel),
    ).toEqual([0, 1]);
  });
  it.each([
    "disabled",
    "unsupported",
    "waiting",
    "pending_upload",
    "importing",
    "validating",
    "failed",
    "expired",
    "deleted",
    "rejected",
  ])("shows safe %s state with no resolver request", (state) => {
    const { container } = render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={{ ...projection, state, filename: "canary-secret.wav" }}
      />,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(container.textContent).not.toContain("canary-secret");
    expect(container.querySelector("audio")).toBeNull();
    expect(resolveAttachments).not.toHaveBeenCalled();
  });
  it("does not reveal existence or play half of a denied recording", async () => {
    resolveAttachments.mockResolvedValue([
      grant,
      { occurrence_id: occurrenceId, status: "not_found" },
    ]);
    const { container } = render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={{ ...projection, manifest: stereoManifest }}
      />,
    );
    await screen.findByText("Recording unavailable");
    expect(container.querySelector("audio")).toBeNull();
    expect(screen.queryByText("Assistant · User")).not.toBeInTheDocument();
  });
  it("labels partial capture and gaps without inventing playout evidence", async () => {
    render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={{
          ...projection,
          manifest: {
            ...mixedManifest,
            capture_complete: false,
            segments: [
              {
                ...mixedManifest.segments[0],
                start_ms: 1000,
                duration_ms: 29000,
              },
              ...mixedManifest.segments.slice(1),
            ],
            gaps: [{ start_ms: 0, duration_ms: 1000, reason: "truncated" }],
          },
        }}
      />,
    );
    await screen.findByText(
      "Partial recording — some audio is missing (truncated at this position).",
    );
  });
  it("aborts resolution and ignores late grants on parent switch/unmount", async () => {
    let finish;
    resolveAttachments.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const onInstance = vi.fn();
    const { unmount } = render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={projection}
        onInstance={onInstance}
      />,
    );
    const signal = resolveAttachments.mock.calls[0][2];
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => finish([grant]));
    expect(
      onInstance.mock.calls.every(([value]) => value.multitrack === null),
    ).toBe(true);
  });
  it("renews in place without resetting the transcript seek", async () => {
    vi.useFakeTimers();
    resolveAttachments.mockResolvedValue([
      { ...grant, expires_at: new Date(Date.now() + 20000).toISOString() },
    ]);
    const onInstance = vi.fn();
    render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={projection}
        onInstance={onInstance}
      />,
    );
    await act(async () => {});
    const player = onInstance.mock.calls[0][0].multitrack;
    act(() => player.setTime(7100));
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(resolveAttachments).toHaveBeenCalledTimes(2);
    expect(player.getCurrentTime()).toBe(7100);
    expect(onInstance).toHaveBeenCalledTimes(1);
  });
  it("clears bytes and transport when renewal is denied", async () => {
    vi.useFakeTimers();
    resolveAttachments
      .mockResolvedValueOnce([
        { ...grant, expires_at: new Date(Date.now() + 20000).toISOString() },
      ])
      .mockResolvedValueOnce([
        { occurrence_id: occurrenceId, status: "not_found" },
      ]);
    const onInstance = vi.fn();
    const { container } = render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={projection}
        onInstance={onInstance}
      />,
    );
    await act(async () => {});
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText("Recording unavailable")).toBeInTheDocument();
    expect(container.querySelector("audio").getAttribute("src")).toBeNull();
    expect(onInstance).toHaveBeenLastCalledWith({
      multitrack: null,
      wavesurfers: [],
    });
  });
});

describe("paginated attachment list", () => {
  it("fetches only the selected page and no media", async () => {
    listAttachments
      .mockResolvedValueOnce({ items: [occurrence], next_cursor: "next" })
      .mockResolvedValueOnce({ items: [], next_cursor: null });
    render(<AttachmentsList projectId={projectId} parent={parent} />);
    await screen.findByText("Audio recording");
    expect(listAttachments).toHaveBeenCalledTimes(1);
    expect(resolveAttachments).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Next attachments" }));
    await screen.findByText("No attachments available");
    expect(listAttachments.mock.calls[1][2]).toBe("next");
    expect(screen.queryByText("Audio recording")).not.toBeInTheDocument();
  });
  it("collapses foreign/not-found failures without showing metadata", async () => {
    listAttachments.mockRejectedValue(new Error("canary-secret.wav"));
    render(<AttachmentsList projectId={projectId} parent={parent} />);
    await waitFor(() =>
      expect(screen.getByText("Recording unavailable")).toBeInTheDocument(),
    );
    expect(screen.queryByText("canary-secret.wav")).not.toBeInTheDocument();
  });
});
