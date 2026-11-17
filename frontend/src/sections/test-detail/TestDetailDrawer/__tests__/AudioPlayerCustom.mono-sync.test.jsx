import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import VoiceAudioBridge from "src/components/VoiceDetailDrawerV2/VoiceAudioBridge";
import useVoiceAudioStore from "src/components/VoiceDetailDrawerV2/voiceAudioStore";
import AudioPlayerCustom, {
  StereoMultiTrackPlayer,
} from "../AudioPlayerCustom";

const { captured, stereo } = vi.hoisted(() => ({
  captured: { props: null, instance: null },
  stereo: { assistantUrl: "", customerUrl: "", loading: false, error: null },
}));

vi.mock("src/hooks/use-stereo-channels", () => ({
  default: () => stereo,
}));

vi.mock("src/components/iconify", () => ({
  default: () => <span />,
}));

vi.mock(
  "src/components/multi-track-audio-player/MultiTrackAudioPlayer",
  () => ({
    // The real waveform publishes this contract once its playable tracks load.
    // eslint-disable-next-line react/prop-types
    default: function MockMultiTrackPlayer({
      trackUrls,
      audioUrls,
      id,
      height,
      onInstance,
    }) {
      captured.props = { trackUrls, audioUrls, id, height, onInstance };
      return (
        <button type="button" onClick={() => onInstance?.(captured.instance)}>
          Recording ready
        </button>
      );
    },
    MemoizedBarsIcon: () => <span />,
  }),
);

vi.mock("src/components/custom-audio/CustomAudioPlayer", () => ({
  default: () => <div data-testid="standalone-player" />,
}));

vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({
    // eslint-disable-next-line react/prop-types
    AudioPlaybackProvider: ({ children }) => <div>{children}</div>,
  }),
);

vi.mock("src/sections/test-detail/AudioDownloadButton", () => ({
  default: () => <span />,
}));

const COMBINED = "https://example.test/combined.wav";
const PROJECT_CALL = {
  id: "observe-mono-65",
  module: "project",
  status: "completed",
  recording_available: true,
  call_metadata: {},
  recording: { mono: { combined_url: COMBINED } },
};
const frames = new Map();
let nextFrame = 0;

const renderWithQuery = (element) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      {element}
    </QueryClientProvider>,
  );

beforeEach(() => {
  useVoiceAudioStore.getState().reset();
  captured.props = null;
  captured.instance = {
    multitrack: {
      setTime: vi.fn(),
      getCurrentTime: vi.fn(() => 0),
      isPlaying: vi.fn(() => false),
      zoom: vi.fn(),
    },
    wavesurfers: [{ getDuration: () => 120, getCurrentTime: () => 0 }],
  };
  Object.assign(stereo, {
    assistantUrl: "",
    customerUrl: "",
    loading: false,
    error: null,
  });
  frames.clear();
  nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", (callback) => {
    const id = ++nextFrame;
    frames.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id));
});

afterEach(() => {
  cleanup();
  useVoiceAudioStore.getState().reset();
  frames.clear();
  vi.unstubAllGlobals();
});

describe("OBS-SW-65 combined recording synchronization", () => {
  it.each([
    { label: "nested Observe recording", data: PROJECT_CALL },
    {
      label: "flat combined recording",
      data: {
        id: "observe-mono-65",
        status: "completed",
        recordings: { combined: COMBINED },
      },
    },
    {
      label: "canonical audio_url fallback",
      data: {
        id: "observe-mono-65",
        status: "completed",
        audio_url: COMBINED,
      },
    },
  ])(
    "forwards a single combined track and bridge props for $label",
    ({ data }) => {
      const onInstance = vi.fn();
      renderWithQuery(
        <AudioPlayerCustom data={data} onInstance={onInstance} />,
      );

      expect(captured.props).not.toBeNull();
      expect(captured.props.trackUrls).toEqual([
        expect.objectContaining({ url: COMBINED, name: "Combined Audio" }),
      ]);
      expect(captured.props.id).toBe(data.id);
      expect(captured.props.height).toBe(70);
      expect(captured.props.audioUrls).toEqual(
        expect.objectContaining({ combined: COMBINED, mono: COMBINED }),
      );
      expect(captured.props.onInstance).toBe(onInstance);
      expect(screen.queryByTestId("standalone-player")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Recording ready" }));
      expect(onInstance).toHaveBeenCalledWith(captured.instance);
    },
  );

  it("keeps the combined fallback synchronized after stereo splitting fails", () => {
    stereo.error = "Failed to fetch stereo audio: 403";
    const recordings = {
      stereo: "https://example.test/stereo.wav",
      combined: COMBINED,
      mono: COMBINED,
    };
    const onInstance = vi.fn();
    render(
      <StereoMultiTrackPlayer
        recordings={recordings}
        id="stereo-fallback-65"
        height={42}
        onInstance={onInstance}
      />,
    );

    expect(captured.props).not.toBeNull();
    expect(captured.props.trackUrls.map(({ url }) => url)).toEqual([COMBINED]);
    expect(captured.props.audioUrls).toBe(recordings);
    expect(captured.props.id).toBe("stereo-fallback-65");
    expect(captured.props.height).toBe(42);
    expect(captured.props.onInstance).toBe(onInstance);
  });

  it("refreshes the combined track when the recording URL changes", () => {
    const onInstance = vi.fn();
    const { rerender } = render(
      <StereoMultiTrackPlayer
        recordings={{ combined: COMBINED }}
        id="call-65"
        onInstance={onInstance}
      />,
    );
    expect(captured.props).not.toBeNull();
    const initialTracks = captured.props.trackUrls;

    rerender(
      <StereoMultiTrackPlayer
        recordings={{ combined: COMBINED }}
        id="call-65"
        onInstance={onInstance}
      />,
    );
    expect(captured.props.trackUrls).toBe(initialTracks);

    const replacement = "https://example.test/replacement.wav";
    rerender(
      <StereoMultiTrackPlayer
        recordings={{ combined: replacement }}
        id="call-66"
        onInstance={onInstance}
      />,
    );
    expect(captured.props.trackUrls).not.toBe(initialTracks);
    expect(captured.props.trackUrls.map(({ url }) => url)).toEqual([
      replacement,
    ]);
    expect(captured.props.id).toBe("call-66");
  });

  it("seeks a combined recording through the real voice store", () => {
    renderWithQuery(<VoiceAudioBridge data={PROJECT_CALL} />);
    fireEvent.click(screen.getByRole("button", { name: "Recording ready" }));

    act(() => useVoiceAudioStore.getState().seekTo(42));

    expect(captured.instance.multitrack.setTime).toHaveBeenCalledWith(42);
    expect(useVoiceAudioStore.getState().multitrack).toBe(
      captured.instance.multitrack,
    );
    expect(useVoiceAudioStore.getState().currentTime).toBe(42);
    expect(useVoiceAudioStore.getState().pendingSeek).toBeNull();
  });

  it("publishes combined playback time and playing state through the real bridge", () => {
    renderWithQuery(<VoiceAudioBridge data={PROJECT_CALL} />);
    fireEvent.click(screen.getByRole("button", { name: "Recording ready" }));
    captured.instance.multitrack.getCurrentTime.mockReturnValue(19.5);
    captured.instance.multitrack.isPlaying.mockReturnValue(true);

    act(() => {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(16));
    });

    expect(useVoiceAudioStore.getState().currentTime).toBe(19.5);
    expect(useVoiceAudioStore.getState().isPlaying).toBe(true);
    expect(useVoiceAudioStore.getState().duration).toBe(120);
  });
});
