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
import { resolveAttachments } from "../client";
import {
  occurrenceId,
  parent,
  projectId,
  projection,
  standaloneManifest,
} from "./fixtures";
import {
  pipecatProjection,
  segmentGrant,
  threeSegmentProjection,
} from "./pipecatRecording";

vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));

const [first, second] = pipecatProjection.manifest.segments.map(
  (segment) => segment.occurrence_id,
);
const request = (occurrence_id, owner = pipecatProjection.parent) => [
  { occurrence_id, parent: owner, representation: "playable" },
];
const requested = () => resolveAttachments.mock.calls.map((call) => call[1]);

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  resolveAttachments.mockImplementation(async (_, items) =>
    items.map((item) => segmentGrant(item.occurrence_id)),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});

// jsdom has no media pipeline: give the element a position and a full buffer.
function controllable(audio) {
  let position = 0;
  Object.defineProperty(audio, "readyState", {
    configurable: true,
    get: () => 4,
  });
  Object.defineProperty(audio, "currentTime", {
    configurable: true,
    get: () => position,
    set: (value) => {
      position = value;
    },
  });
  return audio;
}
const fire = (element, name) =>
  act(async () => {
    element.dispatchEvent(new Event(name));
  });
const position = () =>
  screen
    .getByRole("slider", { name: "Recording position" })
    .getAttribute("aria-valuenow");

async function renderRecording(attachment, onInstance = vi.fn()) {
  const view = render(
    <RecordingAttachmentPlayer
      projectId={projectId}
      attachment={attachment}
      onInstance={onInstance}
    />,
  );
  await screen.findByRole("button", { name: "Play recording" });
  const audio = controllable(view.container.querySelector("audio"));
  return { ...view, audio, onInstance };
}
async function crossBoundary(audio, nextId) {
  await fire(audio, "ended");
  await waitFor(() =>
    expect(audio.getAttribute("src")).toBe(segmentGrant(nextId).gateway_path),
  );
  await fire(audio, "loadedmetadata");
}

describe("unit: Voice recording plays every segment in order (TH-2379)", () => {
  it("plays the actual 36.467 s Pipecat call through its 30 s boundary to the end", async () => {
    const { container, audio, onInstance } =
      await renderRecording(pipecatProjection);
    expect(requested()).toEqual([request(first)]);
    expect(audio.getAttribute("preload")).toBe("none");
    expect(audio.getAttribute("src")).toBeNull();
    expect(onInstance).toHaveBeenCalledTimes(1);
    expect(onInstance.mock.calls[0][0].multitrack.getDuration()).toBe(36.467);
    expect(screen.getByText("0:00 / 0:36")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    expect(audio.getAttribute("src")).toBe(segmentGrant(first).gateway_path);
    await fire(audio, "loadedmetadata");
    audio.currentTime = 29.9;
    await fire(audio, "timeupdate");
    expect(position()).toBe("29.9");

    await crossBoundary(audio, second);
    expect(requested()).toEqual([request(first), request(second)]);
    expect(position()).toBe("30");
    expect(audio.currentTime).toBe(0);
    expect(
      screen.getByRole("button", { name: "Pause recording" }),
    ).toBeInTheDocument();
    audio.currentTime = 6.4;
    await fire(audio, "timeupdate");
    expect(position()).toBe("36.4");

    await fire(audio, "ended");
    await screen.findByRole("button", { name: "Play recording" });
    expect(position()).toBe("36.467");
    expect(screen.getByText("0:36 / 0:36")).toBeInTheDocument();
    expect(container.querySelectorAll("audio")).toHaveLength(1);
    expect(resolveAttachments).toHaveBeenCalledTimes(2);
  });

  it("crosses both boundaries of a three-segment call with one source at a time", async () => {
    const ids = threeSegmentProjection.manifest.segments.map(
      (segment) => segment.occurrence_id,
    );
    const { container, audio } = await renderRecording(threeSegmentProjection);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await fire(audio, "loadedmetadata");
    for (const id of ids.slice(1)) {
      await crossBoundary(audio, id);
      expect(container.querySelectorAll("audio")).toHaveLength(1);
    }
    expect(position()).toBe("60");
    await fire(audio, "ended");
    await screen.findByRole("button", { name: "Play recording" });
    expect(position()).toBe("66.467");
    expect(requested()).toEqual(ids.map((id) => request(id)));
  });

  it("clears the source and stops at the boundary when the next segment is denied", async () => {
    resolveAttachments.mockImplementation(async (_, [item]) => [
      item.occurrence_id === second
        ? { occurrence_id: second, status: "not_found" }
        : segmentGrant(item.occurrence_id),
    ]);
    const { audio, onInstance } = await renderRecording(pipecatProjection);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await fire(audio, "loadedmetadata");
    await fire(audio, "ended");
    await screen.findByText("Recording unavailable");
    expect(audio.getAttribute("src")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Retry recording" }),
    ).not.toBeInTheDocument();
    expect(onInstance).toHaveBeenLastCalledWith({
      multitrack: null,
      wavesurfers: [],
    });
    expect(requested()).toEqual([request(first), request(second)]);
  });

  it("restarts from the beginning when an unreachable next segment is retried", async () => {
    let reachable = false;
    resolveAttachments.mockImplementation(async (_, [item]) => {
      if (item.occurrence_id === second && !reachable)
        throw new Error("canary-secret");
      return [segmentGrant(item.occurrence_id)];
    });
    const { audio } = await renderRecording(pipecatProjection);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await fire(audio, "loadedmetadata");
    audio.currentTime = 29.9;
    await fire(audio, "timeupdate");
    await fire(audio, "ended");
    reachable = true;
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry recording" }),
    );
    await screen.findByRole("button", { name: "Play recording" });
    expect(position()).toBe("0");
    expect(screen.queryByText(/canary-secret/)).not.toBeInTheDocument();
    expect(requested()).toEqual([
      request(first),
      request(second),
      request(first),
    ]);
  });

  it("aborts the boundary authorization on unmount and ignores its late grant", async () => {
    let finish;
    resolveAttachments.mockImplementation((_, [item]) =>
      item.occurrence_id === second
        ? new Promise((resolve) => {
            finish = () => resolve([segmentGrant(second)]);
          })
        : Promise.resolve([segmentGrant(item.occurrence_id)]),
    );
    const { audio, onInstance, unmount } =
      await renderRecording(pipecatProjection);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await fire(audio, "loadedmetadata");
    await fire(audio, "ended");
    await waitFor(() => expect(finish).toBeDefined());
    const signal = resolveAttachments.mock.calls[1][2];
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => finish());
    expect(audio.getAttribute("src")).toBeNull();
    expect(audio.isConnected).toBe(false);
    expect(onInstance).toHaveBeenLastCalledWith({
      multitrack: null,
      wavesurfers: [],
    });
  });

  it("plays a standalone file tiled on the timeline as one file with one grant", async () => {
    const { audio, onInstance } = await renderRecording({
      ...projection,
      manifest: standaloneManifest,
    });
    expect(requested()).toEqual([request(occurrenceId, parent)]);
    expect(onInstance.mock.calls[0][0].multitrack.getDuration()).toBe(66.467);
    expect(screen.getByText("0:00 / 1:06")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    expect(audio.getAttribute("src")).toBe(
      segmentGrant(occurrenceId).gateway_path,
    );
    await fire(audio, "loadedmetadata");
    audio.currentTime = 31;
    await fire(audio, "timeupdate");
    expect(position()).toBe("31");
    expect(resolveAttachments).toHaveBeenCalledTimes(1);
  });

  it("refuses a projection whose anchor is not its first segment", () => {
    render(
      <RecordingAttachmentPlayer
        projectId={projectId}
        attachment={{ ...pipecatProjection, occurrence_id: second }}
      />,
    );
    expect(screen.getByText("Recording unavailable")).toBeInTheDocument();
    expect(resolveAttachments).not.toHaveBeenCalled();
  });
});
