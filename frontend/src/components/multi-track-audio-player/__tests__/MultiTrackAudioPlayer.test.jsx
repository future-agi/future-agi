import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

// These mirror how wavesurfer-multitrack actually behaves, which is the whole
// reason the component is built the way it is:
//
//   success -> the multitrack emits "canplay", and only by then are its
//              wavesurfers constructed, so "ready" listeners can attach.
//   failure -> the multitrack emits nothing at all and `wavesurfers` stays
//              empty forever (verified in a browser), so a failing track can
//              never be observed through the library. It is observed through
//              the media element the component owns and hands to the track.
const makeWavesurfer = () => {
  const handlers = {};
  return {
    on: (event, cb) => {
      (handlers[event] ||= []).push(cb);
    },
    emit: (event, ...args) => (handlers[event] || []).forEach((cb) => cb(...args)),
  };
};

const instances = [];

vi.mock("wavesurfer-multitrack", () => ({
  default: class FakeMultiTrack {
    constructor(tracks) {
      this.tracks = tracks;
      // Empty until canplay, exactly like the real one.
      this.wavesurfers = [];
      this.handlers = {};
      instances.push(this);
    }
    on(event, cb) {
      (this.handlers[event] ||= []).push(cb);
    }
    /** The tracks' media loaded, so the wavesurfers now exist and canplay
     *  fires — but nothing has finished rendering a waveform yet. */
    arrive() {
      this.wavesurfers = this.tracks.map(() => makeWavesurfer());
      (this.handlers.canplay || []).forEach((cb) => cb());
    }
    /** Drive the success path the way the library does. */
    succeed() {
      this.arrive();
      this.wavesurfers.forEach((ws) => ws.emit("ready"));
    }
    initAllAudios() {}
    destroy() {}
    play() {}
    pause() {}
  },
}));

vi.mock("src/sections/test-detail/AudioDownloadButton", () => ({
  default: () => <span data-testid="download" />,
}));

import MultiTrackAudioPlayer from "../MultiTrackAudioPlayer";

// MediaError codes: 2 NETWORK, 3 DECODE, 4 SRC_NOT_SUPPORTED.
const SRC_NOT_SUPPORTED = 4;
const NETWORK = 2;
const DECODE = 3;

const TRACKS = [
  { url: "https://example.test/customer.wav", color: "#f00", name: "Customer Audio" },
  { url: "https://example.test/assistant.wav", color: "#00f", name: "Assistant Audio" },
];

const LOADING_COPY = /painting sound waves/i;
const UNAVAILABLE_COPY = /recording unavailable/i;
const FAILED_COPY = /audio failed to load/i;

const medias = [];
let RealAudio;

beforeEach(() => {
  instances.length = 0;
  medias.length = 0;
  RealAudio = window.Audio;
  // Capture the elements the component creates so a test can fail them.
  window.Audio = function FakeAudio() {
    const el = new RealAudio();
    medias.push(el);
    return el;
  };
});

afterEach(() => {
  window.Audio = RealAudio;
});

const failMedia = (index, code) =>
  act(() => {
    Object.defineProperty(medias[index], "error", {
      configurable: true,
      value: { code },
    });
    medias[index].dispatchEvent(new Event("error"));
  });

const latest = () => instances[instances.length - 1];

const renderPlayer = (props = {}) =>
  render(<MultiTrackAudioPlayer trackUrls={TRACKS} id="call-1" {...props} />);

describe("MultiTrackAudioPlayer loading state", () => {
  it("shows the waveform once the tracks load", async () => {
    renderPlayer();

    act(() => latest().succeed());

    await waitFor(() =>
      expect(screen.queryByText(LOADING_COPY)).not.toBeInTheDocument(),
    );
  });

  it("keeps the loader while the tracks have reported nothing", () => {
    renderPlayer();

    expect(screen.getByText(LOADING_COPY)).toBeInTheDocument();
    expect(screen.queryByText(UNAVAILABLE_COPY)).not.toBeInTheDocument();
    expect(screen.queryByText(FAILED_COPY)).not.toBeInTheDocument();
  });
});

describe("MultiTrackAudioPlayer — recording unavailable", () => {
  it("reports the recording as unavailable when the source is rejected", async () => {
    renderPlayer();

    failMedia(0, SRC_NOT_SUPPORTED);

    expect(await screen.findByText(UNAVAILABLE_COPY)).toBeInTheDocument();
    expect(screen.queryByText(LOADING_COPY)).not.toBeInTheDocument();
  });

  it("offers no retry, because a rejected source will be rejected again", async () => {
    renderPlayer();

    failMedia(0, SRC_NOT_SUPPORTED);

    await screen.findByText(UNAVAILABLE_COPY);
    expect(screen.queryByRole("button", { name: /^retry$/i })).not.toBeInTheDocument();
  });

  // "No recording at all" is the caller's case, not this component's: it owns
  // the "No recording found" message. Reporting a failure here would replace
  // that with the wrong one.
  it("does not claim a failure when there is no url to play", () => {
    renderPlayer({
      trackUrls: [
        { url: "", color: "#f00", name: "Customer Audio" },
        { url: "", color: "#00f", name: "Assistant Audio" },
      ],
    });

    expect(screen.queryByText(UNAVAILABLE_COPY)).not.toBeInTheDocument();
    expect(screen.queryByText(FAILED_COPY)).not.toBeInTheDocument();
  });
});

describe("MultiTrackAudioPlayer — audio failed to load", () => {
  it("reports a network drop as a load failure, not an absent recording", async () => {
    renderPlayer();

    failMedia(0, NETWORK);

    expect(await screen.findByText(FAILED_COPY)).toBeInTheDocument();
    expect(screen.queryByText(UNAVAILABLE_COPY)).not.toBeInTheDocument();
  });

  it("reports an undecodable file as a load failure", async () => {
    renderPlayer();

    failMedia(0, DECODE);

    expect(await screen.findByText(FAILED_COPY)).toBeInTheDocument();
  });

  it("offers a retry, because the recording is there to try again", async () => {
    renderPlayer();

    failMedia(0, NETWORK);

    await screen.findByText(FAILED_COPY);
    expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
  });

  it("recovers when the retry succeeds", async () => {
    const user = userEvent.setup();
    renderPlayer();

    failMedia(0, NETWORK);
    await screen.findByText(FAILED_COPY);
    const before = instances.length;

    await user.click(screen.getByRole("button", { name: /^retry$/i }));
    await waitFor(() => expect(instances.length).toBeGreaterThan(before));

    act(() => latest().succeed());

    await waitFor(() =>
      expect(screen.queryByText(FAILED_COPY)).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(LOADING_COPY)).not.toBeInTheDocument();
  });
});

// The media element loading is only half the load: wavesurfer fetches and
// decodes the file again for the waveform. That second pass can fail on a
// source the element played, and nothing else reports it.
describe("MultiTrackAudioPlayer — wavesurfer's own load fails", () => {
  it("reports a load failure when a waveform fails after the media arrived", async () => {
    renderPlayer();

    act(() => latest().arrive());
    expect(screen.getByText(LOADING_COPY)).toBeInTheDocument();

    act(() => latest().wavesurfers[0].emit("error", new Error("decode failed")));

    expect(await screen.findByText(FAILED_COPY)).toBeInTheDocument();
    expect(screen.queryByText(LOADING_COPY)).not.toBeInTheDocument();
  });

  it("still shows the waveform when one track readies and none error", async () => {
    renderPlayer();

    act(() => latest().succeed());

    await waitFor(() =>
      expect(screen.queryByText(LOADING_COPY)).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(FAILED_COPY)).not.toBeInTheDocument();
  });
});

describe("MultiTrackAudioPlayer failure behaviour", () => {
  it("keeps the first failure when two tracks fail for different reasons", async () => {
    renderPlayer();

    failMedia(0, SRC_NOT_SUPPORTED);
    failMedia(1, NETWORK);

    // Arrival order must not decide whether a refused source offers a retry.
    expect(await screen.findByText(UNAVAILABLE_COPY)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^retry$/i })).not.toBeInTheDocument();
  });

  it("fails when one track errors even though the other is fine", async () => {
    renderPlayer();

    act(() => latest().arrive());
    act(() => latest().wavesurfers[0].emit("ready"));
    failMedia(1, SRC_NOT_SUPPORTED);

    expect(await screen.findByText(UNAVAILABLE_COPY)).toBeInTheDocument();
  });

  it("ignores a media error that arrives after teardown", async () => {
    const { unmount } = renderPlayer();
    const stale = medias[0];

    unmount();

    // Would warn about setting state on an unmounted component if the
    // listener were still attached.
    expect(() => {
      Object.defineProperty(stale, "error", {
        configurable: true,
        value: { code: SRC_NOT_SUPPORTED },
      });
      stale.dispatchEvent(new Event("error"));
    }).not.toThrow();
  });

  // A dead grey play button next to "Recording unavailable" is clutter: there
  // is nothing to play, and the download button already hides itself.
  it("hides the transport controls when the recording has failed", async () => {
    renderPlayer();

    failMedia(0, SRC_NOT_SUPPORTED);

    await screen.findByText(UNAVAILABLE_COPY);
    expect(screen.queryByRole("button", { name: /play-pause/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("download")).not.toBeInTheDocument();
  });

  it("keeps the transport controls while still loading", () => {
    renderPlayer();

    expect(screen.getByRole("button", { name: /play-pause/i })).toBeDisabled();
  });

  // The real failure carries the recording URL, which can be a private one.
  it("never renders the underlying media error", async () => {
    renderPlayer();

    failMedia(0, SRC_NOT_SUPPORTED);

    await screen.findByText(UNAVAILABLE_COPY);
    expect(screen.queryByText(/example\.test/)).not.toBeInTheDocument();
  });

  it("hands the track the media element it owns, so playback is not fetched twice", () => {
    renderPlayer();

    expect(medias).toHaveLength(TRACKS.length);
    latest().tracks.forEach((track, i) => {
      expect(track.options.media).toBe(medias[i]);
    });
  });
});
