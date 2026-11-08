import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import TestDetailSideDrawer from "../TestDetailSideDrawer";
import CallAnalyticsView from "src/components/VoiceDetailDrawerV2/CallAnalyticsView";

const mocks = vi.hoisted(() => ({
  detail: {},
  store: {},
  module: "simulate",
  drawerProps: null,
}));
vi.mock("src/sections/test-detail/states", () => ({
  useTestDetailSideDrawerStoreShallow: (selector) => selector(mocks.store),
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => [
    { rowIndex: 0, module: mocks.module, origin: mocks.module },
    vi.fn(),
    vi.fn(),
  ],
}));
vi.mock("src/sections/agents/helper", () => ({
  useCallExecutionDetail: () => mocks.detail,
  useVoiceCallDetail: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("src/hooks/useTestRunDetails", () => ({
  default: () => ({ data: undefined }),
}));
vi.mock("src/components/VoiceDetailDrawerV2", () => {
  function VoiceDrawer({ data, isStale }) {
    mocks.drawerProps = { data, isStale };
    return (
      <>
        <div data-testid="retained-transcript">
          {data.transcript?.[0]?.content}
        </div>
        <CallAnalyticsView
          audioMetrics={data.audio_metrics}
          isStale={isStale}
        />
      </>
    );
  }
  VoiceDrawer.propTypes = { data: PropTypes.object, isStale: PropTypes.bool };
  return { default: VoiceDrawer };
});
vi.mock("src/components/traceDetailDrawer/AnnotationSidebarContent", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AddLabelDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/ChatDetailDrawerV2", () => ({ default: () => null }));

const metrics = (value) => ({
  schema_version: 1,
  state: "complete",
  metrics: {
    average_pitch_hz: { state: "available", unit: "Hz", value },
    estimated_snr_db: { state: "available", unit: "dB", value: 0 },
    voice_quality_index: { state: "available", unit: "index", value: 0 },
  },
});

beforeEach(() => {
  mocks.module = "simulate";
  mocks.detail = { data: undefined, isLoading: false, isError: false };
  mocks.drawerProps = null;
  mocks.store = {
    testDetailDrawerOpen: {
      id: "legacy-call",
      simulation_call_type: "voice",
      ignoreCache: true,
      transcript: [{ content: "Keep this transcript", duration: 4 }],
      // Deliberately untrusted list-derived metric: never render or export it.
      audio_metrics: metrics(999),
    },
    compareReplay: false,
    setTestDetailDrawerOpen: vi.fn(),
    setCompareReplay: vi.fn(),
  };
});

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  const ui = () => (
    <Wrapper>
      <TestDetailSideDrawer drawerQueryKey={["executions", "run-1"]} />
    </Wrapper>
  );
  const result = render(ui());
  return { ...result, refresh: () => result.rerender(ui()) };
}

describe("Legacy acoustic detail hydration", () => {
  it("shows a distinct initial detail-fetch error, retaining transcript but not list acoustic data", () => {
    mocks.detail.isError = true;
    setup();
    expect(
      screen.getByRole("region", { name: /Acoustic metrics/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load audio metrics",
    );
    expect(screen.getByText("Keep this transcript")).toBeInTheDocument();
    expect(screen.queryByText("999.0")).not.toBeInTheDocument();
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
    expect(mocks.drawerProps.data).not.toHaveProperty("audio_metrics");
  });

  it("shows initial loading separately from analysis pending, then accepts hydrated detail", () => {
    mocks.detail.isLoading = true;
    const { refresh } = setup();
    expect(
      screen.getByRole("progressbar", { name: "Loading audio metrics" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Processing audio metrics"),
    ).not.toBeInTheDocument();
    expect(mocks.drawerProps.data).not.toHaveProperty("audio_metrics");
    mocks.detail = {
      data: {
        id: "legacy-call",
        audio_metrics: metrics(180),
        transcript: [{ content: "Hydrated transcript" }],
      },
      isLoading: false,
      isError: false,
    };
    refresh();
    expect(screen.getByText("180.0")).toBeInTheDocument();
    expect(screen.getByText("0.00")).toBeInTheDocument();
    expect(screen.getByText("Hydrated transcript")).toBeInTheDocument();
    expect(mocks.drawerProps.data.transcript[0].duration).toBe(4);
    expect(mocks.drawerProps.data.audio_metrics).toEqual(metrics(180));
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("never fills a flag-off hydrated response from a list-row envelope", () => {
    mocks.detail.data = { id: "legacy-call" };
    setup();
    expect(
      screen.queryByRole("region", { name: /Acoustic metrics/ }),
    ).not.toBeInTheDocument();
    expect(mocks.drawerProps.data).not.toHaveProperty("audio_metrics");
  });

  it("does not trust list-row acoustic data in the Observe drawer either", () => {
    mocks.module = "project";
    setup();
    expect(
      screen.queryByRole("region", { name: /Acoustic metrics/ }),
    ).not.toBeInTheDocument();
    expect(mocks.drawerProps.data).not.toHaveProperty("audio_metrics");
  });

  it("keeps the last hydrated result and stale notice until refresh recovers", () => {
    mocks.detail = {
      data: { id: "legacy-call", audio_metrics: metrics(180) },
      isLoading: false,
      isError: false,
    };
    const { refresh } = setup();
    mocks.detail = { ...mocks.detail, isError: true };
    refresh();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Couldn't refresh audio metrics. Showing the last result.",
    );
    expect(screen.getByText("180.0")).toBeInTheDocument();
    expect(
      screen.queryByText("Couldn't load audio metrics"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Retrying/)).not.toBeInTheDocument();
    refresh();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    mocks.detail = { ...mocks.detail, isError: false };
    refresh();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
  });
});
