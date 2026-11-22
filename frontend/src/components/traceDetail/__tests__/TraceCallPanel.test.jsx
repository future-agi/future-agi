import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  detail: vi.fn(),
  panelProps: vi.fn(),
}));

vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: (...args) => mocks.detail(...args),
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceLeftPanel", () => ({
  default: (props) => {
    mocks.panelProps(props);
    return <div data-testid="voice-left-panel" />;
  },
}));

import TraceCallPanel from "../TraceCallPanel";

const projectId = "10000000-0000-0000-0000-000000000001";
const renderPanel = () =>
  render(<TraceCallPanel traceId="trace-1" projectId={projectId} />);

describe("TraceCallPanel", () => {
  beforeEach(() => {
    mocks.detail.mockReset();
    mocks.panelProps.mockClear();
  });

  it("reads the Voice Observability call detail for the trace's project", () => {
    mocks.detail.mockReturnValue({ data: undefined, isFetching: true });
    renderPanel();

    expect(mocks.detail).toHaveBeenLastCalledWith("trace-1", {
      enabled: true,
      projectId,
    });
    expect(screen.queryByTestId("voice-left-panel")).toBeNull();
  });

  it("renders the Voice Observability left panel with the call", () => {
    const projection = { state: "rejected" };
    mocks.detail.mockReturnValue({
      data: {
        id: "trace-1",
        trace_id: "trace-1",
        transcript: [{ role: "user", content: "Hi" }],
        recording_attachment: projection,
      },
      isFetching: false,
    });
    renderPanel();

    expect(screen.getByTestId("voice-left-panel")).toBeInTheDocument();
    const { data } = mocks.panelProps.mock.lastCall[0];
    expect(data).toMatchObject({
      trace_id: "trace-1",
      project_id: projectId,
      module: "project",
      transcript: [{ role: "user", content: "Hi" }],
    });
    expect(data.recording_attachment).toBe(projection);
    // Round play with the peaks waveform, seek bar without peaks.
    expect(mocks.panelProps.mock.lastCall[0].recordingVariant).toBe("trace");
  });

  it("never falls back to legacy recording URLs without an attachment projection", () => {
    mocks.detail.mockReturnValue({
      data: { trace_id: "trace-1", recording_url: "https://legacy.example/a" },
      isFetching: false,
    });
    renderPanel();

    const { data } = mocks.panelProps.mock.lastCall[0];
    expect(Object.hasOwn(data, "recording_attachment")).toBe(true);
    expect(data.recording_attachment).toBeNull();
  });

  it("says so when the call cannot be loaded", () => {
    mocks.detail.mockReturnValue({ error: new Error("x"), isFetching: false });
    renderPanel();

    expect(screen.getByText("Failed to load call")).toBeInTheDocument();
    expect(screen.queryByTestId("voice-left-panel")).toBeNull();
  });
});
