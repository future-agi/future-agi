import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import AudioPlayerCustom from "../AudioPlayerCustom";

// An Observe call with nothing recorded, e.g. an unanswered Vapi call.
const unrecordedCall = (fields = {}) => ({
  id: "call-unanswered",
  module: "project",
  status: "completed",
  recording_available: false,
  ...fields,
});

describe("OBS-SW-112 Observe call with no recording", () => {
  it("says there is no recording and gives the end reason on its own line", () => {
    render(
      <AudioPlayerCustom
        data={unrecordedCall({ ended_reason: "customer-did-not-answer" })}
      />,
    );

    expect(screen.getByText("No recording for this call")).toBeInTheDocument();
    expect(
      screen.getByText("Call ended: customer-did-not-answer"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No recording found/)).not.toBeInTheDocument();
  });

  it.each([
    ["missing", undefined],
    ["empty", ""],
    ["null", null],
  ])(
    "shows no end reason line and no trailing dash when the reason is %s",
    (_, endedReason) => {
      render(
        <AudioPlayerCustom
          data={unrecordedCall({ ended_reason: endedReason })}
        />,
      );

      expect(
        screen.getByText("No recording for this call"),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Call ended/)).not.toBeInTheDocument();
      expect(screen.queryByText(/-$/)).not.toBeInTheDocument();
    },
  );
});
