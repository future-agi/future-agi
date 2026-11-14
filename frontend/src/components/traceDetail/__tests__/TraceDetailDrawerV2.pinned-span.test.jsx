import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

// TH-4804: an evaluation opens its trace pinned to the evaluated span. A
// display filter the user saved for this project in Observe (here: only `llm`
// spans) must not hide that span, and must not be rewritten.

const SAVED_KEY = "trace-view-default-project-1";
const SAVED = JSON.stringify({
  viewMode: "tree",
  spanTypeFilter: null,
  filters: [{ field: "nodeType", operator: "is", value: ["llm"] }],
});

const span = (id, type, children = []) => ({
  observation_span: {
    id,
    trace: "trace-1",
    name: `${type}-span`,
    observation_type: type,
    parent_span_id: id === "root-llm" ? null : "root-llm",
    start_time: "2026-11-13T10:00:00Z",
  },
  children,
});

const mocks = vi.hoisted(() => ({
  spanPaneProps: vi.fn(),
  treeProps: vi.fn(),
  storage: new Map(),
}));

vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/api/project/trace-detail", () => {
  // One response object, as React Query returns for unchanged data.
  let data;
  return {
    useGetTraceDetail: () => ({
      isLoading: false,
      data: (data ??= {
        trace: { id: "trace-1", tags: [] },
        observation_spans: [
          span("root-llm", "llm", [
            span("tool-1", "tool"),
            span("llm-2", "llm"),
          ]),
        ],
      }),
    }),
  };
});

vi.mock("../DrawerToolbar", () => ({ default: () => null }));
vi.mock("../DrawerHeader", () => ({ default: () => null }));
vi.mock("../TraceTreeV2", () => ({
  default: (props) => {
    mocks.treeProps(props);
    return null;
  },
}));
vi.mock("../SpanDetailPane", () => ({
  default: (props) => {
    mocks.spanPaneProps(props);
    return null;
  },
}));

import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const ids = (entries) =>
  (entries || []).flatMap((entry) => [
    entry.observation_span.id,
    ...ids(entry.children),
  ]);

const renderDrawer = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TraceDetailDrawerV2
        traceId="trace-1"
        open
        onClose={vi.fn()}
        projectId="project-1"
        initialSpanId="tool-1"
        {...props}
      />
    </QueryClientProvider>,
  );

describe("TraceDetailDrawerV2 pinned initial span", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    mocks.spanPaneProps.mockClear();
    mocks.treeProps.mockClear();
    mocks.storage = new Map([[SAVED_KEY, SAVED]]);
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key) => mocks.storage.get(key) ?? null),
      setItem: vi.fn((key, value) => mocks.storage.set(key, value)),
      removeItem: vi.fn((key) => mocks.storage.delete(key)),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows and selects the pinned tool span despite a saved llm-only filter", () => {
    renderDrawer({ keepInitialSpanVisible: true });

    const tree = mocks.treeProps.mock.lastCall[0];
    expect(ids(tree.spans)).toEqual(["root-llm", "tool-1", "llm-2"]);
    expect(tree.selectedSpanId).toBe("tool-1");
    const pane = mocks.spanPaneProps.mock.lastCall[0];
    expect(pane.entry.observation_span.id).toBe("tool-1");
    // The saved filter still applies to every other span, and is unchanged.
    expect(JSON.parse(mocks.storage.get(SAVED_KEY)).filters).toEqual(
      JSON.parse(SAVED).filters,
    );
  });

  it("keeps one spans identity when an unpinned drawer opens another span", () => {
    // Observe does not pin; a new spans array would reset the Agent Graph's
    // node positions although the visible tree is the same.
    const client = new QueryClient();
    const drawer = (initialSpanId) => (
      <QueryClientProvider client={client}>
        <TraceDetailDrawerV2
          traceId="trace-1"
          open
          onClose={vi.fn()}
          projectId="project-1"
          initialSpanId={initialSpanId}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(drawer("tool-1"));
    const before = mocks.treeProps.mock.lastCall[0].spans;

    rerender(drawer("llm-2"));

    expect(mocks.treeProps.mock.lastCall[0].spans).toBe(before);
  });

  it("keeps Observe's behaviour when the caller does not pin the span", () => {
    renderDrawer();

    const tree = mocks.treeProps.mock.lastCall[0];
    expect(ids(tree.spans)).toEqual(["root-llm", "llm-2"]);
    expect(mocks.spanPaneProps).not.toHaveBeenCalled();
  });
});
