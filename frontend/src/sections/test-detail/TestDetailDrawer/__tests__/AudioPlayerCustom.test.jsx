import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const captured = { trackUrls: null, singleUrl: null };

// Mutable so a case can hand back real split channels; hoisted because the
// vi.mock factory below is lifted above this file's other statements.
const { stereo } = vi.hoisted(() => ({
  stereo: { assistantUrl: "", customerUrl: "", loading: false, error: null },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/hooks/use-stereo-channels", () => ({
  default: () => stereo,
}));

vi.mock("src/components/multi-track-audio-player/MultiTrackAudioPlayer", () => ({
  default: (props) => {
    captured.trackUrls = props.trackUrls;
    return <div data-testid="multi-track" />;
  },
  MemoizedBarsIcon: () => <span data-testid="bars" />,
}));

vi.mock("src/components/custom-audio/CustomAudioPlayer", () => ({
  default: (props) => {
    captured.singleUrl = props.audioData?.url;
    return <div data-testid="single-track" />;
  },
}));

vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({
    // eslint-disable-next-line react/prop-types
    AudioPlaybackProvider: ({ children }) => <div>{children}</div>,
  }),
);

// Path is resolved from the component under test, not from this file.
vi.mock("src/sections/test-detail/AudioDownloadButton", () => ({
  default: () => <span data-testid="download" />,
}));

// Imported after the mocks above so the component picks them up.
import AudioPlayerCustom, {
  StereoMultiTrackPlayer,
} from "../AudioPlayerCustom";

const COMBINED = "https://example.test/recording.wav";

const resetCaptured = () => {
  captured.trackUrls = null;
  captured.singleUrl = null;
  Object.assign(stereo, {
    assistantUrl: "",
    customerUrl: "",
    loading: false,
    error: null,
  });
};

describe("StereoMultiTrackPlayer track selection", () => {
  beforeEach(resetCaptured);

  it("splits a stereo recording into two channel tracks", () => {
    Object.assign(stereo, {
      assistantUrl: "blob:assistant",
      customerUrl: "blob:customer",
    });

    render(
      <StereoMultiTrackPlayer
        recordings={{ stereo: "https://example.test/stereo.wav" }}
        id="call-0"
      />,
    );

    expect(captured.trackUrls).toHaveLength(2);
    expect(captured.trackUrls.map((t) => t.url)).toEqual([
      "blob:customer",
      "blob:assistant",
    ]);
  });

  it("still splits into customer and assistant rows when channels exist", () => {
    render(
      <StereoMultiTrackPlayer
        recordings={{
          combined: COMBINED,
          assistant: "https://example.test/assistant.wav",
          customer: "https://example.test/customer.wav",
        }}
        id="call-2"
      />,
    );

    expect(captured.trackUrls).toHaveLength(2);
    expect(captured.trackUrls.map((t) => t.name)).toEqual([
      "Customer Audio",
      "Assistant Audio",
    ]);
  });

  it("uses the single-track bar when only a combined mix exists", () => {
    // A two-row waveform can neither be filled nor separate the speakers, and
    // it never reports ready while a track URL is missing.
    render(
      <StereoMultiTrackPlayer recordings={{ combined: COMBINED }} id="call-1" />,
    );

    expect(captured.singleUrl).toBe(COMBINED);
    expect(captured.trackUrls).toBeNull();
  });
});

describe("AudioPlayerCustom picks the renderer from the recording shape", () => {
  beforeEach(resetCaptured);

  it.each(["retell", "bland"])(
    "sends a single-URL %s call to the same single-track bar",
    (provider) => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <AudioPlayerCustom
            data={{
              module: "project",
              recording_available: true,
              call_metadata: { provider },
              recording: { mono: { combined_url: COMBINED } },
            }}
          />
        </QueryClientProvider>,
      );

      expect(captured.singleUrl).toBe(COMBINED);
      expect(captured.trackUrls).toBeNull();
    },
  );

  it("routes a call that names no provider anywhere", () => {
    // The observability detail payload carries `call_metadata: {}` and no
    // top-level provider, so the recording shape is the only signal available.
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AudioPlayerCustom
          data={{
            module: "project",
            recording_available: true,
            call_metadata: {},
            recording: { mono: { combined_url: COMBINED } },
          }}
        />
      </QueryClientProvider>,
    );

    expect(captured.singleUrl).toBe(COMBINED);
    expect(captured.trackUrls).toBeNull();
  });
});
