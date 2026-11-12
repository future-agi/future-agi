import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import SharedVoiceView from "../SharedVoiceView";

const captured = vi.hoisted(() => ({
  audio: null,
  details: null,
  transcript: null,
  stereoArgs: null,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/VoiceDetailDrawerV2/CallDetailsBar", () => ({
  default: ({ data }) => {
    captured.details = data;
    return null;
  },
}));
vi.mock("src/components/VoiceDetailDrawerV2/TranscriptView", () => ({
  default: ({ transcript }) => {
    captured.transcript = transcript;
    return createElement(
      "div",
      { "data-testid": "transcript" },
      transcript.map((turn) => turn.content).join("|"),
    );
  },
}));

// Keep the real project-mode recording gate and track selection. Only bypass
// the bridge's animation/resize synchronization, not AudioPlayerCustom.
vi.mock("src/components/VoiceDetailDrawerV2/VoiceAudioBridge", async () => {
  const { default: AudioPlayerCustom } = await import(
    "src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom"
  );
  return {
    default: ({ data }) => {
      captured.audio = data;
      return createElement(AudioPlayerCustom, { data });
    },
  };
});
vi.mock("src/hooks/use-stereo-channels", () => ({
  default: (...args) => {
    captured.stereoArgs = args;
    return {
      assistantUrl: args[0] ? "blob:assistant" : "",
      customerUrl: args[0] ? "blob:customer" : "",
      loading: false,
      error: null,
    };
  },
}));
vi.mock(
  "src/components/multi-track-audio-player/MultiTrackAudioPlayer",
  () => ({
    default: ({ trackUrls }) =>
      createElement(
        "div",
        { "data-testid": "multi-track" },
        trackUrls.map((track) => track.url || "").join("|"),
      ),
    MemoizedBarsIcon: () => null,
  }),
);
vi.mock("src/components/custom-audio/CustomAudioPlayer", () => ({
  default: ({ audioData }) =>
    createElement("div", { "data-testid": "single-track" }, audioData.url),
}));
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({
    AudioPlaybackProvider: ({ children }) => children,
  }),
);
vi.mock("src/sections/test-detail/AudioDownloadButton", () => ({
  default: () => null,
}));

const URL = "https://audio.example.test/call.wav";
const TRANSCRIPT = [
  { role: "assistant", content: "How can I help?", start_time: 0, end_time: 1 },
  {
    role: "user",
    content: "Please check my order.",
    start_time: 1,
    end_time: 2,
  },
];
const AUDIO_UNAVAILABLE =
  "Audio recording is not available in this shared view.";
const TRANSCRIPT_UNAVAILABLE =
  "Transcript is not available in this shared view.";

function sharedTrace(attributes = {}, fields = {}) {
  return {
    id: "trace-shared",
    status: "completed",
    observation_spans: [
      {
        observation_span: { id: "root", observation_type: "span" },
        children: [
          {
            observation_span: {
              id: "conversation",
              observation_type: "conversation",
              status: "completed",
              span_attributes: attributes,
            },
          },
        ],
      },
    ],
    ...fields,
  };
}

function renderView(props) {
  return render(
    createElement(
      QueryClientProvider,
      {
        client: new QueryClient({
          defaultOptions: { queries: { retry: false } },
        }),
      },
      createElement(SharedVoiceView, props),
    ),
  );
}

beforeEach(() => {
  Object.assign(captured, {
    audio: null,
    details: null,
    transcript: null,
    stereoArgs: null,
  });
});

describe("OBS-SW-20 shared Observe normalization", () => {
  it("decodes the collector transcript and plays its mono recording", () => {
    renderView({
      resourceData: sharedTrace({
        raw_log: JSON.stringify({ transcript: "Provider-native plain text" }),
        "fi.conversation.transcript": JSON.stringify(TRANSCRIPT),
        "conversation.recording.mono.combined": URL,
      }),
    });

    expect(captured.transcript).toEqual(TRANSCRIPT);
    expect(screen.getByTestId("single-track")).toHaveTextContent(URL);
    expect(captured.audio).toMatchObject({
      module: "project",
      recording_available: true,
    });
    expect(screen.queryByText(AUDIO_UNAVAILABLE)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/No recording for this call/),
    ).not.toBeInTheDocument();
  });

  it.each([
    {
      name: "stereo",
      attribute: "conversation.recording.stereo",
      output: "blob:customer|blob:assistant",
    },
    {
      name: "assistant",
      attribute: "conversation.recording.mono.assistant",
      output: `|${URL}`,
    },
    {
      name: "customer",
      attribute: "conversation.recording.mono.customer",
      output: `${URL}|`,
    },
  ])(
    "plays a collector $name recording without legacy fields",
    ({ attribute, output }) => {
      renderView({ resourceData: sharedTrace({ [attribute]: URL }) });

      expect(screen.getByTestId("multi-track").textContent).toBe(output);
      expect(captured.audio.recording_available).toBe(true);
      expect(screen.queryByText(AUDIO_UNAVAILABLE)).not.toBeInTheDocument();
    },
  );

  it("prefers the normalized array over the provider-native transcript", () => {
    renderView({
      resourceData: sharedTrace({
        raw_log: { transcript: [{ role: "agent", content: "Unnormalized" }] },
        "fi.conversation.transcript": TRANSCRIPT,
      }),
    });

    expect(captured.transcript).toEqual(TRANSCRIPT);
  });

  it.each(["raw_log", "rawLog"])(
    "decodes %s before reading legacy voice fields",
    (key) => {
      renderView({
        resourceData: sharedTrace({
          [key]: JSON.stringify({
            transcript: TRANSCRIPT,
            audio_url: URL,
            duration_seconds: 12,
            phone_number: "redacted",
            ended_reason: "completed",
          }),
        }),
      });

      expect(captured.transcript).toEqual(TRANSCRIPT);
      expect(screen.getByTestId("single-track")).toHaveTextContent(URL);
      expect(captured.details).toMatchObject({
        duration_seconds: 12,
        phone_number: "redacted",
        ended_reason: "completed",
      });
    },
  );

  it.each([
    { name: "audio_url", fields: { audio_url: URL } },
    { name: "audioUrl", fields: { audioUrl: URL } },
    { name: "flat recordings", fields: { recordings: { combined: URL } } },
    {
      name: "snake-case nested recordings",
      fields: { recordings: { mono: { combined_url: URL } } },
    },
    {
      name: "camelCase nested recordings",
      fields: { recordings: { mono: { combinedUrl: URL } } },
    },
    {
      name: "empty nested recordings with audio fallback",
      fields: { recordings: { mono: {} }, audio_url: URL },
    },
  ])("adapts legacy $name to the project player", ({ fields }) => {
    renderView({
      resourceData: sharedTrace({}, { ...fields, transcript: TRANSCRIPT }),
    });

    expect(screen.getByTestId("single-track")).toHaveTextContent(URL);
    expect(captured.audio).toMatchObject({
      module: "project",
      recording_available: true,
    });
    expect(captured.transcript).toEqual(TRANSCRIPT);
    expect(
      screen.queryByText(/No recording for this call/),
    ).not.toBeInTheDocument();
  });

  it("preserves legacy stereo channel metadata", () => {
    const layout = { left: "customer", right: "assistant" };
    renderView({
      resourceData: sharedTrace(
        {},
        { recordings: { stereo: URL, stereo_channels: layout } },
      ),
    });

    expect(screen.getByTestId("multi-track")).toHaveTextContent(
      "blob:customer|blob:assistant",
    );
    expect(captured.stereoArgs).toEqual([URL, false, null, layout]);
  });

  it("keeps an already-shaped project recording playable", () => {
    renderView({
      resourceData: sharedTrace(
        {},
        { recording: { mono: { combined_url: URL } } },
      ),
    });

    expect(screen.getByTestId("single-track")).toHaveTextContent(URL);
    expect(captured.audio.recording_available).toBe(true);
  });

  it("decodes a top-level JSON transcript without losing its precedence", () => {
    renderView({
      resourceData: sharedTrace(
        {
          "fi.conversation.transcript": [
            { role: "assistant", content: "Fallback" },
          ],
        },
        { transcript: JSON.stringify(TRANSCRIPT) },
      ),
    });

    expect(captured.transcript).toEqual(TRANSCRIPT);
  });

  it.each(["not-json", "null"])(
    "ignores malformed or null raw_log %s",
    (rawLog) => {
      renderView({
        resourceData: sharedTrace({
          raw_log: rawLog,
          "fi.conversation.transcript": JSON.stringify(TRANSCRIPT),
          "conversation.recording.mono.combined": URL,
        }),
      });

      expect(captured.transcript).toEqual(TRANSCRIPT);
      expect(screen.getByTestId("single-track")).toHaveTextContent(URL);
    },
  );

  it("does not let empty or malformed transcripts hide a later valid array", () => {
    renderView({
      resourceData: sharedTrace(
        {
          raw_log: { transcript: TRANSCRIPT },
          "fi.conversation.transcript": "not-json",
        },
        { transcript: [] },
      ),
    });

    expect(captured.transcript).toEqual(TRANSCRIPT);
  });
});

describe("SharedVoiceView compatibility controls", () => {
  it("keeps the simulation voiceData bypass and player contract unchanged", () => {
    const voiceData = {
      id: "simulation-call",
      module: "simulate",
      status: "completed",
      recordings: { stereo: URL },
      transcript: TRANSCRIPT,
    };
    renderView({
      voiceData,
      resourceData: sharedTrace({ raw_log: "not-json" }),
    });

    expect(captured.audio).toBe(voiceData);
    expect(captured.transcript).toBe(TRANSCRIPT);
    expect(screen.getByTestId("multi-track")).toHaveTextContent(
      "blob:customer|blob:assistant",
    );
  });

  it("shows unavailable states for absent recordings and invalid transcript data", () => {
    renderView({
      resourceData: sharedTrace({
        raw_log: "not-json",
        "fi.conversation.transcript": "{}",
        "conversation.recording.mono.combined": "",
      }),
    });

    expect(screen.getByText(AUDIO_UNAVAILABLE)).toBeInTheDocument();
    expect(screen.getByText(TRANSCRIPT_UNAVAILABLE)).toBeInTheDocument();
    expect(captured.audio).toBeNull();
    expect(captured.transcript).toBeNull();
  });

  it("keeps the missing-resource warning", () => {
    renderView({});

    expect(
      screen.getByText("This shared voice call has no playable data."),
    ).toBeInTheDocument();
    expect(captured.audio).toBeNull();
  });
});
