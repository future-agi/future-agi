import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, userEvent } from "src/utils/test-utils";
import VoiceLeftPanel from "../VoiceLeftPanel";
import useVoiceAudioStore from "../voiceAudioStore";

vi.mock("../VoiceAudioBridge", () => ({ default: () => null }));
vi.mock("../PathAnalysisView", () => ({ default: () => null }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/sections/common/CellMarkdown", () => ({
  // eslint-disable-next-line react/prop-types
  default: function MarkdownText({ text }) {
    return <div>{text}</div>;
  },
}));

const data = {
  id: "anchored-call",
  module: "observe",
  status: "completed",
  started_at: "2026-11-09T12:00:00.000Z",
  transcript: [
    {
      speaker_role: "assistant",
      content: "Hello there",
      time: "2026-11-09T12:00:05.000Z",
      duration: 2,
    },
    {
      speaker_role: "user",
      content: "A later reply",
      time: "2026-11-09T12:00:20.000Z",
      duration: 3,
    },
  ],
};

describe("OBS-SW-66 transcript playback call-start wiring", () => {
  const originalScrollIntoView = Element.prototype.scrollIntoView;

  beforeEach(() => {
    useVoiceAudioStore.getState().reset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
    useVoiceAudioStore.getState().reset();
    if (originalScrollIntoView) {
      Element.prototype.scrollIntoView = originalScrollIntoView;
    } else {
      delete Element.prototype.scrollIntoView;
    }
  });

  it("shows recording offsets and seeks to the later utterance at 20 seconds", async () => {
    const setTime = vi.fn();
    useVoiceAudioStore.getState().setInstance({
      multitrack: { setTime },
      wavesurfers: [],
    });
    const user = userEvent.setup();
    render(<VoiceLeftPanel data={data} />);

    expect(screen.getByText("0:05")).toBeInTheDocument();
    expect(screen.getByText("0:20")).toBeInTheDocument();
    await user.click(screen.getByText("A later reply"));
    expect(setTime).toHaveBeenLastCalledWith(20);
  });

  it("recomputes offsets when only started_at changes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<VoiceLeftPanel data={data} />);
    rerender(
      <VoiceLeftPanel
        data={{ ...data, started_at: "2026-11-09T11:59:58.000Z" }}
      />,
    );

    expect(screen.getByText("0:07")).toBeInTheDocument();
    expect(screen.getByText("0:22")).toBeInTheDocument();
    await user.click(screen.getByText("A later reply"));
    expect(useVoiceAudioStore.getState().pendingSeek).toBe(22);
  });
});
