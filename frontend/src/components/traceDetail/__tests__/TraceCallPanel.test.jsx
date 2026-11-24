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
const renderPanel = (props) =>
  render(<TraceCallPanel traceId="trace-1" projectId={projectId} {...props} />);

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

  it("adds the call's tool spans as activity that selects the span on the right", () => {
    const T0 = Date.parse("2026-11-24T10:00:00Z");
    const at = (ms) => new Date(T0 + ms).toISOString();
    const transcript = [
      { role: "user", content: "Where is it?", timestamp: at(500) },
      { role: "assistant", content: "Checking.", timestamp: at(2500) },
      { role: "assistant", content: "Shipped.", timestamp: at(4500) },
    ];
    const spans = [
      {
        observation_span: {
          id: "root-1",
          start_time: at(0),
          observation_type: "conversation",
        },
        children: [
          {
            observation_span: {
              id: "tool-1",
              name: "lookup_order",
              observation_type: "tool",
              start_time: at(3000),
              end_time: at(4000),
              input: "private",
            },
            children: [],
          },
        ],
      },
    ];
    const detail = {
      trace_id: "trace-1",
      transcript,
      observation_span: [{ id: "root-1" }],
    };
    mocks.detail.mockReturnValue({ data: detail, isFetching: false });
    const onSelectSpan = vi.fn();
    renderPanel({ spans, onSelectSpan });

    const props = mocks.panelProps.mock.lastCall[0];
    expect(props.onSelectActivity).toBe(onSelectSpan);
    expect(props.activity).toEqual([
      expect.objectContaining({
        spanId: "tool-1",
        name: "lookup_order",
        startMs: 3000,
        endMs: 4000,
        interleaved: true,
        anchorTurnId: "call-turn-2",
      }),
    ]);
    expect(props.data.transcript.map((t) => t.id)).toEqual([
      "call-turn-0",
      "call-turn-1",
      "call-turn-2",
    ]);
    // The fetched call detail itself is left as it was.
    expect(transcript.every((t) => !("id" in t))).toBe(true);
    expect(JSON.stringify(props.activity)).not.toContain("private");
  });

  it("keeps the transcript's identity when only the tree refreshes", () => {
    const detail = {
      trace_id: "trace-1",
      transcript: [{ role: "user", content: "Hi" }],
      observation_span: [{ id: "root-1" }],
    };
    mocks.detail.mockReturnValue({ data: detail, isFetching: false });
    const tree = () => [
      {
        observation_span: {
          id: "root-1",
          start_time: "2026-11-24T10:00:00Z",
        },
        children: [],
      },
    ];
    const { rerender } = renderPanel({ spans: tree() });
    const first = mocks.panelProps.mock.lastCall[0].data.transcript;
    rerender(
      <TraceCallPanel traceId="trace-1" projectId={projectId} spans={tree()} />,
    );
    expect(mocks.panelProps.mock.lastCall[0].data.transcript).toBe(first);
  });

  it("shows no activity without the drawer's tree", () => {
    mocks.detail.mockReturnValue({
      data: {
        trace_id: "trace-1",
        transcript: [],
        observation_span: [{ id: "root-1" }],
      },
      isFetching: false,
    });
    renderPanel();
    expect(mocks.panelProps.mock.lastCall[0].activity).toEqual([]);
  });

  it("says so when the call cannot be loaded", () => {
    mocks.detail.mockReturnValue({ error: new Error("x"), isFetching: false });
    renderPanel();

    expect(screen.getByText("Failed to load call")).toBeInTheDocument();
    expect(screen.queryByTestId("voice-left-panel")).toBeNull();
  });
});
