import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, userEvent } from "src/utils/test-utils";
import axios from "src/utils/axios";
import ContentPanel from "../annotate/content-panel";

const { mockUseGetTraceDetail } = vi.hoisted(() => ({
  mockUseGetTraceDetail: vi.fn(() => ({ data: null, isLoading: false })),
}));

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
    project: {
      traceSession: "/tracer/trace-session/",
    },
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
  default: ({ data, embedded, hiddenActionIds = [], hideAnnotationTab }) => (
    <div
      data-testid="voice-drawer"
      data-scenario={data?.scenario}
      data-embedded={String(embedded)}
      data-hidden-actions={hiddenActionIds.join(",")}
      data-hide-annotation={String(hideAnnotationTab)}
    />
  ),
}));

vi.mock("src/components/ChatDetailDrawerV2", () => ({
  default: ({ data, embedded, hideAnnotationTab }) => (
    <div
      data-testid="chat-drawer"
      data-scenario={data?.scenario}
      data-embedded={String(embedded)}
      data-hide-annotation={String(hideAnnotationTab)}
    />
  ),
}));

vi.mock("src/sections/projects/TracesDrawer/SessionHistory", () => ({
  default: ({ traceDetail = [], onTraceClick }) => (
    <div data-testid="session-history">
      {traceDetail.map((trace) => (
        <button
          type="button"
          key={trace.trace_id}
          data-testid={`session-trace-${trace.trace_id}`}
          onClick={() => onTraceClick?.(trace.trace_id)}
        >
          {trace.input}
        </button>
      ))}
    </div>
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
  default: ({ rightSlot }) => (
    <div data-testid="drawer-toolbar">{rightSlot}</div>
  ),
  // eslint-disable-next-line react/prop-types -- test stub
  ToolbarPill: ({ icon, label, onClick }) => (
    <button
      type="button"
      data-testid="toolbar-pill"
      data-icon={icon}
      onClick={onClick}
    >
      {label}
    </button>
  ),
}));

vi.mock("src/components/traceDetail/TraceDisplayPanel", () => ({
  default: () => <div data-testid="trace-display-panel" />,
  DEFAULT_VIEW_CONFIG: {
    viewMode: "tree",
    spanTypeFilter: null,
    visibleMetrics: {
      latency: true,
      tokens: true,
      cost: false,
      evals: false,
      annotations: false,
      events: false,
    },
    showAgentGraph: true,
  },
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mockUseGetTraceDetail(...args),
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

vi.mock("src/components/custom-dialog/confirm-dialog", () => ({
  default: () => null,
}));

function renderWithQuery(ui) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("Annotation queue ContentPanel", () => {
  const clipboardWriteText = vi.fn(() => Promise.resolve());

  beforeEach(() => {
    clipboardWriteText.mockClear();
    mockUseGetTraceDetail.mockReset();
    mockUseGetTraceDetail.mockReturnValue({ data: null, isLoading: false });
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboardWriteText },
      configurable: true,
    });
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
    renderWithQuery(
      <ContentPanel
        item={{
          source_type: "call_execution",
          source_content: { call_id: "call-1" },
        }}
      />,
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
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-hidden-actions",
      "queue,tags",
    );
    expect(screen.queryByTestId("new-scenario-view")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "View session" }),
    ).not.toBeInTheDocument();
  });

  it("renders ChatDetailDrawerV2 (embedded) for chat call execution queue items", async () => {
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

    renderWithQuery(
      <ContentPanel
        item={{
          source_type: "call_execution",
          source_content: { call_id: "chat-call-1" },
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("chat-drawer")).toBeInTheDocument();
    });
    expect(screen.getByTestId("chat-drawer")).toHaveAttribute(
      "data-embedded",
      "true",
    );
    expect(screen.getByTestId("chat-drawer")).toHaveAttribute(
      "data-hide-annotation",
      "true",
    );
    // Chat sims no longer use the voice drawer or the legacy ScenarioView path.
    expect(screen.queryByTestId("voice-drawer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-scenario-view")).not.toBeInTheDocument();
  });

  it("copies every dataset field including JSON objects and booleans", async () => {
    const user = userEvent.setup();

    render(
      <ContentPanel
        item={{
          source_type: "dataset_row",
          source_content: {
            fields: {
              approved: false,
              options: {
                expected: false,
                alternatives: ["passed", "failed"],
              },
            },
            field_types: {
              approved: "boolean",
              options: "json",
            },
          },
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy approved" }));
    await waitFor(async () => {
      await expect(navigator.clipboard.readText()).resolves.toBe("False");
    });

    await user.click(screen.getByRole("button", { name: "Copy options" }));
    await waitFor(async () => {
      await expect(navigator.clipboard.readText()).resolves.toBe(
        JSON.stringify(
          {
            expected: false,
            alternatives: ["passed", "failed"],
          },
          null,
          2,
        ),
      );
    });
    expect(
      screen.queryByRole("button", { name: "View session" }),
    ).not.toBeInTheDocument();
  });

  it("renders session traces without the legacy trace detail drawer", async () => {
    axios.get.mockResolvedValueOnce({
      data: {
        result: {
          session_metadata: { total_traces: 1 },
          response: [
            {
              trace_id: "trace-123",
              input: "customer asks for help",
              output: "assistant responds",
              system_metrics: {},
              evals_metrics: {},
            },
          ],
          next: null,
        },
      },
    });

    renderWithQuery(
      <ContentPanel
        item={{
          source_type: "trace_session",
          source_content: { session_id: "session-123" },
        }}
      />,
    );

    // Session traces render, and the legacy trace-detail-drawer is gone — the
    // per-card "View Trace" button now opens TraceDetailDrawerV2 instead.
    expect(
      await screen.findByText("customer asks for help"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("trace-detail-drawer")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "View session" }),
    ).not.toBeInTheDocument();
  });

  it("renders prototype run content without a View session action", () => {
    render(
      <ContentPanel
        item={{
          source_type: "prototype_run",
          source_content: {
            name: "My prototype",
            prompt: "hello",
            response: "world",
          },
        }}
      />,
    );

    expect(screen.getByText("Prototype")).toBeInTheDocument();
    expect(screen.getByText("My prototype")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "View session" }),
    ).not.toBeInTheDocument();
  });

  describe("View session for trace / span items", () => {
    it("shows View session when the loaded trace has a parent session", () => {
      mockUseGetTraceDetail.mockReturnValue({
        data: {
          trace: {
            project: "proj-1",
            session: "session-abc",
            tags: [],
          },
          observation_spans: [],
        },
        isLoading: false,
      });

      renderWithQuery(
        <ContentPanel
          item={{
            source_type: "trace",
            source_content: { trace_id: "trace-1" },
          }}
        />,
      );

      expect(
        screen.getByRole("button", { name: "View session" }),
      ).toBeInTheDocument();
      expect(screen.getByTestId("drawer-toolbar")).toBeInTheDocument();
    });

    it("hides View session when the loaded trace has no parent session", () => {
      mockUseGetTraceDetail.mockReturnValue({
        data: {
          trace: {
            project: "proj-1",
            session: null,
            tags: [],
          },
          observation_spans: [],
        },
        isLoading: false,
      });

      renderWithQuery(
        <ContentPanel
          item={{
            source_type: "observation_span",
            source_content: { trace_id: "trace-1", span_id: "span-1" },
          }}
        />,
      );

      expect(screen.getByTestId("drawer-toolbar")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "View session" }),
      ).not.toBeInTheDocument();
    });

    it("opens session detail when View session is clicked", async () => {
      const user = userEvent.setup();
      mockUseGetTraceDetail.mockReturnValue({
        data: {
          trace: {
            project: "proj-1",
            session: "session-abc",
            tags: [],
          },
          observation_spans: [],
        },
        isLoading: false,
      });
      axios.get.mockResolvedValueOnce({
        data: {
          result: {
            session_metadata: { total_traces: 1 },
            response: [
              {
                trace_id: "trace-in-session",
                input: "session conversation turn",
                output: "assistant reply",
                system_metrics: {},
                evals_metrics: {},
              },
            ],
            next: null,
          },
        },
      });

      renderWithQuery(
        <ContentPanel
          item={{
            source_type: "trace",
            source_content: { trace_id: "trace-1" },
          }}
        />,
      );

      await user.click(screen.getByRole("button", { name: "View session" }));

      expect(
        await screen.findByRole("button", { name: "Back to trace" }),
      ).toBeInTheDocument();
      expect(
        await screen.findByText("session conversation turn"),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "View session" }),
      ).not.toBeInTheDocument();
    });
  });
});
