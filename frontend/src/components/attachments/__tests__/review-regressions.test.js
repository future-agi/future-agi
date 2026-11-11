import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { listAttachments, resolveAttachments } from "../client";
import { resolveResultSchema } from "../contract";
import StreamingPlayback from "../StreamingPlayback";
import { grant, occurrence, occurrenceId, parent, projectId } from "./fixtures";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));
beforeEach(() => vi.resetAllMocks());
const items = [{ occurrence_id: occurrenceId, parent, representation: "playable" }];
function respond(result) {
  axios.post.mockImplementation(async (path) => path.endsWith("attachment-media-sessions")
    ? { status: 204 } : { data: { items: [result] } });
}

describe("backend wire regression fixtures", () => {
  // Python datetime.isoformat(), as used by playback.resolve; not JS toISOString.
  it.each(["2099-01-01T00:00:00+00:00", "2099-01-01T05:30:00.123456+05:30", "2098-12-31T17:00:00-07:00", "2099-01-01T00:00:00Z"])(
    "accepts resolver AND occurrence expiry %s", async (expires_at) => {
      const result = { ...grant, expires_at };
      respond(result);
      expect(await resolveAttachments(projectId, items)).toEqual([result]);
      const page = { items: [{ ...occurrence, expires_at }], next_cursor: null };
      axios.get.mockResolvedValue({ data: page });
      expect(await listAttachments(projectId, parent)).toEqual(page);
    },
  );
  it.each(["2000-01-01T00:00:00+00:00", "2099-01-01T00:00:00", "2099-02-30T00:00:00+00:00", "2099-01-01T00:00:00+99:00", "not-a-date"])(
    "still rejects invalid/expired grants %s", async (expires_at) => {
      respond({ ...grant, expires_at });
      await expect(resolveAttachments(projectId, items)).rejects.toThrow("Attachment unavailable");
    },
  );
  it.each([projectId, "00000000-0000-0000-0000-000000000000", "f".repeat(36), "-".repeat(36)])(
    "rejects gateway identity %s before handing a path to media", async (id) => {
      const result = { ...grant, gateway_path: `/tracer/v1/attachment-media/${id}/synthetic` };
      expect(resolveResultSchema.safeParse(result).success).toBe(false);
      respond(result);
      await expect(resolveAttachments(projectId, items)).rejects.toThrow("Attachment unavailable");
      expect(axios.get).not.toHaveBeenCalled();
    },
  );
});

function media(readyState = 4) {
  return Object.assign(new EventTarget(), {
    currentTime: 0, readyState, seeking: false,
    play: vi.fn().mockResolvedValue(), pause: vi.fn(), load: vi.fn(), removeAttribute: vi.fn(),
  });
}
const event = (element, name) => element.dispatchEvent(new Event(name));
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

describe("shared readiness barrier", () => {
  it("does not start the fast channel until the slow channel can play", async () => {
    const a = media(), b = media(0), player = new StreamingPlayback([a, b], 7200);
    await player.play();
    expect(a.play).not.toHaveBeenCalled();
    b.readyState = 1; event(b, "loadedmetadata");
    expect(a.play).not.toHaveBeenCalled();
    b.readyState = 4; event(b, "canplay"); await settle();
    expect(a.currentTime).toBe(0); expect(b.currentTime).toBe(0);
    expect(player.isPlaying()).toBe(true);
    player.destroy();
  });
  it("holds every channel on midstream buffering, then resumes at the earliest clock", async () => {
    const a = media(), b = media(), player = new StreamingPlayback([a, b], 7200);
    await player.play();
    a.currentTime = 10.5; b.currentTime = 10; b.readyState = 2;
    event(b, "waiting");
    expect(player.isPlaying()).toBe(false);
    expect(a.pause).toHaveBeenCalled(); expect(b.pause).toHaveBeenCalled();
    expect(player.getCurrentTime()).toBe(10); expect(a.currentTime).toBe(10);
    event(a, "timeupdate"); expect(player.getCurrentTime()).toBe(10);
    b.readyState = 4; event(b, "canplay"); await settle();
    expect(player.isPlaying()).toBe(true); expect(b.currentTime).toBe(10);
    player.destroy();
  });
  it("keeps all tracks paused until an asymmetric seek finishes", async () => {
    const a = media(), b = media(), player = new StreamingPlayback([a, b], 7200);
    await player.play();
    b.seeking = true; b.readyState = 1;
    player.setTime(7000);
    expect(player.isPlaying()).toBe(false); expect(a.play).toHaveBeenCalledTimes(1);
    event(a, "seeked"); expect(a.play).toHaveBeenCalledTimes(1);
    b.seeking = false; b.readyState = 4; event(b, "seeked"); await settle();
    expect(player.isPlaying()).toBe(true);
    expect([a.currentTime, b.currentTime]).toEqual([7000, 7000]);
    player.destroy();
  });
  it("retains play intent and clock through renewal during a stall", async () => {
    const a = media(), b = media(), player = new StreamingPlayback([a, b], 7200);
    await player.play(); a.currentTime = 20; b.currentTime = 20;
    b.readyState = 2; event(b, "waiting");
    player.replaceSources(["/fresh-a", "/fresh-b"]);
    a.currentTime = 0; b.currentTime = 0;
    event(a, "loadedmetadata"); event(a, "canplay");
    expect(a.play).toHaveBeenCalledTimes(1);
    b.readyState = 4; event(b, "loadedmetadata"); await settle();
    expect([a.currentTime, b.currentTime]).toEqual([20, 20]);
    expect(player.isPlaying()).toBe(true); player.destroy();
  });
  it("user pause cancels buffering intent, including subsequent ready events", async () => {
    const a = media(), b = media(0), player = new StreamingPlayback([a, b], 7200);
    await player.play(); player.pause();
    b.readyState = 4; event(b, "canplay"); await settle();
    expect(a.play).not.toHaveBeenCalled(); expect(player.isPlaying()).toBe(false);
    player.destroy();
  });
  it("invalidates pending play promises when a required track stalls", async () => {
    const a = media(), b = media(), player = new StreamingPlayback([a, b], 7200);
    let finish;
    b.play.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const playing = player.play(); b.readyState = 2; event(b, "waiting");
    finish(); await playing;
    expect(player.isPlaying()).toBe(false);
    b.readyState = 4; event(b, "canplay"); await settle();
    expect(player.isPlaying()).toBe(true); player.destroy();
  });
});
