import { describe, expect, it, vi } from "vitest";
import StreamingPlayback from "../StreamingPlayback";

function media() {
  const element = new EventTarget();
  Object.assign(element, {
    currentTime: 0,
    readyState: 1,
    play: vi.fn().mockResolvedValue(),
    pause: vi.fn(),
    load: vi.fn(),
    removeAttribute: vi.fn(),
  });
  return element;
}

describe("shared streaming transport", () => {
  it.each([1, 2])(
    "synchronizes time, transcript seek and play/pause with %i tracks",
    async (count) => {
      const elements = Array.from({ length: count }, media);
      const player = new StreamingPlayback(elements, 7200);
      const tick = vi.fn();
      player.on("timeupdate", tick);
      await player.play();
      expect(player.isPlaying()).toBe(true);
      player.setTime(7198);
      expect(elements.map((element) => element.currentTime)).toEqual(
        Array(count).fill(7198),
      );
      elements[0].currentTime = 7199;
      elements[0].dispatchEvent(new Event("timeupdate"));
      expect(player.getCurrentTime()).toBe(7199);
      expect(tick).toHaveBeenLastCalledWith(7199);
      player.pause();
      expect(player.isPlaying()).toBe(false);
      player.destroy();
      elements.forEach((element) =>
        expect(element.removeAttribute).toHaveBeenCalledWith("src"),
      );
    },
  );
  it("preserves seek and playing intent across grant renewal", async () => {
    const element = media();
    const player = new StreamingPlayback([element], 7200);
    await player.play();
    player.setTime(7000);
    player.replaceSources(["/new-grant"]);
    element.currentTime = 0;
    element.dispatchEvent(new Event("loadedmetadata"));
    expect(element.currentTime).toBe(7000);
    expect(element.preload).toBe("none");
    expect(element.play).toHaveBeenCalledTimes(2);
    player.destroy();
  });
  it("stops all tracks when any track fails instead of playing a partial direction", async () => {
    const elements = [media(), media()];
    elements[1].play.mockRejectedValue(new Error("canary-secret"));
    const player = new StreamingPlayback(elements, 100);
    const error = vi.fn();
    player.on("error", error);
    await player.play();
    expect(player.isPlaying()).toBe(false);
    expect(elements[0].pause).toHaveBeenCalled();
    expect(error).toHaveBeenCalledWith(undefined);
    player.destroy();
  });
  it("never resumes or publishes state after cancellation", async () => {
    const element = media();
    let finish;
    element.play.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const player = new StreamingPlayback([element], 100);
    const playEvent = vi.fn();
    player.on("play", playEvent);
    const pending = player.play();
    player.destroy();
    finish();
    await pending;
    expect(playEvent).not.toHaveBeenCalled();
    expect(player.isPlaying()).toBe(false);
  });
  it("clamps seek and removes listeners on teardown", () => {
    const element = media();
    const player = new StreamingPlayback([element], 100);
    player.setTime(Infinity);
    expect(player.getCurrentTime()).toBe(0);
    player.setTime(-1);
    expect(player.getCurrentTime()).toBe(0);
    player.seekTo(2);
    expect(player.getCurrentTime()).toBe(100);
    player.destroy();
    element.currentTime = 2;
    element.dispatchEvent(new Event("timeupdate"));
    expect(player.getCurrentTime()).toBe(100);
  });
});
