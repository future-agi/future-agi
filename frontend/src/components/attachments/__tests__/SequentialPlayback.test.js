import { afterEach, describe, expect, it, vi } from "vitest";
import SequentialPlayback from "../SequentialPlayback";
import { recordingPlan } from "../contract";
import {
  pipecatProjection,
  segmentGrant,
  threeSegmentProjection,
} from "./pipecatRecording";

const [first, second] = pipecatProjection.manifest.segments.map(
  (segment) => segment.occurrence_id,
);
const three = threeSegmentProjection.manifest.segments.map(
  (segment) => segment.occurrence_id,
);

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
    load: vi.fn(),
    removeAttribute: vi.fn((name) => {
      if (name === "src") element.src = "";
    }),
  });
  return element;
}
const fire = (element, name) => element.dispatchEvent(new Event(name));

// Grants as playback.issue() returns them: one per call, a fresh token each.
function grants(override = () => undefined) {
  let issued = 0;
  return vi.fn(async (part) => {
    issued += 1;
    const denied = override(part, issued);
    if (denied) return denied;
    return {
      ...segmentGrant(part.occurrence_id),
      gateway_path: `/tracer/v1/attachment-media/${part.occurrence_id}/grant-${issued}`,
      expires_at: new Date(Date.now() + 60000).toISOString(),
    };
  });
}

function build(projection, authorize = grants()) {
  const { parts } = recordingPlan(
    projection.occurrence_id,
    projection.parent,
    projection.manifest,
  );
  const element = media();
  const player = new SequentialPlayback(
    element,
    parts,
    projection.manifest.duration_ms / 1000,
    authorize,
  );
  const seen = { ready: vi.fn(), ended: vi.fn(), unavailable: vi.fn() };
  Object.entries(seen).forEach(([event, fn]) => player.on(event, fn));
  return { element, player, authorize, seen };
}
const authorized = (authorize) =>
  authorize.mock.calls.map(([part]) => part.occurrence_id);

// The element's newest source load reports metadata, then playback starts.
async function playing(element, player) {
  await vi.waitFor(() =>
    expect(element.load.mock.calls.length).toBeGreaterThan(element.loads ?? 0),
  );
  element.loads = element.load.mock.calls.length;
  fire(element, "loadedmetadata");
  await vi.waitFor(() => expect(player.isPlaying()).toBe(true));
}

afterEach(() => {
  vi.useRealTimers();
});

describe("unit: sequential recording segments (TH-2379)", () => {
  it("plays the actual 36.467 s Pipecat call across its 30 s boundary to the end", async () => {
    const { element, player, authorize, seen } = build(pipecatProjection);
    player.prepare();
    await vi.waitFor(() => expect(seen.ready).toHaveBeenCalledTimes(1));
    expect(player.getDuration()).toBe(36.467);
    // Availability is known, but no byte is requested before Play.
    expect(element.src).toBe("");
    void player.play();
    expect(element.src).toBe(`/tracer/v1/attachment-media/${first}/grant-1`);
    await playing(element, player);
    element.currentTime = 29.98;
    fire(element, "timeupdate");
    expect(player.getCurrentTime()).toBeCloseTo(29.98, 5);
    // The next segment is authorized only when it is needed.
    expect(authorized(authorize)).toEqual([first]);

    fire(element, "ended");
    expect(player.getCurrentTime()).toBe(30);
    await vi.waitFor(() =>
      expect(element.src).toBe(`/tracer/v1/attachment-media/${second}/grant-2`),
    );
    expect(authorized(authorize)).toEqual([first, second]);
    await playing(element, player);
    expect(element.currentTime).toBe(0);
    element.currentTime = 6.4;
    fire(element, "timeupdate");
    expect(player.getCurrentTime()).toBeCloseTo(36.4, 5);

    fire(element, "ended");
    expect(seen.ended).toHaveBeenCalledTimes(1);
    expect(player.isPlaying()).toBe(false);
    expect(player.getCurrentTime()).toBe(36.467);
    expect(seen.unavailable).not.toHaveBeenCalled();
    player.destroy();
  });

  it("crosses both boundaries of a three-segment recording and seeks back and forward", async () => {
    const { element, player, authorize } = build(threeSegmentProjection);
    expect(player.getDuration()).toBe(66.467);
    player.prepare();
    void player.play();
    await vi.waitFor(() => expect(element.src).toContain(three[0]));
    for (const id of three.slice(1)) {
      await playing(element, player);
      fire(element, "ended");
      await vi.waitFor(() => expect(element.src).toContain(id));
    }
    await playing(element, player);
    expect(player.getCurrentTime()).toBe(60);
    expect(authorized(authorize)).toEqual(three);

    // Back-seek into the first segment re-authorizes it; local offset 15 s.
    player.setTime(15);
    expect(player.getCurrentTime()).toBe(15);
    await vi.waitFor(() => expect(element.src).toContain(three[0]));
    await playing(element, player);
    expect(element.currentTime).toBe(15);
    // Forward into the middle segment: global 45 s is its local 15 s.
    player.setTime(45);
    await vi.waitFor(() => expect(element.src).toContain(three[1]));
    await playing(element, player);
    expect(element.currentTime).toBe(15);
    element.currentTime = 16;
    fire(element, "timeupdate");
    expect(player.getCurrentTime()).toBe(46);
    expect(authorized(authorize)).toEqual([...three, three[0], three[1]]);
    player.destroy();
  });

  it("maps global seeks to segments, clamps out-of-range seeks and authorizes nothing while paused", async () => {
    const { element, player, authorize, seen } = build(pipecatProjection);
    player.prepare();
    await vi.waitFor(() => expect(seen.ready).toHaveBeenCalled());
    const times = [];
    player.on("timeupdate", (value) => times.push(value));
    player.setTime(30);
    player.setTime(29.999);
    player.setTime(-5);
    player.setTime(1e9);
    player.setTime(Number.NaN);
    player.setTime(Infinity);
    expect(times).toEqual([30, 29.999, 0, 36.467]);
    player.seekTo(0.5);
    expect(player.getCurrentTime()).toBeCloseTo(18.2335, 5);
    player.seekTo(1);
    expect(authorized(authorize)).toEqual([first]);
    expect(element.src).toBe("");
    // Play at the end restarts from the start of the call.
    void player.play();
    expect(player.getCurrentTime()).toBe(0);
    expect(element.src).toContain(first);
    player.destroy();
  });

  it("pauses without renewing and re-authorizes on resume only when the grant would lapse", async () => {
    vi.useFakeTimers();
    const { element, player, authorize } = build(pipecatProjection);
    player.prepare();
    void player.play();
    await playing(element, player);
    element.currentTime = 10;
    fire(element, "timeupdate");
    player.pause();
    expect(player.isPlaying()).toBe(false);
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    expect(authorized(authorize)).toEqual([first]);
    void player.play();
    await vi.waitFor(() => expect(element.src).toContain("grant-2"));
    expect(authorized(authorize)).toEqual([first, first]);
    element.currentTime = 0;
    await playing(element, player);
    expect(element.currentTime).toBe(10);
    expect(player.getCurrentTime()).toBe(10);
    player.destroy();
  });

  it("renews a stalled segment before its grant lapses, keeping the position", async () => {
    vi.useFakeTimers();
    const { element, player, authorize } = build(pipecatProjection);
    player.prepare();
    void player.play();
    await playing(element, player);
    element.currentTime = 12;
    fire(element, "timeupdate");
    await vi.advanceTimersByTimeAsync(49000);
    expect(authorize).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(authorize).toHaveBeenCalledTimes(2);
    expect(element.src).toContain("grant-2");
    element.currentTime = 0;
    await playing(element, player);
    expect(element.currentTime).toBe(12);
    expect(player.getCurrentTime()).toBe(12);
    player.destroy();
  });

  it.each([
    ["denied", { status: "not_found" }, "not_found", false],
    ["deleted", { status: "deleted" }, "deleted", false],
    ["expired", { status: "expired" }, "expired", false],
    [
      "near-expired",
      { expires_at: () => new Date(Date.now() + 1000).toISOString() },
      "not_found",
      false,
    ],
    ["mismatched", { occurrence_id: first }, "not_found", false],
    ["unreachable", new Error("canary-secret"), "not_found", true],
  ])(
    "fails closed at the boundary when the next segment is %s",
    async (_, outcome, status, retryable) => {
      const authorize = grants((part, issued) => {
        if (issued < 2) return undefined;
        if (outcome instanceof Error) throw outcome;
        if ("status" in outcome)
          return { occurrence_id: part.occurrence_id, ...outcome };
        return {
          ...segmentGrant(part.occurrence_id),
          ...outcome,
          expires_at: outcome.expires_at?.() ?? "2099-01-01T00:00:00+00:00",
        };
      });
      const { element, player, seen } = build(pipecatProjection, authorize);
      player.prepare();
      void player.play();
      await playing(element, player);
      fire(element, "ended");
      await vi.waitFor(() =>
        expect(seen.unavailable).toHaveBeenCalledWith({ status, retryable }),
      );
      expect(authorized(authorize)).toEqual([first, second]);
      expect(element.removeAttribute).toHaveBeenCalledWith("src");
      expect(element.src).toBe("");
      expect(player.isPlaying()).toBe(false);
      expect(player.getCurrentTime()).toBe(30);
      expect(seen.ended).not.toHaveBeenCalled();
    },
  );

  it("reauthorizes once on a media error and fails closed when the segment was revoked", async () => {
    const authorize = grants((part, issued) =>
      issued === 2
        ? { occurrence_id: part.occurrence_id, status: "not_found" }
        : undefined,
    );
    const { element, player, seen } = build(pipecatProjection, authorize);
    player.prepare();
    void player.play();
    await playing(element, player);
    fire(element, "error");
    await vi.waitFor(() =>
      expect(seen.unavailable).toHaveBeenCalledWith({
        status: "not_found",
        retryable: false,
      }),
    );
    expect(element.src).toBe("");
  });

  it("allows one recovery per player, then stops on the next media error", async () => {
    const { element, player, authorize, seen } = build(pipecatProjection);
    player.prepare();
    void player.play();
    await playing(element, player);
    fire(element, "error");
    await vi.waitFor(() => expect(element.src).toContain("grant-2"));
    expect(player.isPlaying()).toBe(false);
    void player.play();
    await playing(element, player);
    fire(element, "error");
    expect(seen.unavailable).toHaveBeenCalledWith({
      status: "not_found",
      retryable: true,
    });
    expect(authorize).toHaveBeenCalledTimes(2);
  });

  it("authorizes one segment at a time and the latest seek target wins", async () => {
    let release;
    let inFlight = 0;
    let most = 0;
    const authorize = vi.fn(async (part) => {
      inFlight += 1;
      most = Math.max(most, inFlight);
      if (part.occurrence_id === three[1])
        await new Promise((resolve) => {
          release = resolve;
        });
      inFlight -= 1;
      return segmentGrant(part.occurrence_id);
    });
    const { element, player } = build(threeSegmentProjection, authorize);
    player.prepare();
    void player.play();
    await playing(element, player);
    fire(element, "ended");
    await vi.waitFor(() => expect(release).toBeDefined());
    player.setTime(5);
    player.setTime(62);
    release();
    await vi.waitFor(() => expect(element.src).toContain(three[2]));
    expect(authorized(authorize)).toEqual([three[0], three[1], three[2]]);
    expect(most).toBe(1);
    await playing(element, player);
    expect(element.currentTime).toBe(2);
    expect(player.getCurrentTime()).toBe(62);
    player.destroy();
  });

  it("aborts an in-flight authorization on teardown and ignores its late grant", async () => {
    let finish;
    const authorize = vi.fn(
      (part, signal) =>
        new Promise((resolve) => {
          finish = () => resolve(segmentGrant(part.occurrence_id));
          authorize.signal = signal;
        }),
    );
    const { element, player, seen } = build(pipecatProjection, authorize);
    player.prepare();
    void player.play();
    player.destroy();
    expect(authorize.signal.aborted).toBe(true);
    finish();
    await Promise.resolve();
    await Promise.resolve();
    expect(seen.ready).not.toHaveBeenCalled();
    expect(element.src).toBe("");
    expect(player.isPlaying()).toBe(false);
  });

  it("keeps a recorded gap as silent time and starts the next segment at its own start_ms", async () => {
    vi.useFakeTimers();
    const gapped = {
      ...pipecatProjection,
      manifest: {
        ...pipecatProjection.manifest,
        capture_complete: false,
        segments: [
          pipecatProjection.manifest.segments[0],
          { ...pipecatProjection.manifest.segments[1], start_ms: 32000 },
        ],
        gaps: [
          { start_ms: 30000, duration_ms: 2000, reason: "source_unavailable" },
        ],
        duration_ms: 38467,
      },
    };
    const { element, player, authorize } = build(gapped);
    player.prepare();
    void player.play();
    await playing(element, player);
    fire(element, "ended");
    expect(player.getCurrentTime()).toBe(30);
    expect(player.isPlaying()).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(player.getCurrentTime()).toBeCloseTo(31, 5);
    expect(authorized(authorize)).toEqual([first]);
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(element.src).toContain(second));
    expect(player.getCurrentTime()).toBe(32);
    await playing(element, player);
    element.currentTime = 1;
    fire(element, "timeupdate");
    expect(player.getCurrentTime()).toBe(33);
    player.destroy();
  });
});
