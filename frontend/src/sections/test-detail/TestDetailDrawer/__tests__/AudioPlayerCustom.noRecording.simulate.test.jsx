import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import AudioPlayerCustom from "../AudioPlayerCustom";

// A Simulate call with nothing recorded, opened in the same voice drawer as
// Observe calls. No timestamp: a call that ended under two minutes ago is
// still fetching its recording, which is a different state.
const unrecordedSimulateCall = (fields = {}) => ({
  id: "call-unrecorded",
  module: "simulate",
  status: "completed",
  ...fields,
});

describe("OBS-SW-129 Simulate call with no recording", () => {
  it("says there is no recording and gives the end reason on its own line", () => {
    render(
      <AudioPlayerCustom
        data={unrecordedSimulateCall({ ended_reason: "customer-ended-call" })}
      />,
    );

    expect(screen.getByText("No recording for this call")).toBeInTheDocument();
    expect(
      screen.getByText("Call ended: customer-ended-call"),
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
          data={unrecordedSimulateCall({ ended_reason: endedReason })}
        />,
      );

      expect(
        screen.getByText("No recording for this call"),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Call ended/)).not.toBeInTheDocument();
      expect(screen.queryByText(/-$/)).not.toBeInTheDocument();
      expect(screen.queryByText(/No recording found/)).not.toBeInTheDocument();
    },
  );
});
