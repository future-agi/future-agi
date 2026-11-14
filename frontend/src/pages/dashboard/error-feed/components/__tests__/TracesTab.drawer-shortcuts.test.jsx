import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";

// TracesTab and TraceDetailDrawerV2 are real; the drawer's panels, the grid
// and the API hooks are replaced so the test sees only who owns the keys.
const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { useGetTraceDetail } = vi.hoisted(() => ({
  useGetTraceDetail: vi.fn(() => ({ data: undefined, isLoading: false })),
}));

const traces = [
  { id: "trace-a", name: "first" },
  { id: "trace-b", name: "second" },
];

vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  const AgGridReact = forwardRef(function MockAgGridReact(
    { rowData, onRowClicked },
    _ref,
  ) {
    return (
      <div>
        {rowData.map((row) => (
          <button
            key={row.id}
            type="button"
            onClick={() => onRowClicked({ data: row })}
          >
            {`Row ${row.id}`}
          </button>
        ))}
      </div>
    );
  });
  return { AgGridReact };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedTraces: () => ({
    data: {
      traces,
      total: traces.length,
      aggregates: {
        total_traces: traces.length,
        avg_score: 0,
        avg_turns: 0,
        p50_latency: 0,
        p95_latency: 0,
      },
    },
    isLoading: false,
  }),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "prototype" } }),
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));

// TraceDetailDrawerV2 dependencies
vi.mock("src/api/project/trace-detail", () => ({ useGetTraceDetail }));
vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: undefined }),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
  getOwnViewNames: () => [],
  DEFAULT_VIEW_NAME: "Default",
  findOwnDefaultView: () => null,
}));
vi.mock("src/api/develop/prompt", () => ({
  useCreatePromptDraft: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/api/model/model", () => ({ modelCatalogQuery: vi.fn() }));
vi.mock("src/sections/develop-detail/RunPrompt/common", () => ({
  getOutputFormatFromCatalogType: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: (selector) => selector({ isSidebarOpen: false }),
}));
vi.mock("src/sections/falcon-ai/FalconAISidebar", () => {
  const FalconAISidebar = () => null;
  FalconAISidebar.SIDEBAR_WIDTH = 0;
  return { default: FalconAISidebar };
});
vi.mock("src/components/imagine/useImagineStore", () => {
  const useImagineStore = (selector) => selector?.({});
  useImagineStore.getState = () => ({ reset: vi.fn() });
  return { default: useImagineStore };
});
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("src/components/traceDetail/DrawerToolbar", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDisplayPanel", () => ({
  default: () => null,
  DEFAULT_VIEW_CONFIG: {
    spanTypeFilter: [],
    visibleMetrics: [],
    showAgentGraph: false,
  },
}));
vi.mock("src/components/traceDetail/SpanTreeTimeline", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/SpanDetailPane", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceLeftPanel", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/SaveViewDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/promptFromSpan", () => ({
  buildPromptConfigFromSpan: vi.fn(),
}));
vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({ default: () => null }),
);
vi.mock("src/components/traceDetailDrawer/addToDataset/add-dataset", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AnnotationSidebarContent", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AddLabelDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/voiceAnnotationSources", () => ({
  buildTraceAnnotationSources: () => [],
}));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: () => null,
}));
vi.mock("src/components/imagine/ImagineTab", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog/confirm-dialog", () => ({
  default: () => null,
}));

import TracesTab from "../TracesTab";

const renderTracesTab = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TracesTab error={{ cluster_id: "cluster-1", project_id: "project-1" }} />
    </QueryClientProvider>,
  );

// The drawer asks for a trace's detail only while it is open.
const openedTraceIds = () =>
  useGetTraceDetail.mock.calls.map(([traceId]) => traceId).filter(Boolean);
const lastRequestedTraceId = () => useGetTraceDetail.mock.calls.at(-1)?.[0];

describe("Error Feed traces tab drawer shortcuts", () => {
  beforeEach(() => {
    useGetTraceDetail.mockClear();
  });

  it("does not open a trace when J is pressed with the drawer closed", () => {
    renderTracesTab();
    expect(lastRequestedTraceId()).toBeNull();

    act(() => {
      fireEvent.keyDown(document.body, { key: "j" });
      fireEvent.keyDown(document.body, { key: "k" });
      fireEvent.keyDown(document.body, { key: "j", ctrlKey: true });
    });

    expect(openedTraceIds()).toEqual([]);
    expect(lastRequestedTraceId()).toBeNull();
  });

  it("navigates with J while open and closes with Esc", () => {
    renderTracesTab();
    fireEvent.click(screen.getByRole("button", { name: "Row trace-a" }));
    expect(lastRequestedTraceId()).toBe("trace-a");

    act(() => {
      fireEvent.keyDown(document.body, { key: "j" });
    });
    expect(lastRequestedTraceId()).toBe("trace-b");

    act(() => {
      fireEvent.keyDown(document.body, { key: "Escape" });
    });
    expect(lastRequestedTraceId()).toBeNull();

    // Closed again: J must not reopen the first trace.
    useGetTraceDetail.mockClear();
    act(() => {
      fireEvent.keyDown(document.body, { key: "j" });
    });
    expect(openedTraceIds()).toEqual([]);
  });
});
