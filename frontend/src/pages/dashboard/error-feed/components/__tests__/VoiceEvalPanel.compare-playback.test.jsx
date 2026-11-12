import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import VoiceEvalPanel from "../VoiceEvalPanel";

// W10-ERROR-FEED-L07: opening "Compare with passing" while the failing call
// plays must stop the single-call player, so the working call never plays
// over a recording the user can no longer see or pause.

vi.mock("src/sections/agents/helper", async (importOriginal) => ({
  ...(await importOriginal()),
  useVoiceCallDetail: vi.fn(),
}));

const FAILING_CALL_ID = "synthetic-failing-call";
const WORKING_CALL_ID = "synthetic-working-call";
const FAILING_URL = "https://example.test/synthetic-failing-call.wav";
const WORKING_URL = "https://example.test/synthetic-working-call.wav";
const RECORDINGS = {
  [FAILING_CALL_ID]: FAILING_URL,
  [WORKING_CALL_ID]: WORKING_URL,
};

// jsdom does not implement media playback, so every player gets a fake
// Audio that records whether it is playing.
let audios = [];
class FakeAudio {
  constructor(src) {
    this.src = src;
    this.paused = true;
    this.currentTime = 0;
    this.duration = Number.NaN;
    audios.push(this);
  }

  addEventListener() {}

  removeEventListener() {}

  play() {
    this.paused = false;
    return Promise.resolve();
  }

  pause() {
    this.paused = true;
  }
}

const playingSources = () => audios.filter((a) => !a.paused).map((a) => a.src);

// The play/pause controls are icon-only buttons; every other button in the
// panel has a text label.
const playButtons = () =>
  screen.getAllByRole("button").filter((b) => !b.textContent.trim());

beforeEach(() => {
  audios = [];
  vi.stubGlobal("Audio", FakeAudio);
  useVoiceCallDetail.mockImplementation((traceId) => ({
    data: traceId
      ? { recording_url: RECORDINGS[traceId], duration_seconds: 30 }
      : undefined,
    isLoading: false,
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Voice call comparison playback", () => {
  it("stops the single-call player when the comparison opens", async () => {
    const user = userEvent.setup();
    render(
      <VoiceEvalPanel
        trace={{ id: FAILING_CALL_ID }}
        successTraceId={WORKING_CALL_ID}
        projectId="synthetic-voice-project"
      />,
    );

    const [singlePlay] = playButtons();
    await user.click(singlePlay);
    expect(playingSources()).toEqual([FAILING_URL]);

    await user.click(
      screen.getByRole("button", { name: "Compare with passing" }),
    );
    expect(playingSources()).toEqual([]);

    // Split view: failing column first, working column second.
    const [, workingPlay] = playButtons();
    await user.click(workingPlay);
    expect(playingSources()).toEqual([WORKING_URL]);

    await user.click(screen.getByRole("button", { name: "Single view" }));
    expect(playingSources()).toEqual([]);
  });
});
