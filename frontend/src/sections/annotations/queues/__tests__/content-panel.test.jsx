import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import ContentPanel from "../annotate/content-panel";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() =>
      Promise.resolve({
        data: {
          status: "completed",
          simulation_call_type: "voice",
          scenario: "Greet the customer",
          scenario_columns: {
            persona: { column_name: "persona", value: "Impatient customer" },
          },
          transcripts: [],
          eval_outputs: {},
        },
      }),
    ),
  },
  endpoints: {
    testExecutions: {
      callDetail: (id) => `/simulate/call-executions/${id}/`,
    },
  },
}));

vi.mock("src/components/VoiceDetailDrawerV2/ScenarioView", () => ({
  default: ({ data }) => (
    <div data-testid="new-scenario-view">{data.scenario}</div>
  ),
}));

vi.mock("src/components/VoiceDetailDrawerV2", () => ({
  default: ({ data, embedded, hideAnnotationTab }) => (
    <div
      data-testid="voice-drawer"
      data-scenario={data?.scenario}
      data-embedded={String(embedded)}
      data-hide-annotation={String(hideAnnotationTab)}
    />
  ),
}));

vi.mock("src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom", () => ({
  default: () => <div data-testid="audio-player" />,
}));

vi.mock("src/components/CallLogsDetailDrawer/LeftSection", () => ({
  default: () => <div data-testid="left-section" />,
}));

vi.mock(
  "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerRightSection",
  () => ({
    default: () => <div data-testid="right-section" />,
  }),
);

vi.mock("src/components/CallLogsDetailDrawer/RightSection", () => ({
  default: () => <div data-testid="call-right-section" />,
}));

vi.mock("src/components/traceDetailDrawer/trace-detail-drawer", () => ({
  default: () => <div data-testid="trace-detail-drawer" />,
}));

vi.mock("src/components/traceDetail/SpanTreeTimeline", () => ({
  default: () => <div data-testid="span-tree" />,
}));

vi.mock("src/components/traceDetail/SpanDetailPane", () => ({
  default: () => <div data-testid="span-detail" />,
}));

vi.mock("src/components/traceDetail/TraceLeftPanel", () => ({
  default: () => <div data-testid="trace-left-panel" />,
}));

vi.mock("src/components/traceDetail/DrawerToolbar", () => ({
  default: () => <div data-testid="drawer-toolbar" />,
}));

vi.mock("src/components/traceDetail/TraceDisplayPanel", () => ({
  default: () => <div data-testid="trace-display-panel" />,
  DEFAULT_VIEW_CONFIG: {},
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null, isLoading: false }),
}));

vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/components/imagine/ImagineTab", () => ({
  default: () => <div data-testid="imagine-tab" />,
}));

vi.mock("src/components/imagine/useImagineStore", () => ({
  default: {
    getState: () => ({
      reset: vi.fn(),
    }),
  },
}));

describe("Annotation queue ContentPanel", () => {
  beforeEach(() => {
    axios.get.mockResolvedValue({
      data: {
        status: "completed",
        simulation_call_type: "voice",
        scenario: "Greet the customer",
        scenario_columns: {
          persona: { column_name: "persona", value: "Impatient customer" },
        },
        transcript: [],
        eval_outputs: {},
      },
    });
  });

  it("uses the voice drawer for voice call execution queue items", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <ContentPanel
          item={{
            source_type: "call_execution",
            source_content: { call_id: "call-1" },
          }}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
        "data-scenario",
        "Greet the customer",
      );
    });
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-embedded",
      "true",
    );
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-hide-annotation",
      "true",
    );
    expect(screen.queryByTestId("new-scenario-view")).not.toBeInTheDocument();
  });

  it("keeps chat call execution queue items on the chat detail layout", async () => {
    axios.get.mockResolvedValueOnce({
      data: {
        status: "completed",
        simulation_call_type: "text",
        scenario: "Answer the customer",
        scenario_columns: {},
        transcript: [],
        eval_outputs: {},
      },
    });

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <ContentPanel
          item={{
            source_type: "call_execution",
            source_content: { call_id: "chat-call-1" },
          }}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("new-scenario-view")).toHaveTextContent(
        "Answer the customer",
      );
    });
    expect(screen.queryByTestId("voice-drawer")).not.toBeInTheDocument();
  });
});
