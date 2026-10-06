/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: () => ({ callDetail: null, isLoading: false }),
  };
});

vi.mock("src/components/share-dialog", () => ({
  ShareDialog: ({ open, onClose, resourceType, resourceId }) => (
    <div
      data-testid="share-dialog"
      data-open={String(open)}
      data-resource-type={resourceType}
      data-resource-id={resourceId}
    >
      <button type="button" onClick={onClose}>
        close share
      </button>
    </div>
  ),
}));

const { default: ChatCallDrawer } = await import("../ChatCallDrawer");

describe("ChatCallDrawer share", () => {
  it("opens the share dialog for the call execution", async () => {
    const user = userEvent.setup();
    render(
      <ChatCallDrawer
        task={{ id: "call-execution-1", status: "completed" }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Share call" }));
    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-open", "true");
    expect(dialog).toHaveAttribute("data-resource-type", "call_execution");
    expect(dialog).toHaveAttribute("data-resource-id", "call-execution-1");

    await user.click(screen.getByRole("button", { name: "close share" }));
    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();
  });
});

describe("ChatCallDrawer tabs", () => {
  it("leaves sub-goal checks out of the Evals tab", () => {
    render(
      <ChatCallDrawer
        task={{
          id: "call-execution-2",
          status: "completed",
          evalResults: [
            { id: "ev-1", name: "Tone", kind: "evaluation", score: 1, passed: true },
            { id: "sg-1", name: "pin_verified", kind: "sub_goal", score: 1, passed: true },
          ],
        }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: "Evals (1)" })).toBeInTheDocument();
  });

  it("has no Checklist or Graph tab while they have no data behind them", () => {
    render(<ChatCallDrawer task={{ id: "call-execution-1", status: "completed" }} onClose={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Checklist" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Graph" })).not.toBeInTheDocument();
  });
});
