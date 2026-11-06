import { describe, expect, it } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import VoiceLeftPanel from "../VoiceLeftPanel";

// A simulate call with an execution id: the case that shows Checklist and Graph.
const data = {
  module: "simulate",
  call_execution_id: "call-1",
  id: "call-1",
  status: "completed",
  transcript: [],
};

describe("VoiceLeftPanel path tabs", () => {
  it("shows Checklist and Graph for a simulate call by default", () => {
    render(<VoiceLeftPanel data={data} />);
    expect(screen.getByText("Checklist")).toBeInTheDocument();
    expect(screen.getByText("Graph")).toBeInTheDocument();
  });

  it("hides them when the host asks", () => {
    render(<VoiceLeftPanel data={data} hidePathTabs />);
    expect(screen.getByText("Transcript")).toBeInTheDocument();
    expect(screen.queryByText("Checklist")).not.toBeInTheDocument();
    expect(screen.queryByText("Graph")).not.toBeInTheDocument();
  });
});

describe("VoiceLeftPanel extra tabs", () => {
  it("adds a host's tab after Transcript and shows its content when picked", async () => {
    const user = userEvent.setup();
    render(
      <VoiceLeftPanel
        data={data}
        hidePathTabs
        extraTabs={[
          {
            label: "Traces",
            value: "traces",
            content: <div data-testid="traces-content" />,
          },
        ]}
      />,
    );

    expect(screen.getByText("Transcript")).toBeInTheDocument();
    expect(screen.queryByTestId("traces-content")).not.toBeInTheDocument();

    await user.click(screen.getByText("Traces"));

    expect(screen.getByTestId("traces-content")).toBeInTheDocument();
  });

  it("has no extra tab when the host passes none", () => {
    render(<VoiceLeftPanel data={data} hidePathTabs />);
    expect(screen.queryByText("Traces")).not.toBeInTheDocument();
  });
});

describe("VoiceLeftPanel when the selected tab goes away", () => {
  it("falls back to the transcript instead of an empty pane", async () => {
    const user = userEvent.setup();
    const traces = {
      label: "Traces",
      value: "traces",
      content: <div data-testid="traces-content" />,
    };
    const { rerender } = render(
      <VoiceLeftPanel data={data} hidePathTabs extraTabs={[traces]} />,
    );
    await user.click(screen.getByText("Traces"));
    expect(screen.getByTestId("traces-content")).toBeInTheDocument();

    rerender(<VoiceLeftPanel data={data} hidePathTabs extraTabs={[]} />);

    expect(screen.queryByText("Traces")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
