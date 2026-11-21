import { afterEach, describe, expect, it, vi } from "vitest";
import SequentialPlayback from "../SequentialPlayback";
import { manifestSchema, recordingLanes, recordingPlan } from "../contract";
import { gatewayPath, occurrenceId, parent, standaloneManifest } from "./fixtures";
import {
  assistantSegments,
  issued,
  stereoProjection,
  stereoSegments,
  tracksProjection,
  userSegments,
} from "./playerLane.fixtures";

function media() {
  const element = new EventTarget();
  Object.assign(element, {
    src: "",
    preload: "none",
    currentTime: 0,
    readyState: 4,
    seeking: false,
    play: vi.fn().mockResolvedValue(),
    pause: vi.fn(),
    // Every source the element was told to load, in order.
    sources: [],
    load: vi.fn(() => element.sources.push(element.src)),
    removeAttribute: vi.fn((name) => {
      if (name === "src") element.src = "";
    }),
  });
  return element;
}
const fire = (element, name) => element.dispatchEvent(new Event(name));

// One grant per requested item, in request order; `deny(part, n)` may
// replace a result. Each call is one batched resolve.
function grants(deny = () => undefined, lifetimeMs = 60000) {
  let n = 0;
  return vi.fn(async (parts) =>
    parts.map((part) => {
      n += 1;
      return deny(part, n) ?? issued(part, n, lifetimeMs);
    }),
  );
}
const short = (id) => id.slice(0, 8) + id.slice(-1);
const label = (part) =>
  part.request.representation === "channel"
    ? `${short(part.occurrence_id)}/ch${part.request.channel}`
    : short(part.occurrence_id);
const batches = (authorize) =>
  authorize.mock.calls.map(([parts]) => parts.map(label));

function build(projection, authorize = grants()) {
  const plan = recordingPlan(
    projection.occurrence_id,
    projection.parent,
    projection.manifest,
  );
  const lanes = recordingLanes(plan, projection.manifest.duration_ms);
  const elements = lanes.map(media);
  const player = new SequentialPlayback(
    elements,
    lanes,
    projection.manifest.duration_ms / 1000,
    authorize,
  );
  const seen = { ready: vi.fn(), ended: vi.fn(), unavailable: vi.fn() };
  Object.entries(seen).forEach(([event, fn]) => player.on(event, fn));
  return { elements, player, authorize, seen, plan, lanes };
}
// Each element whose source changed reports metadata, then everything plays.
async function playing(player, ...elements) {
  for (const element of elements) {
    await vi.waitFor(() =>
      expect(element.load.mock.calls.length).toBeGreaterThan(
        element.loads ?? 0,
      ),
    );
    element.loads = element.load.mock.calls.length;
    fire(element, "loadedmetadata");
  }
  await vi.waitFor(() => expect(player.isPlaying()).toBe(true));
}
const at = (element, seconds) => {
  element.currentTime = seconds;
  fire(element, "timeupdate");
};

afterEach(() => {
  vi.useRealTimers();
});

describe("unit: recording plan lanes from the contract manifest (TH-2379 player)", () => {
  it("accepts the fixtures as contract-valid manifests", () => {
    for (const { manifest } of [stereoProjection, tracksProjection])
      expect(manifestSchema.safeParse(manifest).success).toBe(true);
  });

  it("plans stereo as one lane per channel derivative of EVERY segment", () => {
    const { occurrence_id, parent: owner, manifest } = stereoProjection;
    const plan = recordingPlan(occurrence_id, owner, manifest);
    expect(plan.mode).toBe("channels");
    expect(plan.lanes.map((lane) => lane.track.role)).toEqual([
      "user",
      "assistant",
    ]);
    plan.lanes.forEach((lane, channel) =>
      expect(lane.parts.map((part) => part.request)).toEqual(
        stereoSegments.map((id) => ({
          occurrence_id: id,
          parent: owner,
          representation: "channel",
          channel,
        })),
      ),
    );
    expect(plan.requests).toHaveLength(6);
  });

  it("plans separate tracks as simultaneous lanes of their own playable segments and gaps", () => {
    const { occurrence_id, parent: owner, manifest } = tracksProjection;
    const plan = recordingPlan(occurrence_id, owner, manifest);
    expect(plan.mode).toBe("tracks");
    const [user, assistant] = plan.lanes;
    expect(
      user.parts.map((part) => [part.kind, part.start_ms, part.duration_ms]),
    ).toEqual([
      ["segment", 0, 30000],
      ["gap", 30000, 2000],
      ["segment", 32000, 30000],
      ["segment", 62000, 4467],
    ]);
    expect(user.parts[1].reason).toBe("source_unavailable");
    expect(assistant.parts.map((part) => part.start_ms)).toEqual([
      0, 30000, 60000,
    ]);
    // A mono track never asks for a channel split that does not exist.
    expect(
      plan.requests.every(
        (item) => item.representation === "playable" && !("channel" in item),
      ),
    ).toBe(true);
    expect(plan.requests.map((item) => item.occurrence_id)).toEqual(
      tracksProjection.manifest.segments.map((s) => s.occurrence_id),
    );
  });

  const { segments } = tracksProjection.manifest;
  it.each([
    [
      "an occurrence repeated across tracks",
      {
        segments: segments.map((segment, index) =>
          index === 1
            ? { ...segment, occurrence_id: segments[0].occurrence_id }
            : segment,
        ),
      },
    ],
    [
      "indexes against start order",
      {
        segments: segments.map((segment, index) =>
          index === 2
            ? { ...segment, index: 3 }
            : index === 3
              ? { ...segment, index: 2 }
              : segment,
        ),
      },
    ],
    [
      "segments listed against start order",
      {
        segments: [segments[0], segments[1], segments[3], segments[2]]
          .concat(segments.slice(4))
          .map((segment, index) => ({ ...segment, index })),
      },
    ],
  ])("refuses separate tracks with %s", (_, patch) => {
    const manifest = { ...tracksProjection.manifest, ...patch };
    // Schema-valid coverage; only the playback order/identity is unsafe.
    expect(manifestSchema.safeParse(manifest).success).toBe(true);
    expect(() =>
      recordingPlan(tracksProjection.occurrence_id, laneParentOf(), manifest),
    ).toThrow();
  });
  it("refuses a projection anchored on another track's segment", () => {
    expect(() =>
      recordingPlan(
        assistantSegments[0],
        laneParentOf(),
        tracksProjection.manifest,
      ),
    ).toThrow();
  });
});
function laneParentOf() {
  return tracksProjection.parent;
}

describe("unit: stereo recording plays every segment's channels together (TH-2379 player)", () => {
  it("authorizes each segment's two channels in one request at its boundary, three segments to the end", async () => {
    const { elements, player, authorize, seen } = build(stereoProjection);
    const [left, right] = elements;
    player.prepare();
    await vi.waitFor(() => expect(seen.ready).toHaveBeenCalledTimes(1));
    const [s0, s1, s2] = stereoSegments.map(short);
    expect(batches(authorize)).toEqual([[`${s0}/ch0`, `${s0}/ch1`]]);
    // Availability is known, but no byte is requested before Play.
    expect([left.src, right.src]).toEqual(["", ""]);
    void player.play();
    expect(left.src).toBe(gatewayPath(stereoSegments[0], "grant-1"));
    expect(right.src).toBe(gatewayPath(stereoSegments[0], "grant-2"));
    // The shared barrier: one ready channel never starts alone.
    left.loads = left.load.mock.calls.length;
    fire(left, "loadedmetadata");
    expect(player.isPlaying()).toBe(false);
    expect(left.play).not.toHaveBeenCalled();
    await playing(player, right);
    right.currentTime = 29.9;
    at(left, 29.9);
    expect(player.getCurrentTime()).toBeCloseTo(29.9, 5);
    for (const [index, next] of [
      [1, stereoSegments[1]],
      [2, stereoSegments[2]],
    ]) {
      // One channel running out first leaves the other leading the clock.
      fire(left, "ended");
      expect(authorize).toHaveBeenCalledTimes(index);
      fire(right, "ended");
      expect(player.getCurrentTime()).toBe(index * 30);
      await vi.waitFor(() => expect(left.src).toContain(next));
      expect(right.src).toContain(next);
      await playing(player, left, right);
      expect([left.currentTime, right.currentTime]).toEqual([0, 0]);
      if (index === 1) {
        right.currentTime = 29.95;
        at(left, 29.95);
        expect(player.getCurrentTime()).toBeCloseTo(59.95, 5);
      }
    }
    at(right, 6.4);
    at(left, 6.4);
    expect(player.getCurrentTime()).toBeCloseTo(66.4, 5);
    fire(left, "ended");
    fire(right, "ended");
    expect(seen.ended).toHaveBeenCalledTimes(1);
    expect(player.getCurrentTime()).toBe(66.467);
    expect(player.isPlaying()).toBe(false);
    expect(batches(authorize)).toEqual([
      [`${s0}/ch0`, `${s0}/ch1`],
      [`${s1}/ch0`, `${s1}/ch1`],
      [`${s2}/ch0`, `${s2}/ch1`],
    ]);
    expect(seen.unavailable).not.toHaveBeenCalled();
    player.destroy();
  });

  it("seeks back across a boundary by re-authorizing that segment's channels only", async () => {
    const { elements, player, authorize } = build(stereoProjection);
    const [left, right] = elements;
    player.prepare();
    void player.play();
    await playing(player, left, right);
    player.setTime(62);
    await vi.waitFor(() => expect(left.src).toContain(stereoSegments[2]));
    await playing(player, left, right);
    expect([left.currentTime, right.currentTime]).toEqual([2, 2]);
    player.setTime(15);
    await vi.waitFor(() => expect(left.src).toContain(stereoSegments[0]));
    await playing(player, left, right);
    expect([left.currentTime, right.currentTime]).toEqual([15, 15]);
    expect(player.getCurrentTime()).toBe(15);
    const [s0, , s2] = stereoSegments.map(short);
    expect(batches(authorize)).toEqual([
      [`${s0}/ch0`, `${s0}/ch1`],
      [`${s2}/ch0`, `${s2}/ch1`],
      [`${s0}/ch0`, `${s0}/ch1`],
    ]);
    player.destroy();
  });

  it("keeps the lagging channel's segment until that channel also reaches the boundary", async () => {
    const { elements, player, authorize } = build(stereoProjection);
    const [left, right] = elements;
    player.prepare();
    void player.play();
    await playing(player, left, right);
    // The leading channel reaches 30 s while the other is 100 ms behind,
    // inside the drift allowance.
    right.currentTime = 29.9;
    at(left, 30);
    expect(player.getCurrentTime()).toBeCloseTo(29.9, 5);
    expect(player.isPlaying()).toBe(true);
    expect(authorize).toHaveBeenCalledTimes(1);
    expect(left.src).toContain(stereoSegments[0]);
    expect(right.src).toContain(stereoSegments[0]);
    // The lagging channel's own arrival ends the window.
    at(right, 30);
    expect(player.getCurrentTime()).toBe(30);
    await vi.waitFor(() => expect(left.src).toContain(stereoSegments[1]));
    expect(right.src).toContain(stereoSegments[1]);
    expect(authorize).toHaveBeenCalledTimes(2);
    player.destroy();
  });
});

describe("unit: separate tracks share one timeline with honest gaps (TH-2379 player)", () => {
  const [u0, u1, u2] = userSegments.map(short);
  const [a0, a1, a2] = assistantSegments.map(short);

  it("plays both tracks simultaneously, keeps the user's gap silent and moves each track at its own boundary", async () => {
    const { elements, player, authorize, seen } = build(tracksProjection);
    const [user, assistant] = elements;
    player.prepare();
    await vi.waitFor(() => expect(seen.ready).toHaveBeenCalled());
    expect(batches(authorize)).toEqual([[u0, a0]]);
    void player.play();
    await playing(player, user, assistant);
    assistant.currentTime = 29.95;
    at(user, 29.95);
    expect(player.getCurrentTime()).toBeCloseTo(29.95, 5);

    // 30 s: the user track enters its recorded gap, the assistant moves on.
    fire(user, "ended");
    fire(assistant, "ended");
    expect(player.getCurrentTime()).toBe(30);
    await vi.waitFor(() =>
      expect(assistant.src).toContain(assistantSegments[1]),
    );
    expect(user.removeAttribute).toHaveBeenCalledWith("src");
    expect(user.src).toBe("");
    await playing(player, assistant);
    at(assistant, 1.5);
    expect(player.getCurrentTime()).toBeCloseTo(31.5, 5);

    // 32 s: the user's next segment starts while the assistant keeps its
    // loaded source, realigned to the boundary rather than skipping audio.
    const assistantLoads = assistant.load.mock.calls.length;
    at(assistant, 2.1);
    expect(player.getCurrentTime()).toBe(32);
    await vi.waitFor(() => expect(user.src).toContain(userSegments[1]));
    expect(assistant.load.mock.calls.length).toBe(assistantLoads);
    await playing(player, user);
    expect(user.currentTime).toBe(0);
    expect(assistant.currentTime).toBe(2);

    // 60 s: the assistant ends first; the user track keeps leading.
    user.currentTime = 27.9;
    at(assistant, 29.9);
    fire(assistant, "ended");
    expect(player.isPlaying()).toBe(true);
    at(user, 28);
    expect(player.getCurrentTime()).toBe(60);
    await vi.waitFor(() =>
      expect(assistant.src).toContain(assistantSegments[2]),
    );
    await playing(player, assistant);
    expect(user.currentTime).toBe(28);

    // 62 s: the user's last segment; then both reach the end together.
    assistant.currentTime = 2;
    at(user, 30);
    expect(player.getCurrentTime()).toBe(62);
    await vi.waitFor(() => expect(user.src).toContain(userSegments[2]));
    await playing(player, user);
    fire(user, "ended");
    fire(assistant, "ended");
    expect(seen.ended).toHaveBeenCalledTimes(1);
    expect(player.getCurrentTime()).toBe(66.467);
    expect(batches(authorize)).toEqual([[u0, a0], [a1], [u1], [a2], [u2]]);
    expect(seen.unavailable).not.toHaveBeenCalled();
    player.destroy();
  });

  it("seeks into one track's gap and across boundaries, authorizing only what the position needs", async () => {
    const { elements, player, authorize } = build(tracksProjection);
    const [user, assistant] = elements;
    player.prepare();
    void player.play();
    await playing(player, user, assistant);
    player.setTime(31);
    await vi.waitFor(() =>
      expect(assistant.src).toContain(assistantSegments[1]),
    );
    expect(user.src).toBe("");
    await playing(player, assistant);
    expect(assistant.currentTime).toBe(1);
    expect(player.getCurrentTime()).toBe(31);
    player.setTime(45);
    await vi.waitFor(() => expect(user.src).toContain(userSegments[1]));
    await playing(player, user);
    expect([user.currentTime, assistant.currentTime]).toEqual([13, 15]);
    player.setTime(10);
    await vi.waitFor(() => expect(user.src).toContain(userSegments[0]));
    await playing(player, user, assistant);
    expect([user.currentTime, assistant.currentTime]).toEqual([10, 10]);
    expect(batches(authorize)).toEqual([[u0, a0], [a1], [u1], [u0, a0]]);
    player.destroy();
  });

  it("holds the track that reaches a boundary first until the lagging track has played its last 100 ms", async () => {
    const { elements, player, authorize, seen } = build(tracksProjection);
    const [user, assistant] = elements;
    player.prepare();
    void player.play();
    await playing(player, user, assistant);
    // The 32-60 s window: the user's second segment (32-62 s) plays with
    // the assistant's second (30-60 s).
    player.setTime(45);
    await vi.waitFor(() => expect(user.src).toContain(userSegments[1]));
    expect(assistant.src).toContain(assistantSegments[1]);
    await playing(player, user, assistant);
    expect([user.currentTime, assistant.currentTime]).toEqual([13, 15]);
    const userLoads = user.load.mock.calls.length;
    const userPauses = user.pause.mock.calls.length;
    const assistantPauses = assistant.pause.mock.calls.length;
    // The leading user track (it continues past 60 s) reaches 60.0 while
    // the assistant, whose segment ends at 60 s, is at 59.9: 100 ms behind,
    // inside the 120 ms drift allowance. The assistant is NOT ended here.
    assistant.currentTime = 29.9;
    at(user, 28);
    expect(player.getCurrentTime()).toBeCloseTo(59.9, 5);
    expect(player.isPlaying()).toBe(true);
    expect(user.pause.mock.calls.length).toBe(userPauses + 1);
    expect(assistant.pause.mock.calls.length).toBe(assistantPauses);
    expect(user.currentTime).toBe(28);
    // Nothing is authorized or replaced while the assistant plays its tail.
    expect(authorize).toHaveBeenCalledTimes(2);
    expect(assistant.src).toContain(assistantSegments[1]);

    // Pause and resume keep the earliest position; only the lagging track
    // plays again, the waiting one stays where it is.
    const userPlays = user.play.mock.calls.length;
    const assistantPlays = assistant.play.mock.calls.length;
    player.pause();
    expect(player.getCurrentTime()).toBeCloseTo(59.9, 5);
    void player.play();
    await vi.waitFor(() => expect(player.isPlaying()).toBe(true));
    expect(user.play.mock.calls.length).toBe(userPlays);
    expect(assistant.play.mock.calls.length).toBe(assistantPlays + 1);
    expect(assistant.currentTime).toBe(29.9);
    expect(authorize).toHaveBeenCalledTimes(2);

    // The lagging track leads the clock through its own tail...
    at(assistant, 29.95);
    expect(player.getCurrentTime()).toBeCloseTo(59.95, 5);
    expect(authorize).toHaveBeenCalledTimes(2);
    // ...and only its own arrival at 60 s ends the window.
    at(assistant, 30);
    expect(player.getCurrentTime()).toBe(60);
    await vi.waitFor(() =>
      expect(assistant.src).toContain(assistantSegments[2]),
    );
    // The continuing user track kept its source and its place.
    expect(user.src).toContain(userSegments[1]);
    expect(user.load.mock.calls.length).toBe(userLoads);
    await playing(player, assistant);
    expect([user.currentTime, assistant.currentTime]).toEqual([28, 0]);
    expect(batches(authorize)).toEqual([[u0, a0], [u1, a1], [a2]]);
    expect(seen.unavailable).not.toHaveBeenCalled();
    player.destroy();
  });

  it("plays silent time on a wall clock while every track is in a gap", async () => {
    vi.useFakeTimers();
    // The assistant's second segment failed to upload: a declared gap, so
    // between 30 s and 32 s neither track has audio.
    const manifest = {
      ...tracksProjection.manifest,
      segments: tracksProjection.manifest.segments
        .filter((segment) => segment.occurrence_id !== assistantSegments[1])
        .map((segment, index) => ({ ...segment, index })),
      gaps: [
        ...tracksProjection.manifest.gaps,
        {
          start_ms: 30000,
          duration_ms: 30000,
          reason: "incomplete",
          track_id: tracksProjection.manifest.tracks[1].track_id,
        },
      ],
    };
    expect(manifestSchema.safeParse(manifest).success).toBe(true);
    const { elements, player, authorize } = build({
      ...tracksProjection,
      manifest,
    });
    const [user, assistant] = elements;
    player.prepare();
    void player.play();
    await playing(player, user, assistant);
    fire(user, "ended");
    fire(assistant, "ended");
    expect(player.getCurrentTime()).toBe(30);
    expect(player.isPlaying()).toBe(true);
    expect([user.src, assistant.src]).toEqual(["", ""]);
    await vi.advanceTimersByTimeAsync(1000);
    expect(player.getCurrentTime()).toBeCloseTo(31, 5);
    expect(authorize).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(user.src).toContain(userSegments[1]));
    expect(player.getCurrentTime()).toBe(32);
    expect(assistant.src).toBe("");
    player.destroy();
  });
});

describe("unit: denial, expiry and recovery stay bounded across lanes (TH-2379 player)", () => {
  it.each([
    [
      "stereo, one channel of the second segment denied",
      stereoProjection,
      (part) =>
        part.occurrence_id === stereoSegments[1] && part.request.channel === 1
          ? { occurrence_id: part.occurrence_id, status: "not_found" }
          : undefined,
      "not_found",
      30,
    ],
    [
      "separate tracks, the user's second segment deleted",
      tracksProjection,
      (part) =>
        part.occurrence_id === userSegments[1]
          ? { occurrence_id: part.occurrence_id, status: "deleted" }
          : undefined,
      "deleted",
      32,
    ],
  ])(
    "stops every lane and drops every source when a later segment is refused (%s)",
    async (_, projection, deny, status, stop) => {
      const { elements, player, seen } = build(projection, grants(deny));
      const [first, second] = elements;
      player.prepare();
      void player.play();
      await playing(player, first, second);
      fire(first, "ended");
      fire(second, "ended");
      if (projection === tracksProjection) {
        // The assistant plays on through the user's gap until 32 s.
        await playing(player, second);
        at(second, 2);
      }
      await vi.waitFor(() =>
        expect(seen.unavailable).toHaveBeenCalledWith({
          status,
          retryable: false,
        }),
      );
      expect([first.src, second.src]).toEqual(["", ""]);
      expect(player.isPlaying()).toBe(false);
      expect(player.getCurrentTime()).toBe(stop);
      expect(seen.ended).not.toHaveBeenCalled();
      // The allowed half of a denied pair is never loaded.
      expect(
        [first, second].some((element) =>
          element.src.includes(stereoSegments[1]),
        ),
      ).toBe(false);
    },
  );

  it.each([
    [
      "two channels sharing one grant path",
      stereoProjection,
      (results) =>
        results.map((result) => ({
          ...result,
          gateway_path: results[0].gateway_path,
        })),
    ],
    ["a short batch", stereoProjection, (results) => results.slice(0, 1)],
    [
      "a batch answered out of request order",
      tracksProjection,
      (results) => [...results].reverse(),
    ],
  ])("fails closed on %s", async (_, projection, mangle) => {
    let n = 0;
    const authorize = vi.fn(async (parts) =>
      mangle(parts.map((part) => issued(part, ++n))),
    );
    const { elements, player, seen } = build(projection, authorize);
    player.prepare();
    await vi.waitFor(() =>
      expect(seen.unavailable).toHaveBeenCalledWith({
        status: "not_found",
        retryable: false,
      }),
    );
    expect(seen.ready).not.toHaveBeenCalled();
    expect(elements.map((element) => element.src)).toEqual(["", ""]);
  });

  it("renews every live lane in one request before the grants lapse, keeping the position, and never while paused", async () => {
    vi.useFakeTimers();
    const { elements, player, authorize } = build(stereoProjection);
    const [left, right] = elements;
    player.prepare();
    void player.play();
    await playing(player, left, right);
    right.currentTime = 12;
    at(left, 12);
    await vi.advanceTimersByTimeAsync(49000);
    expect(authorize).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(authorize).toHaveBeenCalledTimes(2);
    expect(authorize.mock.calls[1][0].map(label)).toEqual(
      authorize.mock.calls[0][0].map(label),
    );
    expect(left.src).toContain("grant-3");
    expect(right.src).toContain("grant-4");
    left.currentTime = 0;
    right.currentTime = 0;
    await playing(player, left, right);
    expect([left.currentTime, right.currentTime]).toEqual([12, 12]);
    expect(player.getCurrentTime()).toBe(12);
    player.pause();
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    expect(authorize).toHaveBeenCalledTimes(2);
    void player.play();
    await vi.waitFor(() => expect(authorize).toHaveBeenCalledTimes(3));
    await vi.waitFor(() => expect(left.src).toContain("grant-5"));
    await playing(player, left, right);
    expect(player.getCurrentTime()).toBe(12);
    player.destroy();
  });

  it("plays a standalone file longer than one grant without resolving twice before Play", async () => {
    const authorize = grants();
    const element = media();
    const plan = recordingPlan(occurrenceId, parent, standaloneManifest);
    const player = new SequentialPlayback(
      [element],
      recordingLanes(plan, standaloneManifest.duration_ms),
      standaloneManifest.duration_ms / 1000,
      authorize,
    );
    const ready = vi.fn();
    player.on("ready", ready);
    player.prepare();
    await vi.waitFor(() => expect(ready).toHaveBeenCalled());
    void player.play();
    await playing(player, element);
    at(element, 45);
    expect(player.getCurrentTime()).toBe(45);
    expect(authorize).toHaveBeenCalledTimes(1);
    player.destroy();
  });

  it("recovers an evicted cookie once for every lane on a new session, keeping the position, then stops", async () => {
    const { elements, player, authorize, seen } = build(tracksProjection);
    const [user, assistant] = elements;
    player.prepare();
    void player.play();
    await playing(player, user, assistant);
    assistant.currentTime = 7;
    at(user, 7);
    // e.g. the session's path-scoped cookie was evicted: the gateway refuses
    // every lane on that session, so both elements error back to back.
    fire(assistant, "error");
    fire(user, "error");
    await vi.waitFor(() => expect(authorize).toHaveBeenCalledTimes(2));
    expect(seen.unavailable).not.toHaveBeenCalled();
    expect(authorize.mock.calls.map(([, , options]) => options)).toEqual([
      { fresh: false },
      { fresh: true },
    ]);
    expect(authorize.mock.calls[1][0].map(label)).toEqual(
      [userSegments[0], assistantSegments[0]].map(short),
    );
    await vi.waitFor(() => expect(user.src).toContain("grant-3"));
    expect(assistant.src).toContain("grant-4");
    expect(player.isPlaying()).toBe(false);
    void player.play();
    user.currentTime = 0;
    assistant.currentTime = 0;
    await playing(player, user, assistant);
    expect([user.currentTime, assistant.currentTime]).toEqual([7, 7]);
    expect(player.getCurrentTime()).toBe(7);
    fire(user, "error");
    expect(seen.unavailable).toHaveBeenCalledWith({
      status: "not_found",
      retryable: true,
    });
    expect([user.src, assistant.src]).toEqual(["", ""]);
    expect(authorize).toHaveBeenCalledTimes(2);
  });

  it.each([
    [
      "answers with grants on the evicted session",
      (parts) => parts.map((part, offset) => issued(part, 101 + offset)),
    ],
    [
      "fails",
      () => {
        throw new Error("canary-secret");
      },
    ],
  ])(
    "drops a renewal still out when the cookie is evicted, even if it %s, then recovers on ONE new session",
    async (_, late) => {
      vi.useFakeTimers();
      let calls = 0;
      let n = 0;
      let release;
      const authorize = vi.fn(async (parts) => {
        calls += 1;
        if (calls === 2) {
          // The renewal is held until after the eviction.
          await new Promise((resolve) => {
            release = resolve;
          });
          return late(parts);
        }
        return parts.map((part) => issued(part, ++n));
      });
      const { elements, player, seen } = build(stereoProjection, authorize);
      const [left, right] = elements;
      player.prepare();
      void player.play();
      await playing(player, left, right);
      right.currentTime = 12;
      at(left, 12);
      // The lazy renewal of both channels leaves 10 s before they lapse.
      await vi.advanceTimersByTimeAsync(51000);
      expect(authorize).toHaveBeenCalledTimes(2);
      expect(release).toBeDefined();
      // The session's cookie is evicted: both lanes error while it is out.
      fire(right, "error");
      fire(left, "error");
      expect(player.isPlaying()).toBe(false);
      expect(authorize).toHaveBeenCalledTimes(2);
      release();
      await vi.waitFor(() => expect(authorize).toHaveBeenCalledTimes(3));
      expect(authorize.mock.calls.map(([, , options]) => options)).toEqual([
        { fresh: false },
        { fresh: false },
        { fresh: true },
      ]);
      await vi.waitFor(() => expect(left.src).toContain("grant-3"));
      // The late answer never reached an element, and never stopped the
      // player: only the opening and the new-session grants were loaded.
      expect(left.sources).toEqual([
        gatewayPath(stereoSegments[0], "grant-1"),
        gatewayPath(stereoSegments[0], "grant-3"),
      ]);
      expect(right.sources).toEqual([
        gatewayPath(stereoSegments[0], "grant-2"),
        gatewayPath(stereoSegments[0], "grant-4"),
      ]);
      expect(seen.unavailable).not.toHaveBeenCalled();
      expect(player.isPlaying()).toBe(false);
      expect(player.getCurrentTime()).toBe(12);
      void player.play();
      left.currentTime = 0;
      right.currentTime = 0;
      await playing(player, left, right);
      expect([left.currentTime, right.currentTime]).toEqual([12, 12]);
      // That was the one recovery: the next refusal stops for Retry.
      fire(left, "error");
      expect(seen.unavailable).toHaveBeenCalledWith({
        status: "not_found",
        retryable: true,
      });
      expect([left.src, right.src]).toEqual(["", ""]);
      expect(authorize).toHaveBeenCalledTimes(3);
    },
  );
});
