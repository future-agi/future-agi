import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import VoiceCallDrawer from "src/sections/simulate/environments/workspace/runs/detail/VoiceCallDrawer";
import useVoiceAudioStore from "../voiceAudioStore";

const { query, players, createPlayer } = vi.hoisted(() => ({
  query: { current: {} },
  players: [],
  createPlayer: vi.fn(),
}));

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallExecutionV3Detail: () => query.current,
}));
vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/components/traceDetail/DrawerToolbar", () => ({
  default: () => null,
}));
vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: () => null,
}));
vi.mock("wavesurfer-multitrack", () => ({
  default: function MockMultiTrack(tracks) {
    createPlayer(tracks);
    let onCanPlay;
    const player = {
      currentTime: 0,
      playing: false,
      wavesurfers: tracks.map(() => ({
        on: (event, callback) => {
          if (event === "ready") callback();
        },
        getDuration: () => 60,
        getCurrentTime: () => player.currentTime,
      })),
      on: (event, callback) => {
        if (event === "canplay") onCanPlay = callback;
      },
      initAllAudios: () => {
        Promise.resolve().then(() => onCanPlay());
      },
      destroy: vi.fn(),
      zoom: vi.fn(),
      setTime: (time) => {
        player.currentTime = time;
      },
      getCurrentTime: () => player.currentTime,
      isPlaying: () => player.playing,
      play: () => {
        player.playing = true;
      },
      pause: () => {
        player.playing = false;
      },
    };
    players.push(player);
    return player;
  },
}));

const callData = () => ({
  id: "call-1",
  status: "completed",
  provider: "vapi",
  simulation_call_type: "voice",
  recordings: {
    assistant: "https://example.test/agent.wav",
    customer: "https://example.test/user.wav",
  },
  transcript: [
    {
      id: "turn-1",
      speaker_role: "user",
      content: "Hello there",
      start_time: 0,
    },
    {
      id: "turn-2",
      speaker_role: "assistant",
      content: "How can I help?",
      start_time: 5,
    },
  ],
  audio_metrics: {
    schema_version: 1,
    state: "pending",
    metrics: {
      average_pitch_hz: { state: "pending", unit: "Hz", value: null },
      estimated_snr_db: { state: "pending", unit: "dB", value: null },
      voice_quality_index: {
        state: "unavailable",
        reason: "not_validated",
        unit: "index",
        value: null,
      },
    },
  },
});

beforeEach(() => {
  createPlayer.mockClear();
  players.length = 0;
  useVoiceAudioStore.getState().reset();
  Element.prototype.scrollIntoView = vi.fn();
  query.current = { data: callData(), isPending: false, isError: false };
});

describe("Voice detail refresh preservation (R09 / AC10)", () => {
  it("retains playback, transcript search and cached acoustic values across updates and failed refetches", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const ui = () => (
      <QueryClientProvider client={client}>
        <VoiceCallDrawer task={{ id: "call-1" }} onClose={vi.fn()} />
      </QueryClientProvider>
    );
    const { rerender, unmount } = render(ui());
    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "play-pause" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "play-pause" }));
    act(() => useVoiceAudioStore.getState().seekTo(12));
    await user.type(screen.getByPlaceholderText("Search transcript"), "Hello");
    expect(players[0].playing).toBe(true);
    expect(players[0].currentTime).toBe(12);

    // A fresh JSON response recreates recordings and transcript objects too.
    const data = JSON.parse(JSON.stringify(query.current.data));
    data.audio_metrics.state = "partial";
    data.audio_metrics.metrics.average_pitch_hz = {
      state: "available",
      unit: "Hz",
      value: 182.4,
    };
    data.audio_metrics.metrics.estimated_snr_db = {
      state: "available",
      unit: "dB",
      value: 0,
    };
    query.current = { data, isPending: false, isError: false };
    rerender(ui());
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText("0.0")).toBeInTheDocument();
    expect(createPlayer).toHaveBeenCalledTimes(1);
    expect(players[0].destroy).not.toHaveBeenCalled();
    expect(players[0].playing).toBe(true);
    expect(players[0].currentTime).toBe(12);
    expect(screen.getByPlaceholderText("Search transcript")).toHaveValue(
      "Hello",
    );

    query.current = { ...query.current, isError: true };
    rerender(ui());
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("0.0")).toBeInTheDocument();
    expect(createPlayer).toHaveBeenCalledTimes(1);
    expect(players[0].destroy).not.toHaveBeenCalled();
    expect(useVoiceAudioStore.getState().currentTime).toBe(12);
    expect(screen.getByPlaceholderText("Search transcript")).toHaveValue(
      "Hello",
    );

    // Control: changing a recording URL must recreate the waveform.
    query.current = {
      ...query.current,
      isError: false,
      data: {
        ...data,
        recordings: {
          ...data.recordings,
          assistant: "https://example.test/new-agent.wav",
        },
      },
    };
    rerender(ui());
    expect(createPlayer).toHaveBeenCalledTimes(2);
    expect(players[0].destroy).toHaveBeenCalledTimes(1);
    unmount();
    client.clear();
  });

  it("still shows the initial error when there is no cached detail", () => {
    query.current = { data: undefined, isPending: false, isError: true };
    render(<VoiceCallDrawer task={{ id: "call-1" }} onClose={vi.fn()} />);
    expect(screen.getByText("Couldn’t load this call.")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: /Acoustic metrics/ }),
    ).not.toBeInTheDocument();
  });
});
