import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

// TraceFullPage and TraceDetailDrawerV2 are real; the drawer's panels and API
// hooks are replaced so the test sees only what Esc does to the page.
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

import TraceFullPage from "../TraceFullPage";

const TRACE_PATH = "/dashboard/observe/project-1/trace/trace-1";

const CurrentPath = () => {
  const location = useLocation();
  return <div data-testid="current-path">{location.pathname}</div>;
};

const renderTraceFullPage = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <HelmetProvider>
        <MemoryRouter
          initialEntries={["/previous", TRACE_PATH]}
          initialIndex={1}
        >
          <CurrentPath />
          <Routes>
            <Route
              path="/dashboard/observe/:observeId/trace/:traceId"
              element={<TraceFullPage />}
            />
            <Route path="*" element={<div>Left the trace page</div>} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    </QueryClientProvider>,
  );

const currentPath = () => screen.getByTestId("current-path").textContent;

describe("full-page trace view Esc", () => {
  it("stays on the page when Esc is pressed", () => {
    renderTraceFullPage();
    expect(currentPath()).toBe(TRACE_PATH);
    expect(useGetTraceDetail.mock.calls.at(-1)?.[0]).toBe("trace-1");

    act(() => {
      fireEvent.keyDown(document.body, { key: "Escape" });
    });

    expect(currentPath()).toBe(TRACE_PATH);
    expect(screen.queryByText("Left the trace page")).not.toBeInTheDocument();
  });

  it("still leaves the page from its Close button", () => {
    renderTraceFullPage();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(currentPath()).not.toBe(TRACE_PATH);
    expect(screen.getByText("Left the trace page")).toBeInTheDocument();
  });
});
