import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import useStereoChannels, {
  assistantChannelIndex,
} from "src/hooks/use-stereo-channels";

// 0 is the left channel, 1 the right.
describe("assistantChannelIndex", () => {
  it("follows the backend's layout over the call's direction and provider", () => {
    const leftCustomer = { left: "customer", right: "assistant" };
    const leftAssistant = { left: "assistant", right: "customer" };
    expect(assistantChannelIndex(leftCustomer, true, "phone")).toBe(1);
    expect(assistantChannelIndex(leftAssistant, false, "livekit")).toBe(0);
  });

  // Checked against both fallback answers, so a layout wrongly taken as valid
  // fails one of them.
  it("ignores a layout that doesn't name one customer and one assistant", () => {
    const bad = [
      { left: "assistant", right: "assistant" },
      { left: "agent", right: "customer" },
      { left: "customer" },
      "left-customer",
    ];
    bad.forEach((layout) => {
      expect(assistantChannelIndex(layout, false, "vapi")).toBe(1);
      expect(assistantChannelIndex(layout, true, "vapi")).toBe(0);
    });
  });

  it("falls back to the direction rule without a layout", () => {
    expect(assistantChannelIndex(null, false, "vapi")).toBe(1);
    expect(assistantChannelIndex(null, true, "vapi")).toBe(0);
    expect(assistantChannelIndex(null, true, "livekit")).toBe(1);
  });
});

// A two-channel recording whose left channel is quiet and right is loud, so
// the assistant's peaks show which channel it was given.
describe("useStereoChannels", () => {
  const URL_STEREO = "https://example.test/stereo.wav";
  const LEFT = 0.2;
  const RIGHT = 0.8;
  const leftCustomer = { left: "customer", right: "assistant" };

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
        }),
      ),
    );
    vi.stubGlobal(
      "AudioContext",
      class {
        decodeAudioData() {
          return Promise.resolve({
            numberOfChannels: 2,
            sampleRate: 8000,
            getChannelData: (i) =>
              new Float32Array(100).fill(i === 0 ? LEFT : RIGHT),
          });
        }

        close() {
          return Promise.resolve();
        }
      },
    );
    // jsdom has no object URLs.
    URL.createObjectURL = vi.fn(() => "blob:track");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    // Unmount first: the hook revokes its object URLs on the way out.
    cleanup();
    vi.unstubAllGlobals();
    delete URL.createObjectURL;
    delete URL.revokeObjectURL;
  });

  const assistantLevel = (result) => result.current.assistantPeaks?.[0];

  it("re-splits with the layout when it arrives for the same recording", async () => {
    const { result, rerender } = renderHook(
      ({ layout }) => useStereoChannels(URL_STEREO, true, "phone", layout),
      { initialProps: { layout: null } },
    );
    // Without a layout, an inbound phone call takes the flipped guess: left.
    await waitFor(() => expect(assistantLevel(result)).toBeCloseTo(LEFT));

    rerender({ layout: leftCustomer });
    await waitFor(() => expect(assistantLevel(result)).toBeCloseTo(RIGHT));
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("still finishes when the fallback rule's inputs change mid-download", async () => {
    let release;
    const download = new Promise((resolve) => {
      release = resolve;
    });
    fetch.mockImplementation(() => download);
    const { result, rerender } = renderHook(
      ({ isInbound }) =>
        useStereoChannels(URL_STEREO, isInbound, "phone", null),
      { initialProps: { isInbound: true } },
    );

    rerender({ isInbound: false });
    release({
      ok: true,
      arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.assistantUrl).toBeTruthy();
    // The channel picked when the download started stands, as it always did.
    expect(assistantLevel(result)).toBeCloseTo(LEFT);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("doesn't re-split when only the fallback rule's inputs change", async () => {
    const { result, rerender } = renderHook(
      ({ isInbound }) =>
        useStereoChannels(URL_STEREO, isInbound, "phone", null),
      { initialProps: { isInbound: true } },
    );
    await waitFor(() => expect(assistantLevel(result)).toBeCloseTo(LEFT));

    rerender({ isInbound: false });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(assistantLevel(result)).toBeCloseTo(LEFT);
  });
});
