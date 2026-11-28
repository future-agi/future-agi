import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
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

// Stands for a panel the reader opened something in.
function Expandable() {
  const [open, setOpen] = useState(false);
  return (
    <button type="button" aria-pressed={open} onClick={() => setOpen(true)}>
      expand row
    </button>
  );
}

describe("VoiceLeftPanel extra tabs", () => {
  it("keeps a tab that asks for it mounted, hidden, while another tab shows", async () => {
    const user = userEvent.setup();
    render(
      <VoiceLeftPanel
        data={data}
        hidePathTabs
        extraTabs={[
          {
            label: "Participants",
            value: "workers",
            keepMounted: true,
            content: <Expandable />,
          },
        ]}
      />,
    );
    expect(screen.queryByRole("button", { name: "expand row" })).toBeNull();

    await user.click(screen.getByText("Participants"));
    await user.click(screen.getByRole("button", { name: "expand row" }));
    await user.click(screen.getByText("Transcript"));
    expect(screen.queryByRole("button", { name: "expand row" })).toBeNull();
    await user.click(screen.getByText("Participants"));

    expect(screen.getByRole("button", { name: "expand row" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

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

  it("follows a tab its host holds, and asks the host to change it", async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    render(
      <VoiceLeftPanel
        data={data}
        hidePathTabs
        tab="traces"
        onTabChange={onTabChange}
        extraTabs={[
          {
            label: "Traces",
            value: "traces",
            content: <div data-testid="traces-content" />,
          },
        ]}
      />,
    );

    expect(screen.getByTestId("traces-content")).toBeInTheDocument();
    await user.click(screen.getByText("Transcript"));
    expect(onTabChange).toHaveBeenCalledWith("transcript");
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

// A traced call recorded as an attachment, with no transcript: today's
// LiveKit and Pipecat shape.
const tracedCall = {
  module: "project",
  id: "trace-1",
  trace_id: "trace-1",
  status: "completed",
  transcript: [],
  recording_attachment: null,
};
const toolRow = {
  key: "tool-1",
  spanId: "tool-1",
  name: "lookup_order",
  mcp: false,
  failed: false,
  startMs: 3000,
  endMs: 4000,
  interleaved: false,
};

describe("VoiceLeftPanel background activity", () => {
  it("shows what ran under the recording when there is no transcript", async () => {
    const user = userEvent.setup();
    const onSelectActivity = vi.fn();
    render(
      <VoiceLeftPanel
        data={tracedCall}
        activity={[toolRow]}
        onSelectActivity={onSelectActivity}
      />,
    );
    const group = screen.getByRole("region", { name: "Background activity" });
    expect(group).toHaveTextContent(
      "Background activity · time from call startTool calllookup_order0:03.0–0:04.0",
    );
    expect(screen.getByText("Recording")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "View span lookup_order, tool call, from 0:03.0 to 0:04.0 after call start",
      }),
    );
    expect(onSelectActivity).toHaveBeenCalledExactlyOnceWith("tool-1");
  });

  it("is unchanged without activity", () => {
    render(<VoiceLeftPanel data={tracedCall} />);
    expect(
      screen.queryByRole("region", { name: "Background activity" }),
    ).toBeNull();
    expect(screen.queryByText("Recording")).toBeNull();
  });
});
