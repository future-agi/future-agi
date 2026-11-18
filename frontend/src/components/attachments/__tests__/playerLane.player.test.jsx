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
import { manifestSchema } from "../contract";
import { projectId } from "./fixtures";
import { pipecatProjection, segmentGrant } from "./pipecatRecording";
import {
  assistantSegments,
  issued,
  stereoProjection,
  stereoSegments,
  tracksProjection,
  userSegments,
} from "./playerLane.fixtures";

vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));

let minted;
beforeEach(() => {
  minted = 0;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  resolveAttachments.mockImplementation(async (_, items) =>
    items.map((item) => issued(item, ++minted)),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});

// jsdom has no media pipeline: give each element a position and a full buffer.
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
const requested = () =>
  resolveAttachments.mock.calls.map(([, items]) =>
    items.map((item) =>
      item.representation === "channel"
        ? `${item.occurrence_id}/${item.channel}`
        : item.occurrence_id,
    ),
  );

async function renderRecording(attachment, onInstance = vi.fn()) {
  const view = render(
    <RecordingAttachmentPlayer
      projectId={projectId}
      attachment={attachment}
      onInstance={onInstance}
    />,
  );
  await screen.findByRole("button", { name: "Play recording" });
  const audios = [...view.container.querySelectorAll("audio")].map(
    controllable,
  );
  return { ...view, audios, onInstance };
}
async function loaded(...audios) {
  for (const audio of audios) await fire(audio, "loadedmetadata");
}

describe("unit: Voice player for every recording layout (TH-2379 player)", () => {
  it("keeps the proven two-segment mixed call on one hidden element", async () => {
    const { container, audios } = await renderRecording(pipecatProjection);
    expect(audios).toHaveLength(1);
    // The layout heading and the mixed track's own label.
    expect(screen.getAllByText("Mixed recording")).toHaveLength(2);
    const [first, second] = pipecatProjection.manifest.segments.map(
      (segment) => segment.occurrence_id,
    );
    resolveAttachments.mockImplementation(async (_, items) =>
      items.map((item) => segmentGrant(item.occurrence_id)),
    );
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await loaded(audios[0]);
    await fire(audios[0], "ended");
    await waitFor(() =>
      expect(audios[0].getAttribute("src")).toBe(
        segmentGrant(second).gateway_path,
      ),
    );
    expect(requested()).toEqual([[first], [second]]);
    expect(container.querySelectorAll("audio")).toHaveLength(1);
  });

  it("plays both stereo channels of all three segments with one shared clock", async () => {
    const { container, audios } = await renderRecording(stereoProjection);
    const [left, right] = audios;
    expect(audios).toHaveLength(2);
    expect(screen.getByText("Stereo recording")).toBeInTheDocument();
    expect(screen.getByText("User · Assistant")).toBeInTheDocument();
    expect(
      screen.getByText("Recording does not confirm what participants heard"),
    ).toBeInTheDocument();
    expect(screen.getByText("0:00 / 1:06")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    expect(
      screen.getByRole("button", { name: "Pause recording" }),
    ).toBeInTheDocument();
    await loaded(left, right);
    for (const index of [1, 2]) {
      left.currentTime = 29.9;
      right.currentTime = 29.9;
      await fire(left, "timeupdate");
      expect(Number(position())).toBeCloseTo((index - 1) * 30 + 29.9, 5);
      await fire(left, "ended");
      await fire(right, "ended");
      await waitFor(() =>
        expect(left.getAttribute("src")).toContain(stereoSegments[index]),
      );
      expect(right.getAttribute("src")).toContain(stereoSegments[index]);
      expect(position()).toBe(String(index * 30));
      await loaded(left, right);
    }
    await fire(left, "ended");
    await fire(right, "ended");
    await screen.findByRole("button", { name: "Play recording" });
    expect(position()).toBe("66.467");
    expect(requested()).toEqual(
      stereoSegments.map((id) => [`${id}/0`, `${id}/1`]),
    );
    expect(container.querySelectorAll("audio")).toHaveLength(2);
  });

  it("drops both channels and every element when a later channel is refused", async () => {
    resolveAttachments.mockImplementation(async (_, items) =>
      items.map((item) =>
        item.occurrence_id === stereoSegments[1] && item.channel === 1
          ? { occurrence_id: item.occurrence_id, status: "not_found" }
          : issued(item, ++minted),
      ),
    );
    const { container, audios, onInstance } =
      await renderRecording(stereoProjection);
    const [left, right] = audios;
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await loaded(left, right);
    await fire(left, "ended");
    await fire(right, "ended");
    await screen.findByText("Recording unavailable");
    expect(left.getAttribute("src")).toBeNull();
    expect(right.getAttribute("src")).toBeNull();
    expect(container.querySelector("audio")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Retry recording" }),
    ).not.toBeInTheDocument();
    expect(onInstance).toHaveBeenLastCalledWith({
      multitrack: null,
      wavesurfers: [],
    });
  });

  it("shows separate tracks on one timeline with per-track gap and playout truth", async () => {
    const { audios, onInstance } = await renderRecording(tracksProjection);
    const [user, assistant] = audios;
    expect(screen.getByText("Separate recording tracks")).toBeInTheDocument();
    expect(screen.getByText("User · Assistant")).toBeInTheDocument();
    expect(
      screen.getByText("Partial recording — some audio is missing."),
    ).toBeInTheDocument();
    // Played at 0 s, but the summary never claims the whole call was heard.
    expect(
      screen.getByText("Played to the participant at this position"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Recording does not confirm what participants heard"),
    ).toBeInTheDocument();
    const player = onInstance.mock.calls[0][0].multitrack;
    // Transcript seek into the user's gap, while paused: nothing is fetched.
    act(() => player.setTime(31));
    expect(position()).toBe("31");
    expect(
      screen.getByText(
        "Partial recording — some audio is missing (User track: source unavailable at this position).",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Generated at this position; playout not confirmed"),
    ).toBeInTheDocument();
    expect(requested()).toEqual([[userSegments[0], assistantSegments[0]]]);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await waitFor(() =>
      expect(assistant.getAttribute("src")).toContain(assistantSegments[1]),
    );
    // The user track is silent here: no source, no request for it.
    expect(user.getAttribute("src")).toBeNull();
    expect(requested()).toEqual([
      [userSegments[0], assistantSegments[0]],
      [assistantSegments[1]],
    ]);
    await loaded(assistant);
    assistant.currentTime = 2;
    await fire(assistant, "timeupdate");
    await waitFor(() =>
      expect(user.getAttribute("src")).toContain(userSegments[1]),
    );
    expect(position()).toBe("32");
    expect(
      screen.getByText("Partial recording — some audio is missing."),
    ).toBeInTheDocument();
  });

  it("keeps the transcript seek across a Retry-free bounded recovery", async () => {
    const { audios, onInstance } = await renderRecording(tracksProjection);
    const [user, assistant] = audios;
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    await loaded(user, assistant);
    user.currentTime = 9;
    assistant.currentTime = 9;
    await fire(user, "timeupdate");
    // An evicted session cookie refuses both tracks at once, before any
    // replacement grant can arrive.
    await act(async () => {
      assistant.dispatchEvent(new Event("error"));
      user.dispatchEvent(new Event("error"));
    });
    await screen.findByRole("button", { name: "Play recording" });
    await waitFor(() => expect(resolveAttachments).toHaveBeenCalledTimes(2));
    expect(resolveAttachments.mock.calls[1][3]).toEqual({ fresh: true });
    expect(screen.queryByText("Recording unavailable")).not.toBeInTheDocument();
    expect(position()).toBe("9");
    expect(onInstance.mock.calls[0][0].multitrack.getCurrentTime()).toBe(9);
    fireEvent.click(screen.getByRole("button", { name: "Play recording" }));
    user.currentTime = 0;
    assistant.currentTime = 0;
    await loaded(user, assistant);
    expect([user.currentTime, assistant.currentTime]).toEqual([9, 9]);
    await fire(user, "error");
    await screen.findByText("Recording unavailable");
    expect(
      screen.getByRole("button", { name: "Retry recording" }),
    ).toBeInTheDocument();
    expect(resolveAttachments).toHaveBeenCalledTimes(2);
  });
});

describe("unit: playout evidence at a position comes from every covering interval (TH-2379 player)", () => {
  const PLAYED = "Played to the participant at this position";
  const GENERATED = "Generated at this position; playout not confirmed";
  const CUT_OFF = "Playout was cut off at this position";
  const UNKNOWN = "Playout unknown at this position";
  const unconfirmed = (evidence) =>
    `Playout not confirmed at this position (evidence: ${evidence})`;
  const interval = (from, to, evidence) => ({
    start_ms: from * 1000,
    duration_ms: (to - from) * 1000,
    evidence,
  });
  const orders = (items) =>
    items.length < 2
      ? [items]
      : items.flatMap((item, at) =>
          orders(items.filter((_, other) => other !== at)).map((rest) => [
            item,
            ...rest,
          ]),
        );
  // The stereo fixture has no gaps, so the only per-position line is the
  // playout line.
  const playoutLine = () =>
    screen.queryByText(/at this position/)?.textContent ?? null;

  it.each([
    [
      "an explicit played interval inside a longer generated one",
      [interval(0, 10, "generated"), interval(2, 5, "played")],
      [
        [1, GENERATED],
        [3, PLAYED],
        [5, GENERATED],
        [10, null],
      ],
    ],
    [
      "played overlapping truncated",
      [interval(0, 10, "played"), interval(4, 6, "truncated")],
      [
        [2, PLAYED],
        [4, unconfirmed("played and cut off")],
        [6, PLAYED],
      ],
    ],
    [
      "played overlapping truncated and unknown",
      [
        interval(0, 10, "played"),
        interval(2, 6, "truncated"),
        interval(4, 8, "unknown"),
      ],
      [
        [1, PLAYED],
        [3, unconfirmed("played and cut off")],
        [5, unconfirmed("played, cut off and unknown")],
        [7, unconfirmed("played and unknown")],
        [8, PLAYED],
      ],
    ],
    [
      "truncated overlapping unknown",
      [interval(0, 6, "truncated"), interval(4, 10, "unknown")],
      [
        [2, CUT_OFF],
        [5, unconfirmed("cut off and unknown")],
        [6, UNKNOWN],
        [10, null],
      ],
    ],
    [
      "generated, played and truncated layered",
      [
        interval(0, 20, "generated"),
        interval(0, 10, "played"),
        interval(5, 15, "truncated"),
      ],
      [
        [2, PLAYED],
        [7, unconfirmed("played and cut off")],
        [12, CUT_OFF],
        [17, GENERATED],
        [20, null],
      ],
    ],
    [
      "disjoint intervals that meet at a boundary",
      [interval(0, 30, "played"), interval(30, 60, "generated")],
      [
        [0, PLAYED],
        [29, PLAYED],
        [30, GENERATED],
        [59, GENERATED],
        [60, null],
      ],
    ],
  ])(
    "shows the same evidence for %s in every interval order",
    async (_, playout, expected) => {
      const manifest = { ...stereoProjection.manifest, playout };
      // Overlapping playout intervals are valid manifests.
      expect(manifestSchema.safeParse(manifest).success).toBe(true);
      for (const order of orders(playout)) {
        const { onInstance, unmount } = await renderRecording({
          ...stereoProjection,
          manifest: { ...manifest, playout: order },
        });
        const player = onInstance.mock.calls[0][0].multitrack;
        const shown = expected.map(([seconds]) => {
          act(() => player.setTime(seconds));
          return playoutLine();
        });
        expect(shown).toEqual(expected.map(([, text]) => text));
        unmount();
      }
    },
  );
});
