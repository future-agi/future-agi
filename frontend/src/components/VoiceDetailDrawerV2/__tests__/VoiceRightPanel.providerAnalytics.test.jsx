import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import VoiceRightPanel from "../VoiceRightPanel";

vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: () => null,
}));
vi.mock("../VoiceLogsView", () => ({
  // eslint-disable-next-line react/prop-types
  default: function ProviderIdentity({ vapiId }) {
    return <div data-testid="provider-call-id">{vapiId || "unavailable"}</div>;
  },
}));

const rawLog = {
  id: "provider-call-1",
  summary: "Recorded provider summary",
  costBreakdown: { stt: 0.01, llm: 0.02, tts: 0.03 },
  artifact: {
    performanceMetrics: {
      endpointingLatencyAverage: 140,
      transcriberLatencyAverage: 260,
      modelLatencyAverage: 350,
      voiceLatencyAverage: 450,
    },
  },
};

const makeCall = (attributes, extra = {}) => ({
  id: "observe-call-1",
  module: "observe",
  status: "completed",
  provider: "vapi",
  transcript: [],
  observation_span: [
    {
      id: "child",
      parent_span_id: "root",
      span_attributes: { raw_log: { id: "wrong-child" } },
    },
    {
      id: "root",
      parent_span_id: null,
      observation_type: "conversation",
      span_attributes: attributes,
    },
  ],
  ...extra,
});

const renderCall = (data) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const ui = (call) => (
    <QueryClientProvider client={queryClient}>
      <VoiceRightPanel data={call} />
    </QueryClientProvider>
  );
  const result = render(ui(data));
  return { rerenderCall: (call) => result.rerender(ui(call)) };
};

describe("OBS-SW-64 provider analytics and identity", () => {
  it.each([
    ["canonical object", { raw_log: rawLog }],
    ["canonical JSON string", { raw_log: JSON.stringify(rawLog) }],
    ["legacy object", { rawLog }],
    ["legacy JSON string", { rawLog: JSON.stringify(rawLog) }],
    [
      "canonical before legacy",
      {
        raw_log: JSON.stringify(rawLog),
        rawLog: { ...rawLog, id: "wrong-legacy", summary: "Wrong summary" },
      },
    ],
    ["null canonical with legacy fallback", { raw_log: null, rawLog }],
  ])(
    "shows recorded breakdowns and provider ID from %s",
    async (_, attributes) => {
      const user = userEvent.setup();
      renderCall(makeCall(attributes));

      expect(screen.getByText("Cost breakdown")).toBeInTheDocument();
      expect(screen.getByText("$0.01")).toBeInTheDocument();
      expect(screen.getByText("$0.02")).toBeInTheDocument();
      expect(screen.getByText("$0.03")).toBeInTheDocument();
      expect(screen.getByText("Latency pipeline")).toBeInTheDocument();
      expect(screen.getAllByText("140ms").length).toBeGreaterThan(0);
      expect(screen.getAllByText("260ms").length).toBeGreaterThan(0);
      expect(screen.getByText(rawLog.summary)).toBeInTheDocument();
      await user.click(screen.getByRole("tab", { name: "Logs" }));
      expect(screen.getByTestId("provider-call-id")).toHaveTextContent(
        rawLog.id,
      );
    },
  );

  it.each(["{broken", "null", "[]", "42", '"text"', null])(
    "ignores malformed or non-object raw logs (%s)",
    (value) => {
      renderCall(makeCall({ raw_log: value }));
      expect(screen.queryByText("Cost breakdown")).not.toBeInTheDocument();
      expect(screen.queryByText("Latency pipeline")).not.toBeInTheDocument();
      expect(
        screen.getByText("No call analytics data available"),
      ).toBeInTheDocument();
    },
  );

  it("keeps customer metrics ahead of provider breakdowns", () => {
    renderCall(
      makeCall(
        { raw_log: JSON.stringify(rawLog) },
        {
          customer_latency_metrics: { system_metrics: { model: 777 } },
          customer_cost_breakdown: { stt: { cost: 0.04 } },
        },
      ),
    );
    expect(screen.getAllByText("777ms").length).toBeGreaterThan(0);
    expect(screen.queryByText("260ms")).not.toBeInTheDocument();
    expect(screen.getAllByText("$0.04").length).toBeGreaterThan(0);
    expect(screen.queryByText("$0.01")).not.toBeInTheDocument();
  });
});

describe("OBS-SW-66 analytics call-start wiring", () => {
  it.each(["observe", "simulate"])(
    "shows initial silence as TTFW in %s and refreshes a changed anchor",
    (module) => {
      const call = makeCall(
        {},
        {
          module,
          provider: "retell",
          started_at: "2026-11-09T12:00:00.000Z",
          transcript: [
            {
              speaker_role: "assistant",
              content: "Hello there",
              time: "2026-11-09T12:00:05.000Z",
              duration: 2,
            },
          ],
        },
      );
      const { rerenderCall } = renderCall(call);
      expect(screen.getByText("TTFW").parentElement).toHaveTextContent(
        "5000ms",
      );
      rerenderCall({ ...call, started_at: "2026-11-09T11:59:58.000Z" });
      expect(screen.getByText("TTFW").parentElement).toHaveTextContent(
        "7000ms",
      );
    },
  );
});
