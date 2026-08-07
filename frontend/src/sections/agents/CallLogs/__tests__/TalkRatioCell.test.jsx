import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TalkRatioCell from "../TalkRatioCell";

// TalkRatioCell renders the backend-sourced integer split verbatim
// (user_talk_pct / bot_talk_pct) — no client-side rounding.
describe("TalkRatioCell", () => {
  it("renders the backend integer split as user:bot", () => {
    render(<TalkRatioCell data={{ user_talk_pct: 33, bot_talk_pct: 67 }} />);
    expect(screen.getByText("33:67")).toBeInTheDocument();
  });

  it("renders a dash when the split is absent", () => {
    render(<TalkRatioCell data={{ talk_ratio: 2.0 }} />);
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("uses backend fields directly and never recomputes from talk_ratio", () => {
    // talk_ratio here would imply ~67:33 if recomputed; the cell must ignore it
    // and render the backend-provided split instead.
    render(
      <TalkRatioCell
        data={{ talk_ratio: 2.0, user_talk_pct: 40, bot_talk_pct: 60 }}
      />,
    );
    expect(screen.getByText("40:60")).toBeInTheDocument();
    expect(screen.queryByText("33:67")).not.toBeInTheDocument();
  });
});
