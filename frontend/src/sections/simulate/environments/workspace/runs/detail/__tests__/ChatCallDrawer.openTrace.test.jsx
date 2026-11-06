/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: () => ({ callDetail: null, isLoading: false }),
    useHasWorkers: () => true,
    useCallTraces: () => ({ projectId: "p1", traces: [], isError: false }),
  };
});

vi.mock("../CallWorkersPanel", async () => {
  const { useState } = await import("react");
  return {
    default: function WorkersStub({ onOpenTrace }) {
      // Stands for what the reader opened in the panel: expanded rows, the Raw events toggle.
      const [expanded, setExpanded] = useState(false);
      return (
        <>
          <button type="button" onClick={() => onOpenTrace("tr-1")}>
            open step trace
          </button>
          <button
            type="button"
            aria-pressed={expanded}
            onClick={() => setExpanded(true)}
          >
            expand row
          </button>
        </>
      );
    },
  };
});

vi.mock("../CallTracesPanel", () => ({
  default: ({ openTraceId }) => (
    <div data-testid="traces-panel" data-open-trace={String(openTraceId)} />
  ),
}));

const { default: ChatCallDrawer } = await import("../ChatCallDrawer");

describe("ChatCallDrawer Open in Traces", () => {
  it("switches to Traces with the step's trace, and forgets it once the user changes tab", async () => {
    const user = userEvent.setup();
    render(
      <ChatCallDrawer
        task={{ id: "call-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Participants" }));
    await user.click(screen.getByRole("button", { name: "open step trace" }));
    expect(screen.getByTestId("traces-panel")).toHaveAttribute(
      "data-open-trace",
      "tr-1",
    );

    await user.click(screen.getByRole("tab", { name: "Transcript" }));
    await user.click(screen.getByRole("tab", { name: "Traces" }));
    expect(screen.getByTestId("traces-panel")).toHaveAttribute(
      "data-open-trace",
      "null",
    );
  });

  it("keeps what was opened in Participants after a visit to another tab", async () => {
    const user = userEvent.setup();
    render(
      <ChatCallDrawer
        task={{ id: "call-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Participants" }));
    await user.click(screen.getByRole("button", { name: "expand row" }));

    await user.click(screen.getByRole("tab", { name: "Traces" }));
    expect(
      screen.queryByRole("button", { name: "expand row" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Participants" }));

    expect(screen.getByRole("button", { name: "expand row" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
