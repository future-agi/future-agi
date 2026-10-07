import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { state, FakeMultiTrack } = vi.hoisted(() => {
  const state = { tracks: null, instance: null, initAllAudios: vi.fn() };
  class FakeWave {
    on() {}

    once() {}
  }
  class FakeMultiTrack {
    constructor(tracks) {
      state.tracks = tracks;
      state.instance = this;
      this.wavesurfers = tracks.map(() => new FakeWave());
    }

    on() {}

    once() {}

    initAllAudios() {
      state.initAllAudios();
    }

    destroy() {}

    play() {}

    pause() {}
  }
  return { state, FakeMultiTrack };
});

vi.mock("wavesurfer-multitrack", () => ({ default: FakeMultiTrack }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="icon" data-icon={icon} {...props} />
  ),
}));
vi.mock("src/sections/test-detail/AudioDownloadButton", () => ({
  default: () => null,
}));

import MultiTrackAudioPlayer from "./MultiTrackAudioPlayer";

const tracks = [
  { url: "http://localhost/customer.wav", name: "Customer" },
  { url: "http://localhost/assistant.wav", name: "Assistant" },
];

describe("MultiTrackAudioPlayer media lifecycle", () => {
  beforeEach(() => {
    state.tracks = null;
    state.instance = null;
    state.initAllAudios.mockReset();
  });

  it("creates one owned media element per track without a second initialization", () => {
    render(<MultiTrackAudioPlayer trackUrls={tracks} allowDownload={false} />);

    expect(state.tracks).toHaveLength(2);
    expect(state.tracks.map(({ options }) => options.media)).toHaveLength(2);
    expect(state.initAllAudios).not.toHaveBeenCalled();
  });

  it("ends loading when one owned media element reports an error", async () => {
    render(<MultiTrackAudioPlayer trackUrls={tracks} allowDownload={false} />);

    fireEvent.error(state.tracks[1].options.media);

    await waitFor(() =>
      expect(
        screen.getByText("Unable to load audio recording."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "play-pause" })).toBeDisabled();
  });
});
