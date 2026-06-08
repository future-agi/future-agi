import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import WaitingForSignalPanel from "../components/WaitingForSignalPanel";

const baseProps = {
  signals: { observeProjects: 1, traces: 0 },
  stage: "waiting_for_first_trace",
};

describe("WaitingForSignalPanel send-a-test-trace action", () => {
  it("keeps the existing waiting copy and testid intact", () => {
    render(<WaitingForSignalPanel {...baseProps} onCheckAgain={vi.fn()} />);

    const panel = screen.getByTestId("waiting-for-signal-panel");
    expect(within(panel).getByText("Send one trace")).toBeVisible();
    expect(within(panel).getByText("Check again")).toBeVisible();
  });

  it("does not render the test-trace button when the capability is off", () => {
    render(
      <WaitingForSignalPanel
        {...baseProps}
        canSendTestTrace={false}
        onSendTestTrace={vi.fn()}
        onCheckAgain={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("send-test-trace-button")).toBeNull();
  });

  it("does not render the test-trace button without a handler", () => {
    render(
      <WaitingForSignalPanel
        {...baseProps}
        canSendTestTrace
        onCheckAgain={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("send-test-trace-button")).toBeNull();
  });

  it("renders a gated test-trace button next to Check again and calls the handler", async () => {
    const onSendTestTrace = vi.fn();
    const onCheckAgain = vi.fn();
    render(
      <WaitingForSignalPanel
        {...baseProps}
        canSendTestTrace
        onSendTestTrace={onSendTestTrace}
        onCheckAgain={onCheckAgain}
      />,
    );

    const button = screen.getByTestId("send-test-trace-button");
    expect(button).toBeVisible();
    expect(button).toHaveTextContent(/test trace/i);
    expect(screen.getByText("Check again")).toBeVisible();

    await userEvent.click(button);
    expect(onSendTestTrace).toHaveBeenCalledTimes(1);
    expect(onCheckAgain).not.toHaveBeenCalled();
  });

  it("renders the gated button in the no-journey-step fallback path", () => {
    render(
      <WaitingForSignalPanel
        {...baseProps}
        journeyPlan={{ steps: [] }}
        canSendTestTrace
        onSendTestTrace={vi.fn()}
        onCheckAgain={vi.fn()}
      />,
    );

    expect(screen.getByTestId("send-test-trace-button")).toBeVisible();
  });

  it("disables the button and shows progress copy while sending", () => {
    render(
      <WaitingForSignalPanel
        {...baseProps}
        canSendTestTrace
        isSendingTestTrace
        onSendTestTrace={vi.fn()}
        onCheckAgain={vi.fn()}
      />,
    );

    const button = screen.getByTestId("send-test-trace-button");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent(/sending test trace/i);
  });
});
