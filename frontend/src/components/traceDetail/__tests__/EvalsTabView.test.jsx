import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import EvalsTabView from "../EvalsTabView";

const timedOut = {
  id: "e1",
  eval_name: "Resolution",
  score: null,
  status: "timed_out",
  explanation: "Scoring timed out: no progress for 10 minutes.",
  spanId: "span-1",
};

// The Score header and the row's score cell share one width.
const scoreHeader = () => screen.getByText("Score");
const widthOf = (el) => getComputedStyle(el).width;

describe("EvalsTabView — the View span column", () => {
  it("shows View span in a trace span drawer, which can jump to the span", () => {
    const onSelectSpan = vi.fn();
    render(<EvalsTabView evals={[timedOut]} onSelectSpan={onSelectSpan} />);

    fireEvent.click(screen.getByText("View span"));
    expect(onSelectSpan).toHaveBeenCalledWith("span-1");
    expect(screen.getAllByTestId("eval-actions-column")).toHaveLength(2);
    expect(widthOf(scoreHeader())).toBe("15%");
  });

  it("drops the empty column and widens Score in a drawer with no span jump", () => {
    render(<EvalsTabView evals={[timedOut]} showSpanColumn={false} />);

    expect(screen.queryByText("View span")).toBeNull();
    expect(screen.queryByTestId("eval-actions-column")).toBeNull();
    expect(widthOf(scoreHeader())).toBe("40%");
    expect(widthOf(screen.getByTestId("eval-score-cell"))).toBe("40%");
  });

  it("shows a timed-out eval's state and expands to its reason", () => {
    render(<EvalsTabView evals={[timedOut]} showSpanColumn={false} />);

    expect(screen.getByText("Timed out")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Resolution"));
    expect(
      screen.getByText("Scoring timed out: no progress for 10 minutes."),
    ).toBeInTheDocument();
  });
});
