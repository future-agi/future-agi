import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  buildApiFilterFromPanelRow,
  FILTER_STRING_MAX_UTF8_BYTES,
  TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES,
} from "src/api/contracts/filter-contract";
import { FILTER_VALUE_PAGE_SIZE } from "src/config/runtime_limits";
import axios, { endpoints } from "src/utils/axios";
import TraceFilterPanel, {
  buildManualAttributeProperty,
  buildQueryPropertyEntries,
  buildTraceFilterProperties,
  filterPropertiesForPicker,
  findTraceFilterProperty,
  getTraceFilterFields,
  mergeCatalogSearchProperties,
  mergeTraceFilterProperties,
  mergeRetainedAttributeProperties,
  normalizeFilterRowOperator,
  PropertyPickerPaginationControl,
  shouldUseRetainedAttributePages,
  supplementCatalogSearchCategoryCounts,
  toStaticFilterProperty,
} from "../TraceFilterPanel";
import {
  getPickerOptionSearchText,
  getPickerOptionSecondaryLabel,
  normalizePickerValues,
} from "../filterValuePickerUtils";

const parseQueryMock = vi.fn();
const dashboardFilterValuesMock = vi.hoisted(() => vi.fn());
const exactAttributePropertiesMock = vi.hoisted(() => vi.fn());
const propertyCatalogMock = vi.hoisted(() => vi.fn());
let intersectionObservers = [];

const triggerPropertyPageIntersection = () => {
  const sentinel = document.querySelector(
    "[data-filter-property-page-sentinel]",
  );
  expect(sentinel).toBeInTheDocument();
  const observer = [...intersectionObservers]
    .reverse()
    .find((candidate) => candidate.targets.has(sentinel));
  expect(observer).toBeDefined();
  fireEvent.wheel(observer.root || sentinel.parentElement, { deltaY: 1 });
  act(() => {
    observer.callback([{ isIntersecting: true, target: sentinel }]);
  });
};

const triggerValuePageIntersection = () => {
  const sentinel = screen.getByTestId("filter-value-pagination-sentinel");
  const observer = [...intersectionObservers]
    .reverse()
    .find((candidate) => candidate.targets.has(sentinel));
  expect(observer).toBeDefined();
  fireEvent.wheel(observer.root || sentinel.parentElement, { deltaY: 1 });
  act(() => {
    observer.callback([{ isIntersecting: true, target: sentinel }]);
  });
};

const defaultDashboardFilterValues = () => ({
  data: [],
  isLoading: false,
  isError: false,
  queryReadState: "complete",
  fetchNextPage: vi.fn(),
  hasNextPage: false,
  isFetchingNextPage: false,
  isFetchNextPageError: false,
  continuationKey: null,
  refetch: vi.fn(),
});

const defaultExactAttributeProperties = () => ({
  data: [],
  isFetching: false,
  fetchNextPage: vi.fn(),
  hasNextPage: false,
  isFetchingNextPage: false,
  fetchNextExactPage: vi.fn(),
  hasNextExactPage: false,
  isFetchingExactSearch: false,
  isFetchingNextExactPage: false,
  isFetchNextPageError: false,
  queryReadState: "complete",
  browseStatus: "exhausted",
  totalCount: 0,
  pageCount: 1,
  exactSearchMatched: false,
  cursorRetryExhausted: false,
  continuationKey: null,
  debouncedSearch: "",
  refetch: vi.fn(),
});

const settledPropertyCatalog = ({ metrics = [], categoryCounts } = {}) => ({
  metrics,
  categoryCounts: categoryCounts || {
    all: metrics.length,
    system_metric: 0,
    eval_metric: 0,
    annotation_metric: 0,
    custom_attribute: 0,
    custom_column: 0,
  },
  categoryCountsExact: true,
  legacyFallbackRequired: false,
  error: null,
  isLoading: false,
  isFetching: false,
  isRemoteCatalogSearchPending: false,
  isRemoteCatalogNextPagePending: false,
  isError: false,
  isSuccess: true,
  hasNextPage: false,
  isFetchingNextPage: false,
  isFetchNextPageError: false,
  cursorChainStopped: false,
  fetchNextPage: vi.fn(),
  data: { pages: [] },
});

const GLOBAL_CATALOG_SEARCH_CASES = [
  {
    search: "Tokens",
    source: "voice_calls",
    tab: "voiceCalls",
    initialMetrics: [],
    optionId: "gen_ai.usage.total_tokens",
    categoryName: "System",
    categoryCountLabel: "System property count",
  },
  {
    search: "Annotator",
    source: "traces",
    tab: "trace",
    initialMetrics: [],
    optionId: "annotator",
    categoryName: "Annotations",
    categoryCountLabel: "Annotations property count",
  },
];

beforeEach(() => {
  intersectionObservers = [];
  globalThis.IntersectionObserver = class IntersectionObserver {
    constructor(callback, options = {}) {
      this.callback = callback;
      this.root = options.root;
      this.targets = new Set();
      intersectionObservers.push(this);
    }

    disconnect() {
      this.targets.clear();
    }

    observe(target) {
      this.targets.add(target);
    }

    unobserve(target) {
      this.targets.delete(target);
    }
  };
  dashboardFilterValuesMock.mockReturnValue(defaultDashboardFilterValues());
  propertyCatalogMock.mockReturnValue({
    error: {
      response: {
        status: 503,
        data: { code: "property_catalog_not_ready" },
      },
    },
    legacyFallbackRequired: true,
    metrics: [],
  });
  exactAttributePropertiesMock.mockReturnValue(
    defaultExactAttributeProperties(),
  );
});

describe("JSON array picker value identity", () => {
  it("preserves scalar JSON types and removes only exact duplicates", () => {
    expect(
      normalizePickerValues([
        { value: true, label: "true" },
        { value: 1, label: "1" },
        { value: 1.0, label: "1.0" },
        { value: "1", label: "1" },
        { value: false, label: "false" },
        { value: 0, label: "0" },
        { value: true, label: "duplicate" },
        true,
        7,
        false,
        0,
        "  text  ",
        null,
        Number.NaN,
      ]),
    ).toEqual([true, 1, "1", false, 0, 7, "text"]);
  });
});

describe("PropertyPickerPaginationControl", () => {
  it("names an in-flight catalog continuation explicitly", () => {
    render(
      <PropertyPickerPaginationControl
        resetKey="project-one"
        scrollRootRef={{ current: document.createElement("div") }}
        attributePageAvailable={false}
        attributeContinuationKey={null}
        isFetchingAttributePage={false}
        attributePageError={false}
        onLoadMoreAttributes={vi.fn()}
        catalogPageAvailable
        catalogContinuationKey="catalog-cursor-2"
        isFetchingCatalogPage
        catalogPageError={false}
        onLoadMoreCatalog={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      screen.getByText("Loading next property catalog page…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Loading more properties…"),
    ).not.toBeInTheDocument();
  });

  it("advances both property inventories through one single-flight intersection", () => {
    const loadAttributes = vi.fn(() => new Promise(() => {}));
    const loadCatalog = vi.fn(() => new Promise(() => {}));
    const scrollRootRef = { current: document.createElement("div") };

    render(
      <PropertyPickerPaginationControl
        resetKey="project-one"
        scrollRootRef={scrollRootRef}
        attributePageAvailable
        attributeContinuationKey="attribute-cursor-2"
        isFetchingAttributePage={false}
        attributePageError={false}
        onLoadMoreAttributes={loadAttributes}
        catalogPageAvailable
        catalogContinuationKey="catalog-cursor-2"
        isFetchingCatalogPage={false}
        catalogPageError={false}
        onLoadMoreCatalog={loadCatalog}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Load more properties" }),
    ).not.toBeInTheDocument();
    triggerPropertyPageIntersection();
    triggerPropertyPageIntersection();

    expect(loadAttributes).toHaveBeenCalledOnce();
    expect(loadCatalog).toHaveBeenCalledOnce();
  });

  it("restarts the same property cursors when the project scope changes", async () => {
    const loadProjectOne = vi.fn().mockResolvedValue(undefined);
    const loadProjectTwo = vi.fn().mockResolvedValue(undefined);
    const scrollRootRef = { current: document.createElement("div") };
    const renderControl = (resetKey, loadCatalog) => (
      <PropertyPickerPaginationControl
        resetKey={resetKey}
        scrollRootRef={scrollRootRef}
        attributePageAvailable={false}
        attributeContinuationKey={null}
        isFetchingAttributePage={false}
        attributePageError={false}
        onLoadMoreAttributes={vi.fn()}
        catalogPageAvailable
        catalogContinuationKey="catalog-cursor-2"
        isFetchingCatalogPage={false}
        catalogPageError={false}
        onLoadMoreCatalog={loadCatalog}
      />
    );
    const { rerender } = render(renderControl("project-one", loadProjectOne));

    triggerPropertyPageIntersection();
    await waitFor(() => expect(loadProjectOne).toHaveBeenCalledOnce());

    rerender(renderControl("project-two", loadProjectTwo));
    triggerPropertyPageIntersection();
    await waitFor(() => expect(loadProjectTwo).toHaveBeenCalledOnce());
  });
});

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({
    parseQuery: parseQueryMock,
    loading: false,
    error: null,
  }),
}));

vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: dashboardFilterValuesMock,
  usePropertyCatalog: propertyCatalogMock,
}));

vi.mock("../useExactTraceAttributeProperties", () => ({
  useExactTraceAttributeProperties: exactAttributePropertiesMock,
}));

function renderPanel({
  currentFilters = [],
  properties,
  onApply = vi.fn(),
  onClose = vi.fn(),
  open = true,
  showQueryTab = false,
  projectId,
  source,
  propertyNamespace,
  attributeSource,
  tab,
  allowWorkspaceScope = false,
  propertyFilter,
  catalogError = false,
  hasNextCatalogPage = false,
  catalogContinuationKey = null,
  isFetchingNextCatalogPage = false,
  catalogNextPageError = false,
  loadNextCatalogPage,
  catalogCategoryCounts = null,
  catalogCategoryCountsExact = false,
}) {
  let panelProps = {
    currentFilters,
    properties,
    onApply,
    onClose,
    open,
    showQueryTab,
    projectId,
    source,
    propertyNamespace,
    attributeSource,
    tab,
    allowWorkspaceScope,
    propertyFilter,
    catalogError,
    hasNextCatalogPage,
    catalogContinuationKey,
    isFetchingNextCatalogPage,
    catalogNextPageError,
    loadNextCatalogPage,
    catalogCategoryCounts,
    catalogCategoryCountsExact,
  };
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const panel = () => (
    <QueryClientProvider client={queryClient}>
      <TraceFilterPanel
        anchorEl={anchorEl}
        open={panelProps.open}
        onClose={panelProps.onClose}
        onApply={panelProps.onApply}
        currentFilters={[...panelProps.currentFilters]}
        properties={panelProps.properties}
        showQueryTab={panelProps.showQueryTab}
        projectId={panelProps.projectId}
        source={panelProps.source}
        propertyNamespace={panelProps.propertyNamespace}
        attributeSource={panelProps.attributeSource}
        tab={panelProps.tab}
        allowWorkspaceScope={panelProps.allowWorkspaceScope}
        propertyFilter={panelProps.propertyFilter}
        catalogError={panelProps.catalogError}
        hasNextCatalogPage={panelProps.hasNextCatalogPage}
        catalogContinuationKey={panelProps.catalogContinuationKey}
        isFetchingNextCatalogPage={panelProps.isFetchingNextCatalogPage}
        catalogNextPageError={panelProps.catalogNextPageError}
        loadNextCatalogPage={panelProps.loadNextCatalogPage}
        catalogCategoryCounts={panelProps.catalogCategoryCounts}
        catalogCategoryCountsExact={panelProps.catalogCategoryCountsExact}
      />
    </QueryClientProvider>
  );
  const utils = render(panel());
  return {
    anchorEl,
    onApply,
    onClose,
    ...utils,
    rerenderPanel: (nextProps = {}) => {
      panelProps = { ...panelProps, ...nextProps };
      utils.rerender(panel());
    },
  };
}

describe("TraceFilterPanel workspace property scope", () => {
  it("loads the unified catalog without requiring a route project", () => {
    propertyCatalogMock.mockReturnValue({
      metrics: [],
      legacyFallbackRequired: false,
      usesUnifiedCatalog: true,
    });

    renderPanel({ allowWorkspaceScope: true });

    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectIds: [],
        enabled: true,
        allowLegacyNotReadyFallback: true,
      }),
    );
  });

  it("loads workspace Users attributes from the trace catalog", () => {
    propertyCatalogMock.mockReturnValue({
      metrics: [],
      legacyFallbackRequired: false,
      usesUnifiedCatalog: true,
    });

    renderPanel({
      source: "sessions",
      propertyNamespace: "users",
      attributeSource: "traces",
      allowWorkspaceScope: true,
    });

    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectIds: [],
        source: "traces",
        enabled: true,
      }),
    );
  });

  it("falls back to the bounded organization property page when the catalog is not ready", async () => {
    const get = vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        result: {
          metrics: [],
          has_more: false,
          page: 1,
        },
      },
    });
    propertyCatalogMock.mockReturnValue({
      metrics: [],
      legacyFallbackRequired: true,
      usesUnifiedCatalog: false,
    });

    renderPanel({ allowWorkspaceScope: true });

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith(
        endpoints.dashboard.metrics,
        expect.objectContaining({
          params: expect.not.objectContaining({
            project_ids: expect.anything(),
          }),
        }),
      ),
    );
    get.mockRestore();
  });

  it.each([
    [
      "project",
      { projectId: "project-partial-search" },
      "project-partial-search",
    ],
    ["workspace", { allowWorkspaceScope: true }, null],
  ])(
    "uses bounded partial attribute search at %s scope",
    async (_scope, panelProps, expectedProjectId) => {
      const get = vi.spyOn(axios, "get").mockImplementation((url, config) => {
        if (url !== endpoints.dashboard.metrics) {
          return Promise.resolve({ data: { result: {} } });
        }
        const isAttributeSearch =
          config?.params?.category === "custom_attribute" &&
          config?.params?.search === "prompt_sl";
        return Promise.resolve({
          data: {
            result: {
              metrics: isAttributeSearch
                ? [
                    {
                      name: "prompt_slug",
                      display_name: "prompt_slug",
                      category: "custom_attribute",
                      source: "traces",
                      type: "string",
                    },
                  ]
                : [],
              total: isAttributeSearch ? 1 : 0,
              page: 1,
              page_size: config?.params?.page_size,
              has_more: false,
            },
          },
        });
      });
      propertyCatalogMock.mockReturnValue({
        metrics: [],
        legacyFallbackRequired: true,
        usesUnifiedCatalog: false,
        isLoading: false,
        isFetching: false,
        isError: false,
        isSuccess: false,
      });

      const { anchorEl } = renderPanel({
        source: "traces",
        ...panelProps,
      });
      fireEvent.click(screen.getByRole("button", { name: "Property" }));
      fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
        target: { value: "prompt_sl" },
      });

      await waitFor(() =>
        expect(get).toHaveBeenCalledWith(
          endpoints.dashboard.metrics,
          expect.objectContaining({
            params: expect.objectContaining({
              category: "custom_attribute",
              source: "traces",
              search: "prompt_sl",
              per_eval_config: true,
              page: 1,
              page_size: 20,
              ...(expectedProjectId ? { project_ids: expectedProjectId } : {}),
            }),
          }),
        ),
      );
      if (!expectedProjectId) {
        const matchingCall = get.mock.calls.find(
          ([url, config]) =>
            url === endpoints.dashboard.metrics &&
            config?.params?.category === "custom_attribute" &&
            config?.params?.search === "prompt_sl",
        );
        expect(matchingCall?.[1]?.params).not.toHaveProperty("project_ids");
      }
      expect(await screen.findByText("prompt_slug")).toBeInTheDocument();

      document.body.removeChild(anchorEl);
      get.mockRestore();
    },
  );

  it("does not read property inventories for a mounted but closed panel", () => {
    propertyCatalogMock.mockReturnValue({
      metrics: [],
      legacyFallbackRequired: false,
      usesUnifiedCatalog: true,
    });
    propertyCatalogMock.mockClear();
    exactAttributePropertiesMock.mockClear();

    renderPanel({
      open: false,
      projectId: "project-closed-filter",
      source: "traces",
    });

    expect(
      propertyCatalogMock.mock.calls.some(
        ([request]) => request.enabled === true,
      ),
    ).toBe(false);
    expect(
      exactAttributePropertiesMock.mock.calls.some(
        ([request]) => request.enabled === true,
      ),
    ).toBe(false);
  });
});

const selectQueryPhaseOption = async (typed, nextPlaceholder) => {
  const input = screen.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: typed } });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() =>
    expect(input).toHaveAttribute("placeholder", nextPlaceholder),
  );
  return input;
};

const scrollQueryOptionsToEnd = async () => {
  const listbox = await screen.findByRole("listbox");
  Object.defineProperties(listbox, {
    scrollTop: { configurable: true, value: 180 },
    clientHeight: { configurable: true, value: 220 },
    scrollHeight: { configurable: true, value: 400 },
  });
  fireEvent.scroll(listbox);
  return listbox;
};

describe("TraceFilterPanel AI apply (#577)", () => {
  beforeEach(() => {
    parseQueryMock.mockReset();
  });

  it("runs the AI filter when the AI query is submitted (Enter)", async () => {
    parseQueryMock.mockResolvedValue([
      { field: "status", operator: "equals", value: "ERROR" },
    ]);
    const onApply = vi.fn();
    const onClose = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <TraceFilterPanel
          anchorEl={anchorEl}
          open
          onClose={onClose}
          onApply={onApply}
          currentFilters={[]}
          properties={[
            {
              id: "status",
              name: "Status",
              category: "system",
              type: "string",
            },
          ]}
          showQueryTab={false}
        />
      </QueryClientProvider>,
    );

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "show errors" } });
    // Auto-apply removed the footer "Apply" button; the AI query is now
    // submitted via Enter (or the inline send button in the input).
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() => {
      expect(parseQueryMock).toHaveBeenCalledWith("show errors", {
        smart: true,
        projectId: undefined,
        source: "traces",
      });
    });
    // The AI path now applies computeValidFilters(converted) like every other
    // path, so the operator is normalized to the canonical string op ("in").
    expect(onApply).toHaveBeenCalledWith([
      {
        field: "status",
        fieldCategory: "system",
        fieldType: "string",
        apiColType: undefined,
        registryId: "system_attribute:traces:status",
        operator: "in",
        value: ["ERROR"],
      },
    ]);
    expect(onClose).toHaveBeenCalled();

    document.body.removeChild(anchorEl);
  });
});

describe("TraceFilterPanel AI apply: additive, empty, single-call", () => {
  const properties = [
    { id: "status", name: "Status", category: "system", type: "string" },
    { id: "language", name: "Language", category: "system", type: "string" },
  ];

  beforeEach(() => {
    parseQueryMock.mockReset();
  });

  it("merges the AI-returned filter with the already-applied filter set", async () => {
    parseQueryMock.mockResolvedValue([
      { field: "language", operator: "equals", value: "english" },
    ]);
    const { anchorEl, onApply } = renderPanel({
      currentFilters: [
        {
          field: "status",
          fieldCategory: "system",
          fieldType: "string",
          operator: "in",
          value: ["ERROR"],
        },
      ],
      properties,
    });

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "language is english" } });
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() => expect(parseQueryMock).toHaveBeenCalled());
    await waitFor(() => expect(onApply).toHaveBeenCalled());

    const lastCall = onApply.mock.calls[onApply.mock.calls.length - 1][0];
    expect(lastCall).toHaveLength(2);
    expect(lastCall[0]).toMatchObject({ field: "status", value: ["ERROR"] });
    expect(lastCall[1]).toMatchObject({
      field: "language",
      value: ["english"],
    });

    document.body.removeChild(anchorEl);
  });

  it("uses property_id to disambiguate same-name AI fields", async () => {
    parseQueryMock.mockResolvedValue([
      {
        field: "status",
        property_id: "annotation:status",
        operator: "is",
        value: "Approved",
      },
    ]);
    const { anchorEl, onApply } = renderPanel({
      properties: [
        {
          id: "status",
          name: "Status",
          category: "system",
          type: "string",
          registryId: "system_attribute:traces:status",
        },
        {
          id: "status",
          name: "Review status",
          category: "annotation",
          type: "categorical",
          apiColType: "ANNOTATION",
          registryId: "annotation:status",
        },
      ],
    });

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "approved reviews" } });
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply.mock.calls[0][0]).toEqual([
      expect.objectContaining({
        field: "status",
        registryId: "annotation:status",
        fieldCategory: "annotation",
        apiColType: "ANNOTATION",
        value: ["Approved"],
      }),
    ]);

    document.body.removeChild(anchorEl);
  });

  it("shows an inline caption when the AI returns an empty filter list", async () => {
    parseQueryMock.mockResolvedValue([]);
    const { anchorEl, onApply, onClose } = renderPanel({
      properties,
    });

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "gibberish" } });
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() => expect(parseQueryMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        screen.getByText(/Could not derive filters from that query/i),
      ).toBeInTheDocument(),
    );

    expect(onApply).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(aiInput.value).toBe("gibberish");

    document.body.removeChild(anchorEl);
  });

  it("does not apply an ungrounded fallback when smart grounding rejects", async () => {
    parseQueryMock.mockRejectedValue(
      new Error("AI value grounding needs a more specific value."),
    );
    const { anchorEl, onApply, onClose } = renderPanel({
      properties,
      projectId: "00000000-0000-4000-8000-000000000001",
    });

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "model gpt" } });
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() => expect(parseQueryMock).toHaveBeenCalledTimes(1));
    expect(onApply).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(aiInput.value).toBe("model gpt");
    expect(
      screen.queryByText(/Could not derive filters from that query/i),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("clears the empty-result caption when the user edits the query", async () => {
    parseQueryMock.mockResolvedValue([]);
    const { anchorEl } = renderPanel({ properties });

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "gibberish" } });
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() =>
      expect(
        screen.getByText(/Could not derive filters from that query/i),
      ).toBeInTheDocument(),
    );

    fireEvent.change(aiInput, { target: { value: "gibberish typing more" } });

    expect(
      screen.queryByText(/Could not derive filters from that query/i),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("only calls onApply once with the AI filter set on a successful apply", async () => {
    parseQueryMock.mockResolvedValue([
      { field: "status", operator: "equals", value: "ERROR" },
    ]);
    const { anchorEl, onApply } = renderPanel({ properties });

    const aiInput = screen.getByPlaceholderText(/Ask AI/i);
    fireEvent.change(aiInput, { target: { value: "show errors" } });
    fireEvent.keyDown(aiInput, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    const [applied] = onApply.mock.calls[0];
    expect(applied).not.toBeNull();
    expect(applied[0]).toMatchObject({ field: "status" });

    document.body.removeChild(anchorEl);
  });
});

describe("getTraceFilterFields (TH-4571)", () => {
  it("prepends Trace ID when tab is 'trace'", () => {
    const fields = getTraceFilterFields("trace");
    expect(fields[0]).toMatchObject({
      value: "trace_id",
      label: "Trace ID",
      type: "string",
    });
    expect(fields.some((f) => f.value === "span_id")).toBe(false);
  });

  it("offers every backend span kind for node_type and drops the dead 'generation'", () => {
    const nodeType = getTraceFilterFields("trace").find(
      (f) => f.value === "node_type",
    );
    expect(nodeType).toBeTruthy();
    // Every span kind the backend can store must be filterable.
    [
      "chain",
      "retriever",
      "llm",
      "tool",
      "agent",
      "embedding",
      "reranker",
      "guardrail",
      "evaluator",
      "conversation",
      "unknown",
    ].forEach((kind) => expect(nodeType.choices).toContain(kind));
    // `generation` is not an FI span kind (Langfuse's maps to `llm` on ingest).
    expect(nodeType.choices).not.toContain("generation");
  });

  it("prepends Trace ID and Span ID when tab is 'spans'", () => {
    const fields = getTraceFilterFields("spans");
    expect(fields[0]).toMatchObject({ value: "trace_id", label: "Trace ID" });
    expect(fields[1]).toMatchObject({ value: "span_id", label: "Span ID" });
  });

  it("returns base fields unchanged when tab is null/undefined/unknown", () => {
    const fromNull = getTraceFilterFields(null);
    const fromUndefined = getTraceFilterFields(undefined);
    const fromUnknown = getTraceFilterFields("bogus");

    // None of the fallback calls should inject trace_id or span_id
    [fromNull, fromUndefined, fromUnknown].forEach((fields) => {
      expect(fields.some((f) => f.value === "trace_id")).toBe(false);
      expect(fields.some((f) => f.value === "span_id")).toBe(false);
    });

    // All fallbacks must return the same base list (same reference semantics
    // are not required; structural equality is what consumers rely on).
    expect(fromNull).toEqual(fromUndefined);
    expect(fromNull).toEqual(fromUnknown);
  });

  it("uses canonical voice-call fields without remapping global OTel status", () => {
    const fields = getTraceFilterFields("voiceCalls");

    expect(
      fields.find((field) => field.responseKey === "status"),
    ).toMatchObject({
      value: "call_status",
      category: "system",
      apiColType: "SYSTEM_METRIC",
    });
    expect(
      fields.find((field) => field.responseKey === "cost_cents"),
    ).toMatchObject({
      value: "cost_cents",
      type: "number",
      apiColType: "SYSTEM_METRIC",
    });
    expect(
      fields.find((field) => field.responseKey === "duration_seconds"),
    ).toMatchObject({ value: "duration", type: "number" });
    expect(
      fields.find((field) => field.responseKey === "call_id"),
    ).toMatchObject({
      value: "call_id",
      type: "text",
      category: "system",
      apiColType: "SYSTEM_METRIC",
    });

    // Normal trace/spans surfaces retain the OTel status column.
    expect(
      getTraceFilterFields("trace").some((field) => field.value === "status"),
    ).toBe(true);
  });
});

describe("catalog search global property supplements", () => {
  const tokensProperty = toStaticFilterProperty(
    getTraceFilterFields("voiceCalls").find(
      (field) => field.value === "gen_ai.usage.total_tokens",
    ),
    false,
    "voice_calls",
    "voice_calls",
  );
  const annotatorProperty = buildTraceFilterProperties([], {
    sourceScope: "traces",
    includeGlobalAnnotator: true,
  }).find((property) => property.id === "annotator");
  const emptySearchCounts = {
    all: 0,
    system_metric: 0,
    eval_metric: 0,
    annotation_metric: 0,
    custom_attribute: 0,
    custom_column: 0,
  };

  it.each([
    ["Tokens", tokensProperty, "system_metric"],
    ["Annotator", annotatorProperty, "annotation_metric"],
  ])(
    "restores searched %s and its exact category count when the server omits it",
    (search, property, categoryCountKey) => {
      expect(
        mergeCatalogSearchProperties({
          baseProperties: [property],
          catalogProperties: [],
          search,
        }),
      ).toEqual([property]);
      expect(
        supplementCatalogSearchCategoryCounts({
          categoryCounts: emptySearchCounts,
          baseProperties: [property],
          catalogProperties: [],
          search,
        }),
      ).toEqual({
        ...emptySearchCounts,
        all: 1,
        [categoryCountKey]: 1,
      });
    },
  );

  it("prefers the canonical local Tokens definition without double-counting a server alias", () => {
    const serverAlias = {
      id: "tokens",
      registryId: "system_attribute:voice_calls:tokens",
      name: "Tokens",
      category: "system",
      apiColType: "SYSTEM_METRIC",
      type: "number",
    };
    const serverCounts = {
      ...emptySearchCounts,
      all: 1,
      system_metric: 1,
    };

    expect(
      mergeCatalogSearchProperties({
        baseProperties: [tokensProperty],
        catalogProperties: [serverAlias],
        search: "Tokens",
      }),
    ).toEqual([tokensProperty]);
    expect(
      supplementCatalogSearchCategoryCounts({
        categoryCounts: serverCounts,
        baseProperties: [tokensProperty],
        catalogProperties: [serverAlias],
        search: "Tokens",
      }),
    ).toBe(serverCounts);
  });

  it("keeps a same-key raw Attribute separate from canonical System Tokens", () => {
    const rawAttribute = {
      id: "gen_ai.usage.total_tokens",
      registryId: "custom_attribute:gen_ai.usage.total_tokens",
      name: "gen_ai.usage.total_tokens",
      category: "attribute",
      rawCategory: "custom_attribute",
      apiColType: "SPAN_ATTRIBUTE",
      type: "number",
    };
    const attributeCounts = {
      ...emptySearchCounts,
      all: 1,
      custom_attribute: 1,
    };

    expect(
      mergeCatalogSearchProperties({
        baseProperties: [tokensProperty],
        catalogProperties: [rawAttribute],
        search: "Tokens",
      }),
    ).toEqual([tokensProperty, rawAttribute]);
    expect(
      supplementCatalogSearchCategoryCounts({
        categoryCounts: attributeCounts,
        baseProperties: [tokensProperty],
        catalogProperties: [rawAttribute],
        search: "Tokens",
      }),
    ).toEqual({
      ...attributeCounts,
      all: 2,
      system_metric: 1,
    });
  });

  it("does not restore project-specific System fields that authoritative search omitted", () => {
    const projectMetric = {
      id: "project_specific_metric",
      registryId: "system_attribute:traces:project_specific_metric",
      name: "Project Specific Metric",
      category: "system",
      apiColType: "SYSTEM_METRIC",
      type: "number",
    };

    expect(
      mergeCatalogSearchProperties({
        baseProperties: [projectMetric],
        catalogProperties: [],
        search: "Project Specific Metric",
      }),
    ).toEqual([]);
    expect(
      supplementCatalogSearchCategoryCounts({
        categoryCounts: emptySearchCounts,
        baseProperties: [projectMetric],
        catalogProperties: [],
        search: "Project Specific Metric",
      }),
    ).toBe(emptySearchCounts);
  });
});

describe("voice-call property search aliases", () => {
  const properties = getTraceFilterFields("voiceCalls").map((field) =>
    toStaticFilterProperty(field),
  );

  it("finds the displayed cost field by its Live Preview response key", () => {
    expect(
      filterPropertiesForPicker({ properties, search: "cost_cents" })[0],
    ).toEqual(
      expect.objectContaining({
        id: "cost_cents",
        name: "Cost (cents)",
        apiColType: "SYSTEM_METRIC",
      }),
    );
  });

  it("finds status and uses the normalized voice-list system metric", () => {
    expect(filterPropertiesForPicker({ properties, search: "status" })).toEqual(
      [
        expect.objectContaining({
          id: "call_status",
          category: "system",
          apiColType: "SYSTEM_METRIC",
        }),
      ],
    );
  });

  it("keeps a searched category isolated after browsing Attributes", () => {
    const nestedAttribute = {
      id: "conversation.transcript.0.tool_calls.0.tool_call.id",
      name: "conversation.transcript.0.tool_calls.0.tool_call.id",
      category: "attribute",
      type: "string",
      apiColType: "SPAN_ATTRIBUTE",
    };

    expect(
      filterPropertiesForPicker({
        properties: [...properties, nestedAttribute],
        category: "attribute",
        search: "call_id",
      }),
    ).toEqual([
      expect.objectContaining({
        id: "conversation.transcript.0.tool_calls.0.tool_call.id",
        category: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
      }),
    ]);
  });

  it("keeps all cost attribute matches beside an exact System Cost result", () => {
    const costProperties = [
      {
        id: "cost",
        name: "Cost",
        category: "system",
        type: "number",
        apiColType: "SYSTEM_METRIC",
      },
      {
        id: "cost_breakdown.analysisCost",
        name: "cost_breakdown.analysisCost",
        category: "attribute",
        type: "number",
        apiColType: "SPAN_ATTRIBUTE",
      },
      {
        id: "cost_breakdown.ttsCost",
        name: "cost_breakdown.ttsCost",
        category: "attribute",
        type: "number",
        apiColType: "SPAN_ATTRIBUTE",
      },
    ];

    expect(
      filterPropertiesForPicker({
        properties: costProperties,
        category: "all",
        search: "cost",
      }).map(({ id }) => id),
    ).toEqual([
      "cost",
      "cost_breakdown.analysisCost",
      "cost_breakdown.ttsCost",
    ]);
    expect(
      filterPropertiesForPicker({
        properties: costProperties,
        category: "attribute",
        search: "cost",
      }).map(({ id }) => id),
    ).toEqual(["cost_breakdown.analysisCost", "cost_breakdown.ttsCost"]);
  });

  it("treats the exact Call ID display label as the canonical call_id field", () => {
    expect(
      filterPropertiesForPicker({ properties, search: "Call ID" }),
    ).toEqual([
      expect.objectContaining({
        id: "call_id",
        name: "Call ID",
        category: "system",
        apiColType: "SYSTEM_METRIC",
      }),
    ]);
  });

  it("keeps loaded call_id attribute matches beside the canonical system id", () => {
    const fetchNextPage = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "conversation.transcript.0.tool_calls.0.tool_call.id",
          name: "conversation.transcript.0.tool_calls.0.tool_call.id",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "exact:call-id-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      debouncedSearch: "call_id",
      refetch: vi.fn(),
    });
    const { anchorEl } = renderPanel({ properties });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "call_id" },
    });

    expect(
      document.querySelector('[data-filter-property-option="call_id"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector(
        '[data-filter-property-option="conversation.transcript.0.tool_calls.0.tool_call.id"]',
      ),
    ).toBeInTheDocument();
    triggerPropertyPageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("exposes the complete property name when a picker row is truncated", () => {
    const longPropertyName =
      "cost_breakdown.analysisCostBreakdown.structuredOutputPromptTokens";
    const { anchorEl } = renderPanel({
      properties: [
        {
          id: longPropertyName,
          name: longPropertyName,
          category: "attribute",
          type: "number",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));

    expect(screen.getByText(longPropertyName)).toHaveAttribute(
      "title",
      longPropertyName,
    );
    document.body.removeChild(anchorEl);
  });

  it("keeps retained Call ID siblings pageable after the raw key is certified", () => {
    const fetchNextPage = vi.fn();
    let exactSearchMatched = false;
    let data = [
      {
        id: "recent_attribute",
        name: "recent_attribute",
        category: "attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      },
    ];
    exactAttributePropertiesMock.mockImplementation(() => ({
      data,
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "exact:call-id-sibling-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      exactSearchMatched,
      debouncedSearch: "Call ID",
      refetch: vi.fn(),
    }));
    const { anchorEl, rerenderPanel } = renderPanel({ properties });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "Call ID" },
    });

    expect(
      document.querySelector('[data-filter-property-option="call_id"]'),
    ).toBeInTheDocument();
    triggerPropertyPageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();

    data = [
      {
        id: "Call ID",
        name: "Call ID",
        category: "attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      },
    ];
    exactSearchMatched = true;
    rerenderPanel();

    expect(
      document.querySelector('[data-filter-property-option="Call ID"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector('[data-filter-property-option="call_id"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector("[data-filter-property-page-sentinel]"),
    ).toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("keeps continuation for trace.id when only the distinct trace_id key is loaded", () => {
    const fetchNextPage = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "trace_id",
          name: "trace_id",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "exact:trace-id-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      exactSearchMatched: false,
      debouncedSearch: "trace.id",
      refetch: vi.fn(),
    });
    const traceProperties = getTraceFilterFields("trace").map((field) =>
      toStaticFilterProperty(field),
    );
    const { anchorEl } = renderPanel({ properties: traceProperties });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "trace.id" },
    });

    expect(
      document.querySelector("[data-filter-property-page-sentinel]"),
    ).toBeInTheDocument();
    triggerPropertyPageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("keeps punctuation-normalized trace siblings visible and pageable after an exact match", () => {
    const fetchNextPage = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "trace.id",
          name: "trace.id",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage,
      // Exact certification stops the supplemental lookup, but this retained
      // continuation must remain independently reachable.
      hasNextPage: true,
      continuationKey: "retained:trace-id-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      exactSearchMatched: true,
      debouncedSearch: "trace.id",
      refetch: vi.fn(),
    });
    const traceProperties = getTraceFilterFields("trace").map((field) =>
      toStaticFilterProperty(field),
    );
    const { anchorEl } = renderPanel({ properties: traceProperties });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "trace.id" },
    });

    expect(
      document.querySelector('[data-filter-property-option="trace.id"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector('[data-filter-property-option="trace_id"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector("[data-filter-property-page-sentinel]"),
    ).toBeInTheDocument();
    triggerPropertyPageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it.each([
    ["tracing", "trace", "traces"],
    ["voice", "voiceCalls", "spans"],
  ])(
    "shows the exact %s property first while explicitly paging prefix siblings",
    (surface, tab, expectedAttributeSource) => {
      const fetchNextPage = vi.fn();
      let hasNextPage = true;
      let data = [
        {
          id: "foo",
          name: "foo",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
        {
          id: "foo_archive",
          name: "foo_archive",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ];
      exactAttributePropertiesMock.mockImplementation(({ search, source }) => ({
        data,
        isFetching: false,
        fetchNextPage,
        hasNextPage,
        continuationKey: hasNextPage ? `exact:${surface}:foo-cursor-2` : null,
        isFetchingNextPage: false,
        fetchNextExactPage: vi.fn(),
        hasNextExactPage: false,
        isFetchingExactSearch: false,
        isFetchingNextExactPage: false,
        isFetchNextPageError: false,
        exactSearchError: null,
        queryReadState: "complete",
        browseStatus: hasNextPage ? "continuation" : "exhausted",
        totalCount: 3,
        pageCount: hasNextPage ? 1 : 2,
        exactSearchMatched: search === "foo",
        cursorRetryExhausted: false,
        debouncedSearch: search.trim(),
        refetch: vi.fn(),
        source,
      }));
      const traceProperties = getTraceFilterFields(tab).map((field) =>
        toStaticFilterProperty(field),
      );
      const { anchorEl, rerenderPanel } = renderPanel({
        properties: traceProperties,
        projectId: `project-${surface}`,
        source: "traces",
        tab,
      });

      fireEvent.click(screen.getByRole("button", { name: "Property" }));
      fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
        target: { value: "foo" },
      });

      expect(
        Array.from(
          document.querySelectorAll("[data-filter-property-option]"),
        ).map((option) => option.dataset.filterPropertyOption),
      ).toEqual(["foo", "foo_archive"]);
      expect(
        screen.getByLabelText("Attributes property count"),
      ).toHaveTextContent("3");
      const exactAllCount =
        screen.getByLabelText("All property count").textContent;
      expect(Number(exactAllCount)).toBeGreaterThanOrEqual(3);
      expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
        expect.objectContaining({
          projectId: `project-${surface}`,
          search: "foo",
          source: expectedAttributeSource,
        }),
      );
      triggerPropertyPageIntersection();
      expect(fetchNextPage).toHaveBeenCalledOnce();

      data = [
        ...data,
        {
          id: "foo.bar",
          name: "foo.bar",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ];
      hasNextPage = false;
      rerenderPanel();

      expect(
        Array.from(
          document.querySelectorAll("[data-filter-property-option]"),
        ).map((option) => option.dataset.filterPropertyOption),
      ).toEqual(["foo", "foo_archive", "foo.bar"]);
      expect(
        screen.getByLabelText("Attributes property count"),
      ).toHaveTextContent("3");
      expect(screen.getByLabelText("All property count")).toHaveTextContent(
        exactAllCount,
      );
      expect(
        document.querySelector("[data-filter-property-page-sentinel]"),
      ).not.toBeInTheDocument();
      document.body.removeChild(anchorEl);
    },
  );

  it("shows an unknown attribute total instead of a growing loaded-page count", () => {
    let data = [
      {
        id: "recent.attribute",
        name: "recent.attribute",
        category: "attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      },
    ];
    exactAttributePropertiesMock.mockImplementation(() => ({
      data,
      isFetching: false,
      fetchNextPage: vi.fn(),
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextExactPage: vi.fn(),
      hasNextExactPage: false,
      isFetchingExactSearch: false,
      isFetchingNextExactPage: false,
      isFetchNextPageError: false,
      exactSearchError: null,
      queryReadState: "complete",
      browseStatus: "continuation",
      totalCount: null,
      pageCount: data.length,
      exactSearchMatched: false,
      cursorRetryExhausted: false,
      debouncedSearch: "",
      refetch: vi.fn(),
    }));

    const { anchorEl, rerenderPanel } = renderPanel({
      properties: getTraceFilterFields("trace").map((field) =>
        toStaticFilterProperty(field),
      ),
      projectId: "project-count-unknown",
      source: "traces",
      tab: "trace",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(
      screen.getByLabelText("Attributes property count unavailable"),
    ).toHaveTextContent("…");
    expect(
      screen.getByLabelText("All property count unavailable"),
    ).toHaveTextContent("…");

    data = [
      ...data,
      {
        id: "older.attribute",
        name: "older.attribute",
        category: "attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      },
    ];
    rerenderPanel();

    expect(
      screen.getByLabelText("Attributes property count unavailable"),
    ).toHaveTextContent("…");
    document.body.removeChild(anchorEl);
  });

  it("renders an exact zero attribute total", () => {
    const { anchorEl } = renderPanel({
      properties: getTraceFilterFields("trace").map((field) =>
        toStaticFilterProperty(field),
      ),
      projectId: "project-zero-attributes",
      source: "traces",
      tab: "trace",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("0");
    document.body.removeChild(anchorEl);
  });

  it("renders the exact project-scoped category breakdown from the catalog", () => {
    propertyCatalogMock.mockReturnValue({
      metrics: [],
      categoryCounts: {
        all: 333,
        system_metric: 111,
        eval_metric: 22,
        annotation_metric: 10,
        custom_attribute: 190,
        custom_column: 0,
      },
      categoryCountsExact: true,
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: vi.fn(),
      data: { pages: [] },
    });
    const { anchorEl } = renderPanel({
      projectId: "project-exact-counts",
      source: "traces",
      tab: "trace",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));

    expect(screen.getByLabelText("All property count")).toHaveTextContent(
      "333",
    );
    expect(screen.getByLabelText("System property count")).toHaveTextContent(
      "111",
    );
    expect(screen.getByLabelText("Evals property count")).toHaveTextContent(
      "22",
    );
    expect(
      screen.getByLabelText("Annotations property count"),
    ).toHaveTextContent("10");
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("190");
    document.body.removeChild(anchorEl);
  });

  it("retains exact base counts while a category page is loading", () => {
    propertyCatalogMock.mockImplementation(({ category = "" }) => ({
      metrics: [],
      categoryCounts: category
        ? null
        : {
            all: 436,
            system_metric: 53,
            eval_metric: 0,
            annotation_metric: 3,
            custom_attribute: 380,
            custom_column: 0,
          },
      categoryCountsExact: !category,
      legacyFallbackRequired: false,
      error: null,
      isLoading: Boolean(category),
      isFetching: Boolean(category),
      isError: false,
      isSuccess: !category,
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: vi.fn(),
      data: { pages: [] },
    }));
    const { anchorEl } = renderPanel({
      projectId: "project-loading-counts",
      source: "traces",
      tab: "trace",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.click(screen.getByText("Annotations"));

    expect(screen.getByLabelText("All property count")).toHaveTextContent(
      "436",
    );
    expect(screen.getByLabelText("System property count")).toHaveTextContent(
      "53",
    );
    expect(
      screen.getByLabelText("Annotations property count"),
    ).toHaveTextContent("3");
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("380");
    document.body.removeChild(anchorEl);
  });

  it("keeps base counts invariant after a category page settles", () => {
    propertyCatalogMock.mockImplementation(({ category = "" }) => ({
      metrics: [],
      // A scoped response can be served from a stale or mismatched cache
      // during a rolling deployment. Category navigation must not let it
      // replace the exact totals owned by the unfiltered base scope.
      categoryCounts: category
        ? {
            all: 258,
            system_metric: 36,
            eval_metric: 0,
            annotation_metric: 0,
            custom_attribute: 222,
            custom_column: 0,
          }
        : {
            all: 275,
            system_metric: 53,
            eval_metric: 0,
            annotation_metric: 0,
            custom_attribute: 222,
            custom_column: 0,
          },
      categoryCountsExact: true,
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: vi.fn(),
      data: { pages: [] },
    }));
    const { anchorEl } = renderPanel({
      projectId: "project-settled-counts",
      source: "traces",
      tab: "trace",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.click(screen.getByText("System"));

    expect(screen.getByLabelText("All property count")).toHaveTextContent(
      "275",
    );
    expect(screen.getByLabelText("System property count")).toHaveTextContent(
      "53",
    );
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("222");
    document.body.removeChild(anchorEl);
  });

  it.each(GLOBAL_CATALOG_SEARCH_CASES)(
    "keeps omitted global $search visible in Basic search and category counts",
    async ({
      search,
      source,
      tab,
      initialMetrics,
      optionId,
      categoryName,
      categoryCountLabel,
    }) => {
      propertyCatalogMock.mockImplementation(({ search: catalogSearch = "" }) =>
        settledPropertyCatalog({
          metrics: catalogSearch ? [] : initialMetrics,
          categoryCounts: catalogSearch
            ? {
                all: 0,
                system_metric: 0,
                eval_metric: 0,
                annotation_metric: 0,
                custom_attribute: 0,
                custom_column: 0,
              }
            : undefined,
        }),
      );
      const { anchorEl } = renderPanel({
        projectId: `project-global-${search.toLowerCase()}`,
        source,
        tab,
      });

      fireEvent.click(screen.getByRole("button", { name: "Property" }));
      fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
        target: { value: search },
      });

      await waitFor(() =>
        expect(propertyCatalogMock).toHaveBeenCalledWith(
          expect.objectContaining({ search }),
        ),
      );
      await waitFor(() =>
        expect(
          document.querySelector(`[data-filter-property-option="${optionId}"]`),
        ).toBeInTheDocument(),
      );
      expect(
        screen.getByLabelText("Property search result count"),
      ).toHaveTextContent("1");
      expect(screen.getByLabelText("All property count")).toHaveTextContent(
        "1",
      );
      expect(screen.getByLabelText(categoryCountLabel)).toHaveTextContent("1");

      fireEvent.click(screen.getByText(categoryName));
      await waitFor(() =>
        expect(
          document.querySelector(`[data-filter-property-option="${optionId}"]`),
        ).toBeInTheDocument(),
      );
      expect(
        screen.getByLabelText("Property search result count"),
      ).toHaveTextContent("1");
      document.body.removeChild(anchorEl);
    },
  );

  it("uses the activated catalog for trace attributes, text search, and pagination", async () => {
    const fetchNextSearchPage = vi.fn();
    exactAttributePropertiesMock.mockClear();
    propertyCatalogMock.mockImplementation(({ search = "" }) => {
      const searching = search === "rare.attribute";
      return {
        metrics: [
          {
            property_id: searching
              ? "custom_attribute:rare.attribute"
              : "custom_attribute:catalog.attribute",
            name: searching ? "rare.attribute" : "catalog.attribute",
            display_name: searching ? "rare.attribute" : "catalog.attribute",
            category: "custom_attribute",
            source: "traces",
            sources: ["traces"],
            type: "string",
          },
        ],
        categoryCounts: {
          all: 1,
          system_metric: 0,
          eval_metric: 0,
          annotation_metric: 0,
          custom_attribute: 1,
          custom_column: 0,
        },
        categoryCountsExact: true,
        legacyFallbackRequired: false,
        error: null,
        isLoading: false,
        isFetching: false,
        isError: false,
        isSuccess: true,
        hasNextPage: searching,
        continuationKey: searching ? "catalog:rare-attribute-cursor-2" : null,
        isFetchingNextPage: false,
        isFetchNextPageError: false,
        cursorChainStopped: false,
        fetchNextPage: searching ? fetchNextSearchPage : vi.fn(),
        data: { pages: [] },
      };
    });

    const { anchorEl } = renderPanel({
      projectId: "project-catalog-search",
      source: "traces",
      tab: "trace",
    });
    fireEvent.click(screen.getByRole("button", { name: "Property" }));

    expect(screen.getByText("catalog.attribute")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Attributes"));
    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        category: "custom_attribute",
        projectIds: ["project-catalog-search"],
        pageSize: 20,
        enabled: true,
      }),
    );
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "rare.attribute" },
    });

    expect(await screen.findByText("rare.attribute")).toBeInTheDocument();
    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectIds: ["project-catalog-search"],
        source: "traces",
        search: "rare.attribute",
        pageSize: 20,
        enabled: true,
      }),
    );
    expect(screen.getByLabelText("All property count")).toHaveTextContent("1");
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("1");

    triggerPropertyPageIntersection();
    expect(fetchNextSearchPage).toHaveBeenCalledOnce();
    expect(
      exactAttributePropertiesMock.mock.calls.some(
        ([request]) => request.enabled === true,
      ),
    ).toBe(false);

    document.body.removeChild(anchorEl);
  });

  it("keeps partial system results visibly loading until matching attributes arrive", async () => {
    const costSystemMetric = {
      property_id: "system_attribute:traces:cost",
      name: "cost",
      display_name: "Cost",
      category: "system_metric",
      source: "traces",
      sources: ["traces"],
      type: "number",
    };
    propertyCatalogMock.mockImplementation(({ search = "" }) =>
      search
        ? {
            ...settledPropertyCatalog({ metrics: [] }),
            isLoading: false,
            isFetching: true,
            isRemoteCatalogSearchPending: true,
            isSuccess: true,
          }
        : settledPropertyCatalog({ metrics: [costSystemMetric] }),
    );
    const { anchorEl } = renderPanel({
      projectId: "project-cost-search",
      source: "traces",
      tab: "trace",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "cost" },
    });

    expect(screen.getByText("Cost")).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Searching property catalog" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Searching property catalog…")).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("automatically advances an externally supplied catalog through the real shared picker", async () => {
    const firstProperty = {
      id: "first-eval",
      registryId: "evaluation:first-eval",
      name: "First eval",
      category: "eval",
      type: "number",
      apiColType: "EVAL_METRIC",
    };
    const secondProperty = {
      id: "second-eval",
      registryId: "evaluation:second-eval",
      name: "Second eval",
      category: "eval",
      type: "number",
      apiColType: "EVAL_METRIC",
    };
    const loadNextCatalogPage = vi.fn(() => Promise.resolve());
    const panel = renderPanel({
      properties: [firstProperty],
      source: "simulation",
      hasNextCatalogPage: true,
      catalogContinuationKey: "simulation-evals-cursor-2",
      loadNextCatalogPage,
      catalogCategoryCounts: {
        all: 2,
        system_metric: 0,
        eval_metric: 2,
        annotation_metric: 0,
        custom_attribute: 0,
        custom_column: 0,
      },
      catalogCategoryCountsExact: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(screen.getByText("First eval")).toBeInTheDocument();
    expect(screen.queryByText("Second eval")).not.toBeInTheDocument();
    expect(screen.getByLabelText("All property count")).toHaveTextContent("2");

    triggerPropertyPageIntersection();

    await waitFor(() => expect(loadNextCatalogPage).toHaveBeenCalledOnce());
    panel.rerenderPanel({
      properties: [firstProperty, secondProperty],
      hasNextCatalogPage: false,
      catalogContinuationKey: null,
    });
    expect(await screen.findByText("Second eval")).toBeInTheDocument();

    document.body.removeChild(panel.anchorEl);
  });

  it("keeps exact All search counts and category isolation for voice-call cost fields", async () => {
    const systemCost = {
      property_id: "system_metric:cost_cents",
      name: "cost_cents",
      display_name: "Cost",
      category: "system_metric",
      source: "voice_calls",
      sources: ["voice_calls"],
      type: "number",
    };
    const costAttributes = [
      "cost_breakdown.analysisCost",
      "cost_breakdown.ttsCost",
    ].map((name) => ({
      property_id: `custom_attribute:${name}`,
      name,
      display_name: name,
      category: "custom_attribute",
      source: "voice_calls",
      sources: ["voice_calls"],
      type: "number",
    }));
    propertyCatalogMock.mockImplementation(({ category = "", search = "" }) => {
      const searching = search === "cost";
      const attributePage = category === "custom_attribute";
      return {
        metrics: searching
          ? attributePage
            ? costAttributes
            : [systemCost, ...costAttributes]
          : [],
        categoryCounts: searching
          ? attributePage
            ? {
                all: 30,
                system_metric: 1,
                eval_metric: 0,
                annotation_metric: 0,
                custom_attribute: 29,
                custom_column: 0,
              }
            : {
                all: 35,
                system_metric: 6,
                eval_metric: 0,
                annotation_metric: 0,
                custom_attribute: 29,
                custom_column: 0,
              }
          : {
              all: 35,
              system_metric: 6,
              eval_metric: 0,
              annotation_metric: 0,
              custom_attribute: 29,
              custom_column: 0,
            },
        categoryCountsExact: true,
        legacyFallbackRequired: false,
        error: null,
        isLoading: false,
        isFetching: false,
        isError: false,
        isSuccess: true,
        hasNextPage: false,
        isFetchingNextPage: false,
        isFetchNextPageError: false,
        cursorChainStopped: false,
        fetchNextPage: vi.fn(),
        data: { pages: [] },
      };
    });
    const { anchorEl } = renderPanel({
      projectId: "00000000-0000-4000-8000-000000000035",
      source: "voice_calls",
      tab: "voiceCalls",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "cost" },
    });
    // Select a category before the debounced All-search response can be
    // retained in component state. The independent All-search request must
    // still own the global breakdown.
    fireEvent.click(screen.getByText("Attributes"));

    await waitFor(() =>
      expect(
        document.querySelector(
          '[data-filter-property-option="cost_breakdown.analysisCost"]',
        ),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByLabelText("Property search result count"),
    ).toHaveTextContent("29");
    expect(
      document.querySelector('[data-filter-property-option="cost_cents"]'),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("All property count")).toHaveTextContent("35");
    expect(screen.getByLabelText("System property count")).toHaveTextContent(
      "6",
    );
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("29");
    expect(
      document.querySelector('[data-filter-property-option="cost_cents"]'),
    ).not.toBeInTheDocument();
    expect(
      document.querySelector(
        '[data-filter-property-option="cost_breakdown.analysisCost"]',
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText("All"));
    await waitFor(() =>
      expect(
        screen.getByLabelText("Property search result count"),
      ).toHaveTextContent("35"),
    );
    expect(
      document.querySelector('[data-filter-property-option="cost_cents"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector(
        '[data-filter-property-option="cost_breakdown.analysisCost"]',
      ),
    ).toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("applies propertyFilter to authoritative Basic and Query catalog searches", async () => {
    propertyCatalogMock.mockImplementation(({ search = "" }) => ({
      metrics: search
        ? [
            {
              property_id: "system_metric:voice_calls:cost_cents",
              name: "cost_cents",
              display_name: "Cost",
              category: "system_metric",
              source: "voice_calls",
              sources: ["voice_calls"],
              type: "number",
            },
            {
              property_id: "custom_attribute:cost_breakdown.analysisCost",
              name: "cost_breakdown.analysisCost",
              display_name: "cost_breakdown.analysisCost",
              category: "custom_attribute",
              source: "voice_calls",
              sources: ["voice_calls"],
              type: "number",
            },
          ]
        : [],
      categoryCounts: {
        all: search ? 2 : 0,
        system_metric: search ? 1 : 0,
        eval_metric: 0,
        annotation_metric: 0,
        custom_attribute: search ? 1 : 0,
        custom_column: 0,
      },
      categoryCountsExact: true,
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: vi.fn(),
      data: { pages: [] },
    }));
    const { anchorEl } = renderPanel({
      projectId: "project-property-filter-search",
      source: "voice_calls",
      tab: "voiceCalls",
      showQueryTab: true,
      propertyFilter: (property) => property.category === "attribute",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "cost" },
    });
    const attributeOption = await waitFor(() => {
      const option = document.querySelector(
        '[data-filter-property-option="cost_breakdown.analysisCost"]',
      );
      expect(option).toBeInTheDocument();
      return option;
    });
    expect(
      document.querySelector('[data-filter-property-option="cost_cents"]'),
    ).not.toBeInTheDocument();
    fireEvent.click(attributeOption);

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const queryInput = screen.getByRole("combobox");
    fireEvent.focus(queryInput);
    fireEvent.change(queryInput, { target: { value: "cost" } });
    await waitFor(() =>
      expect(
        screen.getAllByText("cost_breakdown.analysisCost").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByText("Cost")).not.toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("keeps the browsed category when property search starts", () => {
    const { anchorEl } = renderPanel({
      properties: [
        ...properties,
        {
          id: "conversation.transcript.0.tool_calls.0.tool_call.id",
          name: "conversation.transcript.0.tool_calls.0.tool_call.id",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.click(screen.getByText("Attributes"));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "call_id" },
    });

    expect(
      document.querySelector('[data-filter-property-option="call_id"]'),
    ).not.toBeInTheDocument();
    expect(
      document.querySelector(
        '[data-filter-property-option="conversation.transcript.0.tool_calls.0.tool_call.id"]',
      ),
    ).toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("keeps canonical voice statuses available without a values request", () => {
    renderPanel({
      properties,
      currentFilters: [
        {
          field: "call_status",
          fieldName: "Status",
          fieldCategory: "system",
          fieldType: "string",
          apiColType: "SYSTEM_METRIC",
          operator: "in",
          value: [],
        },
      ],
    });

    fireEvent.click(
      document.querySelector('[data-filter-value-trigger="call_status"]'),
    );

    ["completed", "in-progress", "failed", "dropped", "not-connected"].forEach(
      (status) => {
        expect(
          document.querySelector(`[data-filter-value-option="${status}"]`),
        ).toBeInTheDocument();
      },
    );
    expect(dashboardFilterValuesMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        metricName: "call_status",
        metricType: "system_metric",
        source: "traces",
        pageSize: FILTER_VALUE_PAGE_SIZE,
        enabled: false,
      }),
    );
  });

  it("shows provider status aliases once under their canonical row status", () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [
        { value: "ended", label: "ended" },
        { value: "DONE", label: "DONE" },
        { value: "completed", label: "completed" },
      ],
    });
    const { anchorEl } = renderPanel({
      properties,
      currentFilters: [
        {
          field: "call_status",
          fieldName: "Status",
          fieldCategory: "system",
          fieldType: "string",
          apiColType: "SYSTEM_METRIC",
          operator: "in",
          value: ["ended"],
        },
      ],
    });

    fireEvent.click(
      document.querySelector('[data-filter-value-trigger="call_status"]'),
    );

    expect(
      document.querySelectorAll('[data-filter-value-option="completed"]'),
    ).toHaveLength(1);
    expect(
      document.querySelector('[data-filter-value-option="ended"]'),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("completed").length).toBeGreaterThan(0);

    document.body.removeChild(anchorEl);
  });

  it("keeps an uncatalogued raw call_status attribute raw in Basic and the API", async () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [
        { value: "ended", label: "ended" },
        { value: "processing", label: "processing" },
      ],
    });
    const onApply = vi.fn();
    const rawStatusFilter = {
      field: "call_status",
      fieldName: "call_status",
      fieldCategory: "attribute",
      fieldType: "string",
      apiColType: "SPAN_ATTRIBUTE",
      operator: "in",
      value: ["ended"],
    };
    const { anchorEl } = renderPanel({
      properties,
      currentFilters: [rawStatusFilter],
      onApply,
      projectId: "project-1",
    });

    // Only the canonical SYSTEM_METRIC is in the catalog. The explicit raw
    // identity must not borrow its closed choices or lifecycle normalization.
    expect(
      findTraceFilterProperty(properties, rawStatusFilter),
    ).toBeUndefined();
    fireEvent.click(
      document.querySelector('[data-filter-value-trigger="call_status"]'),
    );

    expect(dashboardFilterValuesMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        metricName: "call_status",
        metricType: "custom_attribute",
        enabled: true,
      }),
    );
    expect(
      document.querySelector('[data-filter-value-option="ended"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector('[data-filter-value-option="processing"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector('[data-filter-value-option="completed"]'),
    ).not.toBeInTheDocument();
    expect(
      document.querySelector('[data-filter-value-option="in-progress"]'),
    ).not.toBeInTheDocument();

    fireEvent.click(
      document.querySelector('[data-filter-value-option="processing"]'),
    );
    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const applied = onApply.mock.calls.at(-1)[0][0];
    expect(applied).toMatchObject({
      field: "call_status",
      fieldCategory: "attribute",
      apiColType: "SPAN_ATTRIBUTE",
      value: ["ended", "processing"],
    });
    expect(buildApiFilterFromPanelRow(applied)).toMatchObject({
      column_id: "call_status",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: ["ended", "processing"],
        col_type: "SPAN_ATTRIBUTE",
      },
    });

    document.body.removeChild(anchorEl);
  });
});

describe("voice-call property parity", () => {
  it("does not bind an explicit identity to a lone mismatched same-id property", () => {
    const systemOnly = [
      {
        id: "call_status",
        name: "Status",
        category: "system",
        type: "string",
      },
    ];
    const rawFilter = {
      field: "call_status",
      fieldCategory: "attribute",
      apiColType: "SPAN_ATTRIBUTE",
    };

    expect(findTraceFilterProperty(systemOnly, rawFilter)).toBeUndefined();
    expect(
      findTraceFilterProperty(systemOnly, {
        ...rawFilter,
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
      }),
    ).toEqual(systemOnly[0]);
  });

  it("stamps specialized system filterFields with their wire identity", () => {
    expect(
      mergeTraceFilterProperties({
        filterFields: [
          { id: "specialized_status", name: "Specialized Status" },
        ],
      }),
    ).toContainEqual(
      expect.objectContaining({
        id: "specialized_status",
        category: "system",
        apiColType: "SYSTEM_METRIC",
      }),
    );
  });

  it("keeps supplied filterField category and registry identity", () => {
    expect(
      mergeTraceFilterProperties({
        propertyNamespace: "users",
        filterFields: [
          {
            id: "review_label",
            name: "Review Label",
            category: "annotation",
            apiColType: "ANNOTATION",
            registryId: "annotation:review-label-id",
            type: "categorical",
          },
        ],
      }),
    ).toContainEqual(
      expect.objectContaining({
        id: "review_label",
        registryId: "annotation:review-label-id",
        category: "annotation",
        apiColType: "ANNOTATION",
      }),
    );
  });

  it("uses surface namespaces without changing the native value source", () => {
    expect(
      mergeTraceFilterProperties({ tab: "voiceCalls", source: "traces" }).find(
        (property) => property.id === "ended_reason",
      ),
    ).toMatchObject({
      registryId: "system_attribute:voice_calls:ended_reason",
    });
    expect(
      mergeTraceFilterProperties({
        source: "sessions",
        propertyNamespace: "users",
        filterFields: [{ id: "user_id", name: "User ID" }],
      }),
    ).toContainEqual(
      expect.objectContaining({
        id: "user_id",
        registryId: "system_attribute:users:user_id",
      }),
    );
  });

  it("keeps the Sessions property identity stable across native value aliases", () => {
    expect(
      mergeTraceFilterProperties({
        source: "sessions",
        filterFields: [{ id: "session_id", name: "Session ID" }],
      }),
    ).toContainEqual(
      expect.objectContaining({
        id: "session_id",
        registryId: "system_attribute:sessions:session",
        category: "system",
        apiColType: "SYSTEM_METRIC",
      }),
    );
  });

  it("deduplicates dashboard system aliases while retaining raw attributes", () => {
    const merged = mergeTraceFilterProperties({
      tab: "voiceCalls",
      dynamicProperties: [
        { id: "status", category: "system", type: "string" },
        { id: "agent_latency", category: "system", type: "number" },
        { id: "tokens", category: "system", type: "number" },
        { id: "total_tokens", category: "system", type: "number" },
        { id: "total_cost", category: "system", type: "number" },
        { id: "user_interruptions", category: "system", type: "number" },
        { id: "ai_interruptions", category: "system", type: "number" },
        {
          id: "call.status",
          name: "call.status",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
        {
          id: "tokens",
          name: "tokens",
          category: "attribute",
          type: "number",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
    });

    const idsByCategory = merged.map(({ id, category }) => `${category}:${id}`);
    expect(idsByCategory).not.toContain("system:status");
    expect(idsByCategory).not.toContain("system:agent_latency");
    expect(idsByCategory).not.toContain("system:tokens");
    expect(idsByCategory).not.toContain("system:total_tokens");
    expect(idsByCategory).not.toContain("system:total_cost");
    expect(idsByCategory).not.toContain("system:user_interruptions");
    expect(idsByCategory).not.toContain("system:ai_interruptions");
    expect(idsByCategory).toContain("attribute:call.status");
    expect(idsByCategory).toContain("attribute:tokens");
    expect(
      idsByCategory.filter((id) => id === "system:call_status"),
    ).toHaveLength(1);
  });

  it("marks lifecycle status as a closed canonical vocabulary", () => {
    const status = mergeTraceFilterProperties({ tab: "voiceCalls" }).find(
      (property) => property.id === "call_status",
    );

    expect(status).toMatchObject({
      choices: [
        "completed",
        "in-progress",
        "failed",
        "dropped",
        "not-connected",
      ],
      allowCustomValue: false,
    });
  });

  it("keeps same-id system metrics and raw attributes category-qualified", () => {
    const merged = mergeTraceFilterProperties({
      tab: "voiceCalls",
      dynamicProperties: [
        {
          id: "cost_cents",
          name: "cost_cents",
          category: "attribute",
          type: "number",
          apiColType: "SPAN_ATTRIBUTE",
          attributeTypes: ["number"],
          attributeTypesExact: true,
        },
        {
          id: "call_id",
          name: "call_id",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
    });

    for (const field of ["cost_cents", "call_id"]) {
      expect(
        merged
          .filter((property) => property.id === field)
          .map((property) => [property.category, property.apiColType]),
      ).toEqual([
        ["system", "SYSTEM_METRIC"],
        ["attribute", "SPAN_ATTRIBUTE"],
      ]);
    }

    const costEntries = buildQueryPropertyEntries(
      merged.filter((property) => property.id === "cost_cents"),
    ).entries;
    expect(new Set(costEntries.map(([identity]) => identity)).size).toBe(2);
    expect(
      [...new Map(costEntries).values()].map((property) => property.category),
    ).toEqual(["system", "attribute"]);

    const systemRow = {
      field: "cost_cents",
      fieldCategory: "system",
      fieldType: "number",
      apiColType: "SYSTEM_METRIC",
      operator: "equals",
      value: 12.2,
    };
    const attributeRow = {
      ...systemRow,
      fieldCategory: "attribute",
      apiColType: "SPAN_ATTRIBUTE",
    };
    expect(findTraceFilterProperty(merged, systemRow)).toMatchObject({
      category: "system",
      apiColType: "SYSTEM_METRIC",
    });
    expect(findTraceFilterProperty(merged, attributeRow)).toMatchObject({
      category: "attribute",
      apiColType: "SPAN_ATTRIBUTE",
    });
    expect(
      findTraceFilterProperty(merged, {
        field: "cost_cents",
        registryId: "custom_attribute:cost_cents",
      }),
    ).toMatchObject({
      registryId: "custom_attribute:cost_cents",
      category: "attribute",
    });
    expect([
      buildApiFilterFromPanelRow(systemRow),
      buildApiFilterFromPanelRow(attributeRow),
    ]).toEqual([
      expect.objectContaining({
        column_id: "cost_cents",
        filter_config: expect.objectContaining({ col_type: "SYSTEM_METRIC" }),
      }),
      expect.objectContaining({
        column_id: "cost_cents",
        filter_config: expect.objectContaining({ col_type: "SPAN_ATTRIBUTE" }),
      }),
    ]);
  });

  it("uses raw attribute metadata for a same-id call_id value lookup", () => {
    dashboardFilterValuesMock.mockClear();
    const propertiesWithRawCallId = mergeTraceFilterProperties({
      tab: "voiceCalls",
      dynamicProperties: [
        {
          id: "call_id",
          name: "call_id",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
          attributeTypes: ["string"],
          attributeTypesExact: true,
        },
      ],
    });
    const { anchorEl } = renderPanel({
      properties: propertiesWithRawCallId,
      projectId: "project-1",
      currentFilters: [
        {
          field: "call_id",
          fieldName: "call_id",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "in",
          value: ["raw-call-id"],
        },
      ],
    });

    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "call_id",
        metricType: "custom_attribute",
        attributeType: "string",
      }),
    );
    document.body.removeChild(anchorEl);
  });

  it("keeps system and raw ended_reason value requests category-qualified", () => {
    dashboardFilterValuesMock.mockClear();
    const propertiesWithRawEndedReason = mergeTraceFilterProperties({
      tab: "voiceCalls",
      dynamicProperties: [
        {
          id: "ended_reason",
          name: "ended_reason",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
          attributeTypes: ["string"],
          attributeTypesExact: true,
        },
      ],
    });
    const { anchorEl } = renderPanel({
      properties: propertiesWithRawEndedReason,
      projectId: "project-1",
      currentFilters: [
        {
          field: "ended_reason",
          fieldName: "Ended Reason",
          fieldCategory: "system",
          fieldType: "string",
          apiColType: "SYSTEM_METRIC",
          operator: "in",
          value: [],
        },
        {
          field: "ended_reason",
          fieldName: "ended_reason",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "in",
          value: [],
        },
      ],
    });

    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "ended_reason",
        metricType: "system_metric",
        attributeType: undefined,
      }),
    );
    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "ended_reason",
        metricType: "custom_attribute",
        attributeType: "string",
      }),
    );
    document.body.removeChild(anchorEl);
  });

  it("selects raw cost_cents independently from canonical system cost", async () => {
    const propertiesWithRawCost = mergeTraceFilterProperties({
      tab: "voiceCalls",
      dynamicProperties: [
        {
          id: "cost_cents",
          name: "cost_cents",
          category: "attribute",
          type: "number",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
    });
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      properties: propertiesWithRawCost,
      onApply,
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "cost_cents" },
    });
    expect(
      document.querySelector(
        '[data-filter-property-option="cost_cents"][data-filter-property-category="system"]',
      ),
    ).toBeInTheDocument();
    const rawOption = document.querySelector(
      '[data-filter-property-option="cost_cents"][data-filter-property-category="attribute"]',
    );
    expect(rawOption).toBeInTheDocument();
    fireEvent.click(rawOption);
    fireEvent.change(screen.getByPlaceholderText("Value"), {
      target: { value: "12.2" },
    });

    await waitFor(() =>
      expect(onApply).toHaveBeenLastCalledWith([
        expect.objectContaining({
          field: "cost_cents",
          fieldCategory: "attribute",
          apiColType: "SPAN_ATTRIBUTE",
          value: "12.2",
        }),
      ]),
    );
    document.body.removeChild(anchorEl);
  });
});

describe("exact manual attribute fallback", () => {
  it("requests the unified trace catalog as a source-scoped page of 20", () => {
    propertyCatalogMock.mockReturnValue({
      legacyFallbackRequired: false,
      metrics: [],
      isLoading: false,
      isError: false,
      isSuccess: true,
      hasNextPage: false,
    });

    const { anchorEl } = renderPanel({
      projectId: "project-source-scope",
      source: "traces",
    });

    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectIds: ["project-source-scope"],
        source: "traces",
        pageSize: 20,
        perEvalConfig: true,
      }),
    );
    document.body.removeChild(anchorEl);
  });

  it("keeps unified voice definition, search, and category pages voice-scoped", async () => {
    propertyCatalogMock.mockImplementation(({ search = "" }) => {
      const searchingEndedReason = search === "ended_reason";
      return {
        legacyFallbackRequired: false,
        metrics: searchingEndedReason
          ? [
              {
                property_id: "system_attribute:voice_calls:ended_reason",
                name: "ended_reason",
                display_name: "Ended Reason",
                category: "system_metric",
                source: "voice_calls",
                sources: ["system", "voice_calls", "ended_reason"],
                type: "string",
              },
            ]
          : [],
        categoryCounts: searchingEndedReason
          ? {
              all: 1,
              system_metric: 1,
              eval_metric: 0,
              annotation_metric: 0,
              custom_attribute: 0,
              custom_column: 0,
            }
          : {
              all: 81,
              system_metric: 31,
              eval_metric: 0,
              annotation_metric: 0,
              custom_attribute: 50,
              custom_column: 0,
            },
        categoryCountsExact: true,
        error: null,
        isLoading: false,
        isError: false,
        isSuccess: true,
        hasNextPage: false,
        isFetchingNextPage: false,
        isFetchNextPageError: false,
        cursorChainStopped: false,
        fetchNextPage: vi.fn(),
        data: { pages: [] },
      };
    });

    const { anchorEl } = renderPanel({
      projectId: "project-voice-source-scope",
      source: "traces",
      tab: "voiceCalls",
    });

    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectIds: ["project-voice-source-scope"],
        source: "voice_calls",
        pageSize: 20,
        perEvalConfig: true,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.click(screen.getByText("Attributes"));

    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        category: "custom_attribute",
        projectIds: ["project-voice-source-scope"],
        source: "voice_calls",
        pageSize: 20,
        enabled: true,
      }),
    );
    expect(screen.getByLabelText("All property count")).toHaveTextContent("81");
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("50");

    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "ended_reason" },
    });

    await waitFor(() =>
      expect(screen.getByLabelText("All property count")).toHaveTextContent(
        "1",
      ),
    );
    expect(screen.getByLabelText("System property count")).toHaveTextContent(
      "1",
    );
    expect(
      screen.getByLabelText("Attributes property count"),
    ).toHaveTextContent("0");
    await waitFor(() =>
      expect(propertyCatalogMock).toHaveBeenCalledWith(
        expect.objectContaining({
          projectIds: ["project-voice-source-scope"],
          source: "voice_calls",
          search: "ended_reason",
          pageSize: 20,
          enabled: true,
        }),
      ),
    );
    document.body.removeChild(anchorEl);
  });

  it("keeps property selection usable and prefetches retained keys while the catalog is pending", async () => {
    let resolveCatalog;
    const pendingCatalog = new Promise((resolve) => {
      resolveCatalog = resolve;
    });
    const getSpy = vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === endpoints.dashboard.metrics) return pendingCatalog;
      return Promise.resolve({ data: { result: {} } });
    });

    const { anchorEl } = renderPanel({
      projectId: "project-whatfix",
      source: "traces",
    });

    await waitFor(() =>
      expect(getSpy).toHaveBeenCalledWith(
        endpoints.dashboard.metrics,
        expect.objectContaining({
          params: expect.objectContaining({
            page: 1,
            page_size: 200,
            project_ids: "project-whatfix",
            per_eval_config: true,
            exclude_custom_attributes: true,
          }),
          signal: expect.any(AbortSignal),
        }),
      ),
    );

    expect(screen.getByText("Loading properties…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Property" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(
      screen.getByPlaceholderText("Search properties..."),
    ).toBeInTheDocument();
    expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-whatfix",
        search: "",
        source: "traces",
        enabled: true,
      }),
    );

    resolveCatalog({ data: { result: { metrics: [] } } });
    await waitFor(() =>
      expect(screen.queryByText("Loading properties…")).not.toBeInTheDocument(),
    );
    getSpy.mockRestore();
    document.body.removeChild(anchorEl);
  });

  it("routes session custom attributes through the trace catalog and fact store", () => {
    dashboardFilterValuesMock.mockClear();
    exactAttributePropertiesMock.mockClear();
    const finalStatus = {
      id: "final_status",
      name: "final_status",
      category: "attribute",
      rawCategory: "custom_attribute",
      registryId: "custom_attribute:final_status",
      type: "string",
      attributeTypes: ["string"],
      attributeTypesExact: false,
      apiColType: "SPAN_ATTRIBUTE",
    };
    const { anchorEl } = renderPanel({
      properties: [finalStatus],
      projectId: "project-session-task",
      source: "sessions",
      attributeSource: "traces",
      currentFilters: [
        {
          field: "final_status",
          fieldName: "final_status",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "in",
          value: [],
        },
      ],
    });

    expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-session-task",
        source: "traces",
      }),
    );
    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "final_status",
        metricType: "custom_attribute",
        source: "traces",
      }),
    );

    document.body.removeChild(anchorEl);
  });

  it("keeps voice property keys span-scoped and searched values trace-scoped", () => {
    dashboardFilterValuesMock.mockClear();
    exactAttributePropertiesMock.mockClear();
    const finalStatus = {
      id: "final_status",
      name: "final_status",
      category: "attribute",
      rawCategory: "custom_attribute",
      registryId: "custom_attribute:final_status",
      type: "string",
      attributeTypes: ["string"],
      attributeTypesExact: false,
      apiColType: "SPAN_ATTRIBUTE",
    };
    const { anchorEl } = renderPanel({
      properties: [finalStatus],
      projectId: "project-voice-whatfix",
      source: "traces",
      tab: "voiceCalls",
      currentFilters: [
        {
          field: "final_status",
          fieldName: "final_status",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "in",
          value: [],
        },
      ],
    });

    expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-voice-whatfix",
        source: "spans",
      }),
    );
    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "final_status",
        metricType: "custom_attribute",
        source: "traces",
      }),
    );

    document.body.removeChild(anchorEl);
  });

  it("uses cursor-discovered attributes as the canonical paginated inventory", () => {
    const systemProperty = {
      id: "status",
      name: "Status",
      category: "system",
    };
    const catalogDuplicate = {
      id: "first_page_key",
      name: "first_page_key",
      category: "attribute",
    };
    const catalogOnly = {
      id: "catalog_sample_key",
      name: "catalog_sample_key",
      category: "attribute",
    };
    const retainedPage = [
      {
        id: "first_page_key",
        name: "first_page_key",
        category: "attribute",
      },
      {
        id: "next_page_key",
        name: "next_page_key",
        category: "attribute",
      },
    ];

    expect(
      mergeRetainedAttributeProperties(
        [systemProperty, catalogDuplicate, catalogOnly],
        retainedPage,
        { canonical: true },
      ).map((property) => property.id),
    ).toEqual(["status", "first_page_key", "next_page_key"]);
    expect(
      mergeRetainedAttributeProperties(
        [systemProperty, catalogDuplicate, catalogOnly],
        retainedPage.slice(0, 1),
        { canonical: false },
      ).map((property) => property.id),
    ).toEqual(["status", "first_page_key", "catalog_sample_key"]);
  });

  it("retains raw attributes whose id matches a system metric", () => {
    expect(
      mergeRetainedAttributeProperties(
        [{ id: "cost_cents", category: "system" }],
        [{ id: "cost_cents", category: "attribute" }],
        { canonical: true },
      ).map((property) => [property.category, property.id]),
    ).toEqual([
      ["system", "cost_cents"],
      ["attribute", "cost_cents"],
    ]);
  });

  it("keeps sampled catalog attributes through an empty cursor continuation", () => {
    expect(
      shouldUseRetainedAttributePages({
        enabled: true,
        source: "spans",
        readState: "complete",
        attributes: [],
        browseStatus: "continuation",
      }),
    ).toBe(false);

    expect(
      shouldUseRetainedAttributePages({
        enabled: true,
        source: "spans",
        readState: "complete",
        attributes: [],
        browseStatus: "exhausted",
      }),
    ).toBe(true);

    expect(
      shouldUseRetainedAttributePages({
        enabled: true,
        source: "traces",
        readState: "complete",
        attributes: [{ id: "retained_key" }],
        browseStatus: "continuation",
      }),
    ).toBe(true);
  });

  it("offers an exact text attribute only after bounded discovery has no exact key", () => {
    expect(
      buildManualAttributeProperty({
        search: "final_status",
        category: "all",
        properties: [],
      }),
    ).toEqual({
      id: "final_status",
      name: "final_status",
      category: "attribute",
      rawCategory: "custom_attribute",
      registryId: "custom_attribute:final_status",
      type: "string",
      apiColType: "SPAN_ATTRIBUTE",
      isManualExactAttribute: true,
    });
  });

  it("keeps the exact backend type and never duplicates an existing attribute", () => {
    expect(
      buildManualAttributeProperty({
        search: "final_status",
        category: "attribute",
        properties: [
          {
            id: "final_status",
            category: "attribute",
            type: "boolean",
          },
        ],
      }),
    ).toBeNull();
  });

  it("allows an exact raw attribute beside system voice cost_cents", () => {
    expect(
      buildManualAttributeProperty({
        search: "cost_cents",
        category: "all",
        properties: [
          {
            id: "cost_cents",
            category: "system",
            type: "number",
          },
        ],
      }),
    ).toEqual({
      id: "cost_cents",
      name: "cost_cents",
      category: "attribute",
      rawCategory: "custom_attribute",
      registryId: "custom_attribute:cost_cents",
      type: "string",
      apiColType: "SPAN_ATTRIBUTE",
      isManualExactAttribute: true,
    });
  });

  it("does not inject attributes into a system-only or specialized picker", () => {
    expect(
      buildManualAttributeProperty({
        search: "final_status",
        category: "system",
        properties: [],
      }),
    ).toBeNull();
    expect(
      buildManualAttributeProperty({
        search: "final_status",
        category: "all",
        properties: [],
        hasCategorySidebar: false,
      }),
    ).toBeNull();
  });

  it("continues one settled prompt_slug exact search without draining the cursor", async () => {
    const fetchNextPage = vi.fn();
    const fetchNextExactPage = vi.fn(() => Promise.resolve());
    exactAttributePropertiesMock.mockReturnValue({
      data: [],
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextExactPage,
      hasNextExactPage: true,
      continuationKey: "exact:prompt-slug-cursor-2",
      isFetchingExactSearch: false,
      isFetchingNextExactPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      exactSearchMatched: false,
      cursorRetryExhausted: false,
      debouncedSearch: "prompt_slug",
      refetch: vi.fn(),
    });
    const { anchorEl, rerenderPanel } = renderPanel({
      properties: [],
      projectId: "project-coletia",
      source: "traces",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "prompt_slug" },
    });

    await waitFor(() => expect(fetchNextExactPage).toHaveBeenCalledOnce());
    rerenderPanel();
    expect(fetchNextExactPage).toHaveBeenCalledOnce();
    expect(fetchNextPage).not.toHaveBeenCalled();
    expect(screen.queryByText("No properties found")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "No matching attribute found yet. Older attributes load automatically at the end of this list.",
      ),
    ).toBeInTheDocument();
    expect(
      document.querySelector("[data-filter-property-page-sentinel]"),
    ).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it.each([
    ["tracing", undefined, "traces"],
    ["voice", "voiceCalls", "spans"],
  ])(
    "lets %s repeat the same property search in one open picker",
    async (_surface, tab, expectedAttributeSource) => {
      const fetchPromptPage = vi.fn(() => Promise.resolve());
      exactAttributePropertiesMock.mockImplementation(({ search, source }) => ({
        data: [],
        isFetching: false,
        fetchNextPage: vi.fn(),
        hasNextPage: Boolean(search),
        isFetchingNextPage: false,
        fetchNextExactPage:
          search === "prompt_slug" ? fetchPromptPage : vi.fn(),
        hasNextExactPage: Boolean(search),
        isFetchingExactSearch: false,
        isFetchingNextExactPage: false,
        isFetchNextPageError: false,
        queryReadState: "complete",
        browseStatus: search ? "continuation" : "exhausted",
        pageCount: 1,
        exactSearchMatched: false,
        cursorRetryExhausted: false,
        debouncedSearch: search.trim(),
        refetch: vi.fn(),
        source,
      }));
      const { anchorEl } = renderPanel({
        properties: [],
        projectId: `project-${_surface}`,
        source: "traces",
        tab,
      });

      fireEvent.click(screen.getByRole("button", { name: "Property" }));
      const searchInput = screen.getByPlaceholderText("Search properties...");
      fireEvent.change(searchInput, { target: { value: "prompt_slug" } });
      await waitFor(() => expect(fetchPromptPage).toHaveBeenCalledOnce());

      // Re-enter before the debounce interval elapses. The raw gesture must
      // clear the one-shot identity even while the settled query stays cached.
      fireEvent.change(searchInput, { target: { value: "" } });
      fireEvent.change(searchInput, { target: { value: "prompt_slug" } });
      await waitFor(() => expect(fetchPromptPage).toHaveBeenCalledTimes(2));

      expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
        expect.objectContaining({ source: expectedAttributeSource }),
      );
      expect(fetchPromptPage).toHaveBeenCalledTimes(2);
      document.body.removeChild(anchorEl);
    },
  );

  it("coalesces exact-search scroll and button gestures into one continuation", async () => {
    let resolveExactPage;
    const exactPage = new Promise((resolve) => {
      resolveExactPage = resolve;
    });
    const fetchNextPage = vi.fn();
    const fetchNextExactPage = vi.fn(() => exactPage);
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "prompt_slug_archive",
          name: "prompt_slug_archive",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "retained:recent-cursor-2",
      isFetchingNextPage: false,
      fetchNextExactPage,
      hasNextExactPage: true,
      isFetchingExactSearch: false,
      isFetchingNextExactPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      exactSearchMatched: false,
      cursorRetryExhausted: false,
      debouncedSearch: "prompt_slug",
      refetch: vi.fn(),
    });
    const { anchorEl } = renderPanel({
      properties: [],
      projectId: "project-coletia",
      source: "traces",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "prompt_slug" },
    });
    const propertyList = document.querySelector(
      "[data-filter-property-options-list]",
    );
    Object.defineProperties(propertyList, {
      scrollTop: { configurable: true, value: 200 },
      clientHeight: { configurable: true, value: 200 },
      scrollHeight: { configurable: true, value: 400 },
    });

    fireEvent.scroll(propertyList);
    fireEvent.scroll(propertyList);
    triggerPropertyPageIntersection();

    expect(fetchNextExactPage).toHaveBeenCalledOnce();
    expect(fetchNextPage).not.toHaveBeenCalled();

    await act(async () => {
      resolveExactPage();
      await exactPage;
    });
    document.body.removeChild(anchorEl);
  });

  it("keeps properties beyond the first 500 browseable and selectable", () => {
    exactAttributePropertiesMock.mockReturnValue({
      data: Array.from({ length: 510 }, (_, index) => ({
        id: `retained_${index}`,
        name: `retained_${index}`,
        category: "attribute",
        rawCategory: "custom_attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      })),
      isFetching: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "exhausted",
      pageCount: 51,
      debouncedSearch: "",
    });
    const { anchorEl } = renderPanel({ properties: [] });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(
      document.querySelector('[data-filter-property-option="retained_499"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector('[data-filter-property-option="retained_500"]'),
    ).not.toBeInTheDocument();

    const propertyList = document.querySelector(
      "[data-filter-property-options-list]",
    );
    Object.defineProperties(propertyList, {
      scrollTop: { configurable: true, value: 280 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 500 },
    });
    fireEvent.scroll(propertyList);
    const finalLoadedProperty = document.querySelector(
      '[data-filter-property-option="retained_509"]',
    );
    expect(finalLoadedProperty).toBeInTheDocument();
    fireEvent.click(finalLoadedProperty);
    expect(
      screen.getByRole("button", { name: /retained_509/i }),
    ).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("advances one server cursor page when the list reaches its end", () => {
    const fetchNextPage = vi.fn();
    exactAttributePropertiesMock.mockImplementation(() => ({
      data: Array.from({ length: 20 }, (_, index) => ({
        id: `recent_${index}`,
        name: `recent_${index}`,
        category: "attribute",
        rawCategory: "custom_attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      })),
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "retained:short-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      debouncedSearch: "",
    }));
    const { anchorEl } = renderPanel({ properties: [] });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    const propertyList = document.querySelector(
      "[data-filter-property-options-list]",
    );
    expect(propertyList).toBeTruthy();
    Object.defineProperties(propertyList, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });
    fireEvent.scroll(propertyList);
    fireEvent.scroll(propertyList);
    fireEvent.scroll(propertyList);
    triggerPropertyPageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();

    // Momentum/layout scroll events after the page request must remain inert.
    fireEvent.scroll(propertyList);
    fireEvent.scroll(propertyList);
    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("loads a short attribute page when its end sentinel is visible", () => {
    const fetchNextPage = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "recent_attribute",
          name: "recent_attribute",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "retained:short-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      debouncedSearch: "",
    });
    const { anchorEl } = renderPanel({ properties: [] });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    triggerPropertyPageIntersection();

    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("keeps a failed continuation retryable instead of silently breaking", () => {
    const fetchNextPage = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "retained_attribute",
          name: "retained_attribute",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage,
      hasNextPage: true,
      isFetchingNextPage: false,
      isFetchNextPageError: true,
      queryReadState: "degraded",
      cursorRetryExhausted: false,
      debouncedSearch: "",
    });
    const { anchorEl } = renderPanel({ properties: [] });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(
      screen.getByText("More attributes could not be loaded. Please retry."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry attribute suggestions" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading attributes" }),
    );

    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("shows a retryable exact-search error without hiding retained partial matches", () => {
    const fetchNextExactPage = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "prompt_slug_archive",
          name: "prompt_slug_archive",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage: vi.fn(),
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextExactPage,
      // A failed exact continuation is demoted from normal pagination. The
      // dedicated sanitized Retry must still advance that exact lane once.
      hasNextExactPage: false,
      isFetchingExactSearch: false,
      isFetchingNextExactPage: false,
      isFetchNextPageError: false,
      exactSearchError: new Error("sanitized by picker"),
      queryReadState: "complete",
      browseStatus: "exhausted",
      pageCount: 1,
      exactSearchMatched: false,
      cursorRetryExhausted: false,
      debouncedSearch: "prompt",
      refetch: vi.fn(),
    });
    const { anchorEl } = renderPanel({
      properties: [],
      projectId: "project-whatfix",
      source: "traces",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "prompt" },
    });

    expect(screen.getByText("prompt_slug_archive")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Exact attribute search could not be completed. Retained matches remain available.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry exact attribute search" }),
    );
    expect(fetchNextExactPage).toHaveBeenCalledOnce();
    expect(screen.queryByText("sanitized by picker")).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("offers a sanitized retry when the initial attribute read is unavailable", () => {
    const refetch = vi.fn();
    exactAttributePropertiesMock.mockReturnValue({
      data: [],
      isFetching: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "error",
      browseStatus: undefined,
      pageCount: 0,
      debouncedSearch: "",
      refetch,
    });
    const { anchorEl } = renderPanel({
      projectId: "project-synthetic",
      properties: [],
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    expect(
      screen.getByText(
        "Attribute suggestions are temporarily unavailable. Enter an exact attribute name.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry attribute suggestions" }),
    );

    expect(refetch).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("terminalizes a repeated cursor and preserves manual exact attribute entry", () => {
    exactAttributePropertiesMock.mockReturnValue({
      data: [
        {
          id: "recent_attribute",
          name: "recent_attribute",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "degraded",
      browseStatus: "continuation",
      pageCount: 2,
      exactSearchMatched: false,
      cursorRetryExhausted: true,
      debouncedSearch: "final_status",
      refetch: vi.fn(),
    });
    const { anchorEl } = renderPanel({
      projectId: "project-synthetic",
      properties: [],
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.change(screen.getByPlaceholderText("Search properties..."), {
      target: { value: "final_status" },
    });

    expect(
      screen.queryByRole("button", { name: "Retry attribute suggestions" }),
    ).not.toBeInTheDocument();
    const manualOption = document.querySelector(
      "[data-filter-property-manual-exact]",
    );
    expect(manualOption).toHaveTextContent("final_status");
    fireEvent.click(manualOption);
    expect(
      screen.getByRole("button", { name: /final_status/i }),
    ).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("loads the next bounded catalog page through the unified control", async () => {
    const getSpy = vi.spyOn(axios, "get").mockImplementation((url, config) => {
      if (url !== endpoints.dashboard.metrics) {
        return Promise.resolve({ data: { result: {} } });
      }
      const page = config?.params?.page || 1;
      return Promise.resolve({
        data: {
          result: {
            metrics: [],
            page,
            page_size: config?.params?.page_size,
            has_more:
              config?.params?.category === "custom_attribute" && page === 1,
          },
        },
      });
    });

    const { anchorEl } = renderPanel({
      projectId: "project-whatfix",
      source: "traces",
    });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));

    await waitFor(() =>
      expect(getSpy).toHaveBeenCalledWith(
        endpoints.dashboard.metrics,
        expect.objectContaining({
          params: expect.objectContaining({
            category: "custom_attribute",
            source: "traces",
            project_ids: "project-whatfix",
            per_eval_config: true,
            page: 1,
            page_size: 20,
          }),
        }),
      ),
    );
    await waitFor(() =>
      expect(
        document.querySelector("[data-filter-property-page-sentinel]"),
      ).toBeInTheDocument(),
    );
    triggerPropertyPageIntersection();

    await waitFor(() =>
      expect(getSpy).toHaveBeenCalledWith(
        endpoints.dashboard.metrics,
        expect.objectContaining({
          params: expect.objectContaining({
            category: "custom_attribute",
            source: "traces",
            project_ids: "project-whatfix",
            per_eval_config: true,
            page: 2,
            page_size: 20,
          }),
        }),
      ),
    );
    await waitFor(() =>
      expect(
        document.querySelector("[data-filter-property-page-sentinel]"),
      ).not.toBeInTheDocument(),
    );

    document.body.removeChild(anchorEl);
  });

  it("hides unrelated catalog continuation for matched and attribute views", async () => {
    propertyCatalogMock.mockReturnValue({
      legacyFallbackRequired: false,
      metrics: [
        {
          name: "model",
          display_name: "Model",
          category: "system_metric",
          source: "traces",
          type: "string",
        },
      ],
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      hasNextPage: true,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      fetchNextPage: vi.fn(),
    });

    const { anchorEl } = renderPanel({
      projectId: "project-whatfix",
      source: "traces",
    });
    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    const search = screen.getByPlaceholderText("Search properties...");
    fireEvent.change(search, { target: { value: "model" } });

    await waitFor(() =>
      expect(
        document.querySelector('[data-filter-property-option="model"]'),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", {
        name: /eval and annotation properties/i,
      }),
    ).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "" } });
    fireEvent.click(screen.getByText("Attributes"));
    expect(
      screen.queryByRole("button", {
        name: /eval and annotation properties/i,
      }),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });
});

describe("filter-value picker bounded-read UX", () => {
  const statusProperty = {
    id: "call.status",
    name: "Status",
    category: "attribute",
    type: "string",
    apiColType: "SPAN_ATTRIBUTE",
  };
  const currentFilters = [
    {
      field: "call.status",
      fieldName: "Status",
      fieldCategory: "attribute",
      fieldType: "string",
      apiColType: "SPAN_ATTRIBUTE",
      operator: "in",
      value: [],
    },
  ];

  const openValuePicker = () => {
    const trigger = document.querySelector(
      '[data-filter-value-trigger="call.status"]',
    );
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger);
  };

  it("renders sampled recent values normally without incomplete-result copy", () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "completed", label: "completed" }],
      queryReadState: "sampled",
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();

    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Showing configured or recent suggestions only. Enter an exact value.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/results are incomplete/i),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("matches a trimmed catalog value to the selected filter value", () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [
        { value: "True ", label: "True", type: "string" },
        { value: "False", label: "False", type: "string" },
      ],
    });
    const annotationProperty = {
      id: "Anno-Tate",
      name: "Anno-Tate",
      category: "annotation",
      type: "string",
      apiColType: "ANNOTATION",
    };
    const { anchorEl } = renderPanel({
      currentFilters: [
        {
          field: "Anno-Tate",
          fieldName: "Anno-Tate",
          fieldCategory: "annotation",
          fieldType: "string",
          apiColType: "ANNOTATION",
          operator: "equals",
          value: ["True"],
          valueTypes: ["string"],
        },
      ],
      properties: [annotationProperty],
    });

    fireEvent.click(
      document.querySelector('[data-filter-value-trigger="Anno-Tate"]'),
    );

    const trueOption = document.querySelector(
      '[data-filter-value-option="True"]',
    );
    const falseOption = document.querySelector(
      '[data-filter-value-option="False"]',
    );
    expect(trueOption).toHaveAttribute("aria-checked", "true");
    expect(falseOption).toHaveAttribute("aria-checked", "false");

    document.body.removeChild(anchorEl);
  });

  it("offers Retry and exact free-text entry only for a real request error", () => {
    const refetch = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      isError: true,
      queryReadState: "error",
      refetch,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    expect(
      screen.getByText(
        "Suggestions are temporarily unavailable. Enter an exact value or retry.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();

    fireEvent.change(screen.getByPlaceholderText("Search values..."), {
      target: { value: "completed" },
    });
    expect(
      screen.getByText("completed", { selector: "strong" }),
    ).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("auto-advances an empty prompt_slug value checkpoint once", async () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [],
      browseStatus: "continuation",
      hasNextPage: true,
      continuationKey: "prompt-empty-cursor-2",
      fetchNextPage,
    });
    const promptSlugProperty = {
      id: "prompt_slug",
      name: "prompt_slug",
      category: "attribute",
      type: "string",
      attributeTypes: ["string"],
      attributeTypesExact: false,
      apiColType: "SPAN_ATTRIBUTE",
    };
    const { anchorEl, rerenderPanel } = renderPanel({
      currentFilters: [
        {
          field: "prompt_slug",
          fieldName: "prompt_slug",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "in",
          value: [],
        },
      ],
      properties: [promptSlugProperty],
      projectId: "project-coletia",
      source: "traces",
    });

    fireEvent.click(
      document.querySelector('[data-filter-value-trigger="prompt_slug"]'),
    );

    expect(fetchNextPage).not.toHaveBeenCalled();
    rerenderPanel();
    expect(fetchNextPage).not.toHaveBeenCalled();
    expect(
      screen.queryByText(/No retained values found/i),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "No values found yet. Continue searching or enter an exact value.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /continue searching values/i }),
    ).not.toBeInTheDocument();
    triggerValuePageIntersection();
    triggerValuePageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();

    document.body.removeChild(anchorEl);
  });

  it.each([
    ["tracing", undefined],
    ["voice", "voiceCalls"],
  ])(
    "lets %s repeat the same selected-property value search in one open picker",
    async (_surface, tab) => {
      const fetchRejectedPage = vi.fn();
      dashboardFilterValuesMock.mockImplementation((request) => ({
        ...defaultDashboardFilterValues(),
        data: request.search
          ? []
          : [{ value: "recent", label: "recent", type: "string" }],
        browseStatus: request.search ? "continuation" : "exhausted",
        hasNextPage: Boolean(request.search),
        continuationKey: request.search
          ? `search:${request.search}:cursor-2`
          : null,
        fetchNextPage:
          request.search === "rejected" ? fetchRejectedPage : vi.fn(),
      }));
      const promptSlugProperty = {
        id: "prompt_slug",
        name: "prompt_slug",
        category: "attribute",
        type: "string",
        attributeTypes: ["string"],
        attributeTypesExact: false,
        apiColType: "SPAN_ATTRIBUTE",
      };
      const { anchorEl } = renderPanel({
        currentFilters: [
          {
            field: "prompt_slug",
            fieldName: "prompt_slug",
            fieldCategory: "attribute",
            fieldType: "string",
            apiColType: "SPAN_ATTRIBUTE",
            operator: "in",
            value: [],
          },
        ],
        properties: [promptSlugProperty],
        projectId: `project-${_surface}`,
        source: "traces",
        tab,
      });

      fireEvent.click(
        document.querySelector('[data-filter-value-trigger="prompt_slug"]'),
      );
      const searchInput = screen.getByPlaceholderText("Search values...");
      const callsBeforeSearch = dashboardFilterValuesMock.mock.calls.length;
      fireEvent.change(searchInput, { target: { value: "rejected" } });
      await waitFor(
        () => {
          expect(
            dashboardFilterValuesMock.mock.calls
              .slice(callsBeforeSearch)
              .some(
                ([request]) =>
                  request.metricName === "prompt_slug" &&
                  request.search === "rejected",
              ),
          ).toBe(true);
        },
        { timeout: 1_500 },
      );
      expect(fetchRejectedPage).not.toHaveBeenCalled();
      triggerValuePageIntersection();
      await waitFor(() => expect(fetchRejectedPage).toHaveBeenCalledOnce());

      // Returning through the empty-search scope remounts the bounded cursor
      // lane. The same backend cursor may then be attempted once in the new
      // search session, but never twice inside either session.
      const callsBeforeClear = dashboardFilterValuesMock.mock.calls.length;
      fireEvent.change(searchInput, { target: { value: "" } });
      await waitFor(
        () => {
          expect(
            dashboardFilterValuesMock.mock.calls
              .slice(callsBeforeClear)
              .some(
                ([request]) =>
                  request.metricName === "prompt_slug" && request.search === "",
              ),
          ).toBe(true);
        },
        { timeout: 1_500 },
      );
      const callsBeforeReentry = dashboardFilterValuesMock.mock.calls.length;
      fireEvent.change(searchInput, { target: { value: "rejected" } });
      await waitFor(
        () => {
          expect(
            dashboardFilterValuesMock.mock.calls
              .slice(callsBeforeReentry)
              .some(
                ([request]) =>
                  request.metricName === "prompt_slug" &&
                  request.search === "rejected",
              ),
          ).toBe(true);
        },
        { timeout: 1_500 },
      );
      expect(fetchRejectedPage).toHaveBeenCalledOnce();
      triggerValuePageIntersection();
      triggerValuePageIntersection();
      await waitFor(() => expect(fetchRejectedPage).toHaveBeenCalledTimes(2));

      expect(fetchRejectedPage).toHaveBeenCalledTimes(2);
      expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
        expect.objectContaining({
          metricName: "prompt_slug",
          metricType: "custom_attribute",
          search: "rejected",
          source: "traces",
        }),
      );
      document.body.removeChild(anchorEl);
    },
  );

  it.each([
    [
      "tracing",
      undefined,
      "traces",
      "provider",
      "Provider",
      "provider",
      "system_attribute:traces:provider",
      undefined,
    ],
    [
      "voice",
      "voiceCalls",
      "traces",
      "ended_reason",
      "Ended Reason",
      "ended_reason",
      "system_attribute:voice_calls:ended_reason",
      undefined,
    ],
    [
      "session",
      undefined,
      "sessions",
      "session_id",
      "Session ID",
      "session",
      "system_attribute:sessions:session",
      undefined,
    ],
    [
      "users",
      undefined,
      "sessions",
      "user_id",
      "User ID",
      "user_id",
      "system_attribute:users:user_id",
      "users",
    ],
  ])(
    "sends %s Basic system-value search to the cursor backend",
    async (
      _surface,
      tab,
      source,
      propertyId,
      propertyName,
      expectedMetricName,
      expectedPropertyId,
      propertyNamespace,
    ) => {
      const property = {
        id: propertyId,
        name: propertyName,
        category: "system",
        type: "string",
        apiColType: "SYSTEM_METRIC",
      };
      const { anchorEl } = renderPanel({
        currentFilters: [
          {
            field: propertyId,
            fieldName: propertyName,
            fieldCategory: "system",
            fieldType: "string",
            apiColType: "SYSTEM_METRIC",
            operator: "in",
            value: [],
          },
        ],
        properties: [property],
        projectId: `project-system-${_surface}`,
        source,
        tab,
        propertyNamespace,
      });

      fireEvent.click(
        document.querySelector(`[data-filter-value-trigger="${propertyId}"]`),
      );
      fireEvent.change(screen.getByPlaceholderText("Search values..."), {
        target: { value: "needle" },
      });

      await waitFor(
        () =>
          expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
            expect.objectContaining({
              propertyId: expectedPropertyId,
              metricName: expectedMetricName,
              metricType: "system_metric",
              pageSize: FILTER_VALUE_PAGE_SIZE,
              search: "needle",
              searchGesture: "needle",
              source,
            }),
          ),
        { timeout: 1_500 },
      );
      document.body.removeChild(anchorEl);
    },
  );

  it.each([
    [
      "tracing annotation",
      undefined,
      "annotation",
      "annotation_metric",
      "quality-label",
      "Quality Label",
    ],
    [
      "tracing dynamic eval",
      undefined,
      "eval",
      "eval_metric",
      "trace-quality-eval",
      "Trace Quality Eval",
    ],
    [
      "voice annotation",
      "voiceCalls",
      "annotation",
      "annotation_metric",
      "voice-quality-label",
      "Voice Quality Label",
    ],
    [
      "voice dynamic eval",
      "voiceCalls",
      "eval",
      "eval_metric",
      "voice-quality-eval",
      "Voice Quality Eval",
    ],
  ])(
    "sends %s Basic value search to the cursor backend",
    async (_surface, tab, category, metricType, propertyId, propertyName) => {
      const property = {
        id: propertyId,
        name: propertyName,
        category,
        type: "string",
        apiColType: category === "annotation" ? "ANNOTATION" : "EVAL_METRIC",
      };
      const { anchorEl } = renderPanel({
        currentFilters: [
          {
            field: propertyId,
            fieldName: propertyName,
            fieldCategory: category,
            fieldType: "string",
            apiColType: property.apiColType,
            operator: "in",
            value: [],
          },
        ],
        properties: [property],
        projectId: `project-${_surface}`,
        source: "traces",
        tab,
      });

      fireEvent.click(
        document.querySelector(`[data-filter-value-trigger="${propertyId}"]`),
      );
      fireEvent.change(screen.getByPlaceholderText("Search values..."), {
        target: { value: "needle" },
      });

      await waitFor(
        () =>
          expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
            expect.objectContaining({
              metricName: propertyId,
              metricType,
              pageSize: FILTER_VALUE_PAGE_SIZE,
              search: "needle",
              searchGesture: "needle",
              source: "traces",
            }),
          ),
        { timeout: 1_500 },
      );
      document.body.removeChild(anchorEl);
    },
  );

  it("stops on a failed value continuation until the user retries", () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [],
      browseStatus: "continuation",
      hasNextPage: true,
      continuationKey: "failed-value-cursor",
      isFetchNextPageError: true,
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();

    expect(fetchNextPage).not.toHaveBeenCalled();
    expect(
      screen.getByText(
        "More values could not be loaded. Loaded values remain available.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Searching more values…"),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry searching values" }),
    );
    expect(fetchNextPage).toHaveBeenCalledOnce();

    document.body.removeChild(anchorEl);
  });

  it("retries a stopped Query value cursor through one fresh-chain action", async () => {
    const retryFreshPage = vi.fn(() => Promise.resolve());
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "retained", label: "retained", type: "string" }],
      queryReadState: "degraded",
      cursorChainStopped: true,
      retryFreshPage,
    });
    const { anchorEl } = renderPanel({
      properties: [statusProperty],
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    await selectQueryPhaseOption("Status", "pick operator...");
    await selectQueryPhaseOption("Contains", "type or pick value...");
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry loading values" }),
    );

    expect(retryFreshPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("loads each new short-page value continuation once while the end stays visible", async () => {
    const fetchNextPage = vi.fn().mockResolvedValue(undefined);
    let currentResult = {
      ...defaultDashboardFilterValues(),
      data: [{ value: "completed", label: "completed" }],
      browseStatus: "continuation",
      hasNextPage: true,
      continuationKey: "value-cursor-2",
      fetchNextPage,
    };
    dashboardFilterValuesMock.mockImplementation(() => currentResult);
    const { anchorEl, rerenderPanel } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();
    triggerValuePageIntersection();
    triggerValuePageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();
    await act(async () => {
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(
        screen.queryByText("Searching more values…"),
      ).not.toBeInTheDocument(),
    );

    currentResult = {
      ...currentResult,
      data: [
        { value: "completed", label: "completed" },
        { value: "failed", label: "failed" },
      ],
      continuationKey: "value-cursor-3",
    };
    rerenderPanel();
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchNextPage).toHaveBeenCalledOnce();

    triggerValuePageIntersection();
    await waitFor(() => expect(fetchNextPage).toHaveBeenCalledTimes(2));

    triggerValuePageIntersection();
    rerenderPanel();
    expect(fetchNextPage).toHaveBeenCalledTimes(2);

    document.body.removeChild(anchorEl);
  });

  it("does not paginate after terminal browse metadata proves exhaustion", () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "CONVERSATION", label: "CONVERSATION" }],
      // Model the dev failure: the last response is terminal, while a stale
      // continuation flag still says there is another page. Terminal browse
      // metadata must win.
      browseStatus: "exhausted",
      hasNextPage: true,
      continuationKey: "stale-value-cursor",
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    expect(screen.getByText("CONVERSATION")).toBeInTheDocument();
    expect(
      screen.queryByTestId("filter-value-pagination-sentinel"),
    ).not.toBeInTheDocument();
    expect(fetchNextPage).not.toHaveBeenCalled();

    document.body.removeChild(anchorEl);
  });

  it("auto-loads a resumable limit_reached continuation without manual UI", () => {
    const fetchNextPage = vi.fn(() => new Promise(() => {}));
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "CONVERSATION", label: "CONVERSATION" }],
      browseStatus: "limit_reached",
      hasNextPage: true,
      continuationKey: "resume-value-cursor",
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();
    triggerValuePageIntersection();
    triggerValuePageIntersection();
    expect(fetchNextPage).toHaveBeenCalledOnce();

    document.body.removeChild(anchorEl);
  });

  it("carries mixed option storage types into the applied filter row", async () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [
        { value: "1", label: "string one", type: "string" },
        { value: 1, label: "number one", type: "number" },
        { value: true, label: "boolean true", type: "boolean" },
      ],
    });
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
      onApply,
    });

    openValuePicker();
    fireEvent.click(screen.getByText("string one"));
    fireEvent.click(screen.getByText("number one"));
    fireEvent.click(screen.getByText("boolean true"));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const applied = onApply.mock.calls.at(-1)[0][0];
    expect(applied.value).toEqual(["1", 1, true]);
    expect(applied.valueTypes).toEqual(["string", "number", "boolean"]);

    document.body.removeChild(anchorEl);
  });

  it.each([
    ["normal", "manual-completed"],
    ["between 4 and 16 KiB", "x".repeat(FILTER_STRING_MAX_UTF8_BYTES + 1)],
    ["at 16 KiB", "é".repeat(TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES / 2)],
  ])(
    "preserves string provenance for a %s free-typed exact attribute value",
    async (_case, exactValue) => {
      const onApply = vi.fn();
      const { anchorEl } = renderPanel({
        currentFilters,
        properties: [statusProperty],
        onApply,
      });

      openValuePicker();
      fireEvent.change(screen.getByPlaceholderText("Search values..."), {
        target: { value: exactValue },
      });
      const exactOption = document.querySelector("[data-filter-value-option]");
      expect(exactOption).toHaveAttribute(
        "data-filter-value-option",
        exactValue,
      );
      fireEvent.click(exactOption);

      await waitFor(() => expect(onApply).toHaveBeenCalled());
      const applied = onApply.mock.calls.at(-1)[0][0];
      expect(applied).toMatchObject({
        field: "call.status",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        value: [exactValue],
        valueTypes: ["string"],
      });
      expect(buildApiFilterFromPanelRow(applied).filter_config).toMatchObject({
        filter_value: [exactValue],
        attribute_value_types: ["string"],
      });

      document.body.removeChild(anchorEl);
    },
  );

  it("blocks an oversized exact attribute value before search or apply", async () => {
    const oversizedValue = `${"é".repeat(
      TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES / 2,
    )}x`;
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
      onApply,
    });

    openValuePicker();
    const callsBeforeInput = dashboardFilterValuesMock.mock.calls.length;
    fireEvent.change(screen.getByPlaceholderText("Search values..."), {
      target: { value: oversizedValue },
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Exact values are limited to 16 KiB (16,384 UTF-8 bytes).",
    );
    expect(
      document.querySelector("[data-filter-value-option]"),
    ).not.toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();

    await act(() => new Promise((resolve) => setTimeout(resolve, 550)));
    const requestsAfterInput = dashboardFilterValuesMock.mock.calls
      .slice(callsBeforeInput)
      .map(([request]) => request);
    expect(
      requestsAfterInput.every(
        ({ search, searchGesture }) =>
          search !== oversizedValue && searchGesture !== oversizedValue,
      ),
    ).toBe(true);
    expect(onApply).not.toHaveBeenCalled();

    document.body.removeChild(anchorEl);
  });

  it("keeps Query-tab storage type and sends custom-attribute search", async () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [
        { value: "1", label: "string one", type: "string" },
        { value: 1, label: "number one", type: "number" },
      ],
    });
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      properties: [statusProperty],
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Status" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(input).toHaveAttribute("placeholder", "pick operator..."),
    );

    fireEvent.change(input, { target: { value: "equals" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(input).toHaveAttribute("placeholder", "type or pick value..."),
    );

    fireEvent.change(input, { target: { value: "number" } });
    await waitFor(
      () =>
        expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
          expect.objectContaining({
            metricName: "call.status",
            metricType: "custom_attribute",
            search: "number",
            pageSize: FILTER_VALUE_PAGE_SIZE,
          }),
        ),
      { timeout: 1_200 },
    );
    fireEvent.click(await screen.findByText("number one"));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0][0]).toMatchObject({
      field: "call.status",
      value: [1],
      valueTypes: ["number"],
    });
    document.body.removeChild(anchorEl);
  });

  it("keeps an existing Query-tab token active through edit and commit", async () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: 2, label: "2", type: "number" }],
    });
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters: [
        {
          field: "call.status",
          fieldName: "Status",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "contains",
          value: [1],
          valueTypes: ["number"],
        },
      ],
      properties: [statusProperty],
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    fireEvent.click(screen.getByText("Status contains 1"));

    const input = screen.getByRole("combobox");
    expect(input).toHaveValue("1");
    await waitFor(
      () =>
        expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
          expect.objectContaining({
            metricName: "call.status",
            search: "1",
          }),
        ),
      { timeout: 1_200 },
    );
    fireEvent.change(input, { target: { value: "2" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0][0]).toMatchObject({
      field: "call.status",
      value: [2],
      valueTypes: ["number"],
    });

    document.body.removeChild(anchorEl);
  });

  it("retains an off-page attribute registry identity through Query editing", async () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "new", label: "new", type: "string" }],
    });
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters: [
        {
          field: "rare.attribute",
          registryId: "custom_attribute:rare.attribute",
          fieldName: "Rare attribute",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "contains",
          value: ["old"],
          valueTypes: ["string"],
        },
      ],
      // Deliberately absent from the current catalog page: the selected row
      // itself is the only source of its stable identity.
      properties: [],
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    fireEvent.click(await screen.findByText("Rare attribute contains old"));
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "new" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0][0]).toMatchObject({
      field: "rare.attribute",
      registryId: "custom_attribute:rare.attribute",
      value: ["new"],
    });

    document.body.removeChild(anchorEl);
  });

  it("preserves existing scalar zero and false values with their storage types", async () => {
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters: [
        {
          field: "numeric_zero",
          fieldName: "Numeric zero",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "contains",
          value: 0,
          valueTypes: ["number"],
        },
        {
          field: "boolean_false",
          fieldName: "Boolean false",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "contains",
          value: false,
          valueTypes: ["boolean"],
        },
      ],
      properties: [
        {
          id: "numeric_zero",
          name: "Numeric zero",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
        {
          id: "boolean_false",
          name: "Boolean false",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const zeroToken = await screen.findByText("Numeric zero contains 0");
    expect(
      screen.getByText("Boolean false contains false"),
    ).toBeInTheDocument();

    fireEvent.click(zeroToken);
    const input = screen.getByRole("combobox");
    expect(input).toHaveValue("0");
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          field: "numeric_zero",
          value: [0],
          valueTypes: ["number"],
        }),
        expect.objectContaining({
          field: "boolean_false",
          value: [false],
          valueTypes: ["boolean"],
        }),
      ]),
    );

    document.body.removeChild(anchorEl);
  });

  it("preserves system, eval, annotation, and attribute filters as one mixed query", async () => {
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters: [
        {
          field: "status",
          registryId: "system_attribute:traces:status",
          fieldName: "Status",
          fieldCategory: "system",
          fieldType: "string",
          apiColType: "SYSTEM_METRIC",
          operator: "in",
          value: ["OK"],
        },
        {
          field: "quality-eval",
          registryId: "evaluation:quality-eval",
          fieldName: "Quality eval",
          fieldCategory: "eval",
          fieldType: "categorical",
          apiColType: "EVAL_METRIC",
          operator: "in",
          value: ["passed"],
        },
        {
          field: "annotator",
          registryId: "annotation:annotator",
          fieldName: "Annotator",
          fieldCategory: "annotation",
          fieldType: "annotator",
          apiColType: "ANNOTATION",
          operator: "is_not_null",
          value: [],
        },
        {
          field: "customer.plan",
          registryId: "custom_attribute:customer.plan",
          fieldName: "Customer plan",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "contains",
          value: ["enterprise"],
          valueTypes: ["string"],
        },
      ],
      properties: [
        {
          id: "status",
          registryId: "system_attribute:traces:status",
          name: "Status",
          category: "system",
          type: "string",
          apiColType: "SYSTEM_METRIC",
        },
        {
          id: "quality-eval",
          registryId: "evaluation:quality-eval",
          name: "Quality eval",
          category: "eval",
          type: "categorical",
          apiColType: "EVAL_METRIC",
        },
        {
          id: "annotator",
          registryId: "annotation:annotator",
          name: "Annotator",
          category: "annotation",
          type: "annotator",
          apiColType: "ANNOTATION",
        },
        {
          id: "customer.plan",
          registryId: "custom_attribute:customer.plan",
          name: "Customer plan",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    expect(screen.getByText("Status equals OK")).toBeInTheDocument();
    expect(screen.getByText("Quality eval is passed")).toBeInTheDocument();
    expect(screen.getByText("Annotator is not null")).toBeInTheDocument();
    const attributeToken = screen.getByText(
      "Customer plan contains enterprise",
    );

    // Re-commit one row through the real Query editor. The panel must retain
    // all four wire-qualified families in the single applied filter set.
    fireEvent.click(attributeToken);
    const input = screen.getByRole("combobox");
    expect(input).toHaveValue("enterprise");
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          field: "status",
          registryId: "system_attribute:traces:status",
          fieldCategory: "system",
          apiColType: "SYSTEM_METRIC",
          value: ["OK"],
        }),
        expect.objectContaining({
          field: "quality-eval",
          registryId: "evaluation:quality-eval",
          fieldCategory: "eval",
          apiColType: "EVAL_METRIC",
          value: ["passed"],
        }),
        expect.objectContaining({
          field: "annotator",
          registryId: "annotation:annotator",
          fieldCategory: "annotation",
          apiColType: "ANNOTATION",
          operator: "is_not_null",
        }),
        expect.objectContaining({
          field: "customer.plan",
          registryId: "custom_attribute:customer.plan",
          fieldCategory: "attribute",
          apiColType: "SPAN_ATTRIBUTE",
          value: ["enterprise"],
          valueTypes: ["string"],
        }),
      ]),
    );
    expect(onApply.mock.calls.at(-1)[0]).toHaveLength(4);

    document.body.removeChild(anchorEl);
  });

  it("keeps map values scalar while preserving array-valued text filters", async () => {
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      currentFilters: [
        {
          field: "metadata",
          fieldName: "Metadata",
          fieldCategory: "attribute",
          fieldType: "map",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "contains",
          value: { z: 3, region: "us" },
        },
        {
          field: "tags",
          fieldName: "Tags",
          fieldCategory: "attribute",
          fieldType: "string",
          apiColType: "SPAN_ATTRIBUTE",
          operator: "in",
          value: ["alpha", "beta"],
          valueTypes: ["string", "string"],
        },
      ],
      properties: [
        {
          id: "metadata",
          name: "Metadata",
          category: "attribute",
          type: "map",
          apiColType: "SPAN_ATTRIBUTE",
        },
        {
          id: "tags",
          name: "Tags",
          category: "attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const mapToken = await screen.findByText(
      'Metadata contains entries {"z":3,"region":"us"}',
    );
    expect(screen.getByText("Tags equals alpha – beta")).toBeInTheDocument();

    fireEvent.click(mapToken);
    const input = screen.getByRole("combobox");
    expect(input).toHaveValue('{"z":3,"region":"us"}');
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          field: "metadata",
          value: { region: "us", z: 3 },
        }),
        expect.objectContaining({
          field: "tags",
          value: ["alpha", "beta"],
          valueTypes: ["string", "string"],
        }),
      ]),
    );

    document.body.removeChild(anchorEl);
  });

  it("discovers and applies a rare exact attribute from the Query tab", async () => {
    const fetchNextAttributePage = vi.fn();
    exactAttributePropertiesMock.mockImplementation(
      ({ projectId, search, source, enabled }) => ({
        data:
          search === "final_status"
            ? [
                {
                  id: "final_status",
                  registryId: "custom_attribute:final_status",
                  name: "final_status",
                  category: "attribute",
                  rawCategory: "custom_attribute",
                  type: "string",
                  attributeTypes: ["string"],
                  attributeTypesExact: true,
                  apiColType: "SPAN_ATTRIBUTE",
                },
              ]
            : [],
        isFetching: false,
        fetchNextPage: fetchNextAttributePage,
        hasNextPage: false,
        isFetchingNextPage: false,
        isFetchNextPageError: false,
        queryReadState: "complete",
        browseStatus: "exhausted",
        pageCount: 1,
        exactSearchMatched: search === "final_status",
        cursorRetryExhausted: false,
        debouncedSearch: search,
        refetch: vi.fn(),
        projectId,
        source,
        enabled,
      }),
    );
    dashboardFilterValuesMock.mockImplementation((request) => ({
      ...defaultDashboardFilterValues(),
      data:
        request.metricName === "final_status"
          ? [{ value: "Rechazado", label: "Rechazado", type: "string" }]
          : [],
    }));
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({
      properties: [],
      projectId: "project-coletia",
      source: "traces",
      onApply,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const queryInput = await selectQueryPhaseOption(
      "final_status",
      "pick operator...",
    );
    fireEvent.change(queryInput, { target: { value: "equals" } });
    fireEvent.click(await screen.findByRole("option", { name: /^equals$/i }));
    await waitFor(() =>
      expect(queryInput).toHaveAttribute(
        "placeholder",
        "type or pick value...",
      ),
    );
    await selectQueryPhaseOption("Rechazado", "add filter...");

    expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-coletia",
        search: "final_status",
        source: "traces",
        enabled: true,
      }),
    );
    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "final_status",
        metricType: "custom_attribute",
        source: "traces",
        attributeType: "string",
        enabled: true,
      }),
    );
    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(onApply.mock.calls.at(-1)[0]).toEqual([
      expect.objectContaining({
        field: "final_status",
        registryId: "custom_attribute:final_status",
        fieldCategory: "attribute",
        fieldType: "string",
        apiColType: "SPAN_ATTRIBUTE",
        operator: "in",
        value: ["Rechazado"],
        valueTypes: ["string"],
      }),
    ]);

    document.body.removeChild(anchorEl);
  });

  it("searches Query-tab fields through the activated unified catalog", async () => {
    const fetchNextBasePage = vi.fn();
    const fetchNextSearchPage = vi.fn();
    exactAttributePropertiesMock.mockClear();
    propertyCatalogMock.mockImplementation(({ search = "" }) => ({
      metrics:
        search === "rare.query.attribute"
          ? [
              {
                property_id: "custom_attribute:rare.query.attribute",
                name: "rare.query.attribute",
                display_name: "rare.query.attribute",
                category: "custom_attribute",
                source: "traces",
                sources: ["traces"],
                type: "string",
              },
            ]
          : [],
      categoryCounts: {
        all: search ? 1 : 0,
        system_metric: 0,
        eval_metric: 0,
        annotation_metric: 0,
        custom_attribute: search ? 1 : 0,
        custom_column: 0,
      },
      categoryCountsExact: true,
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      hasNextPage: !search || search === "rare.query.attribute",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage:
        search === "rare.query.attribute"
          ? fetchNextSearchPage
          : fetchNextBasePage,
      data: { pages: [] },
    }));
    const { anchorEl } = renderPanel({
      projectId: "project-query-catalog",
      source: "traces",
      tab: "trace",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "rare.query.attribute" } });
    expect(screen.queryByText("Load more fields")).not.toBeInTheDocument();

    expect(await screen.findByText("rare.query.attribute")).toBeInTheDocument();
    expect(propertyCatalogMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectIds: ["project-query-catalog"],
        source: "traces",
        search: "rare.query.attribute",
        pageSize: 20,
        enabled: true,
      }),
    );
    expect(
      exactAttributePropertiesMock.mock.calls.some(
        ([request]) => request.enabled === true,
      ),
    ).toBe(false);
    await scrollQueryOptionsToEnd();
    expect(fetchNextSearchPage).toHaveBeenCalledOnce();
    expect(fetchNextBasePage).not.toHaveBeenCalled();

    document.body.removeChild(anchorEl);
  });

  it.each(GLOBAL_CATALOG_SEARCH_CASES)(
    "keeps omitted global $search visible in Query search",
    async ({ search, source, tab, initialMetrics }) => {
      propertyCatalogMock.mockImplementation(({ search: catalogSearch = "" }) =>
        settledPropertyCatalog({
          metrics: catalogSearch ? [] : initialMetrics,
        }),
      );
      const { anchorEl } = renderPanel({
        projectId: `project-query-global-${search.toLowerCase()}`,
        source,
        tab,
        showQueryTab: true,
      });

      fireEvent.click(screen.getByRole("tab", { name: "Query" }));
      const input = screen.getByRole("combobox");
      fireEvent.focus(input);
      fireEvent.change(input, { target: { value: search } });

      await waitFor(() =>
        expect(propertyCatalogMock).toHaveBeenCalledWith(
          expect.objectContaining({ search }),
        ),
      );
      expect(await screen.findByText(search)).toBeInTheDocument();

      document.body.removeChild(anchorEl);
    },
  );

  it("lets an authoritative Query search replace stale base fields without collapsing exact matches", async () => {
    propertyCatalogMock.mockImplementation(({ search = "" }) => ({
      metrics: search
        ? [
            {
              property_id: "system_attribute:voice_calls:cost_cents",
              name: "cost_cents",
              display_name: "Cost",
              category: "system_metric",
              source: "voice_calls",
              sources: ["voice_calls"],
              type: "number",
            },
            {
              property_id: "custom_attribute:cost_breakdown.analysisCost",
              name: "cost_breakdown.analysisCost",
              display_name: "cost_breakdown.analysisCost",
              category: "custom_attribute",
              source: "voice_calls",
              sources: ["voice_calls"],
              type: "number",
            },
          ]
        : [
            {
              property_id: "system_attribute:voice_calls:model",
              name: "model",
              display_name: "Model",
              category: "system_metric",
              source: "voice_calls",
              sources: ["voice_calls"],
              type: "string",
            },
          ],
      categoryCounts: {
        all: search ? 2 : 1,
        system_metric: 1,
        eval_metric: 0,
        annotation_metric: 0,
        custom_attribute: search ? 1 : 0,
        custom_column: 0,
      },
      categoryCountsExact: true,
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: vi.fn(),
      data: { pages: [] },
    }));
    const { anchorEl } = renderPanel({
      projectId: "project-query-authoritative-search",
      source: "voice_calls",
      tab: "voiceCalls",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    expect(await screen.findByText("Model")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "cost" } });

    expect(await screen.findByText("Cost")).toBeInTheDocument();
    expect(
      await screen.findByText("cost_breakdown.analysisCost"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Model")).not.toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("pages Query-tab browsing through the activated base catalog", async () => {
    const fetchNextBasePage = vi.fn();
    exactAttributePropertiesMock.mockClear();
    propertyCatalogMock.mockImplementation(({ search = "" }) => ({
      metrics: [
        {
          property_id: "system_attribute:traces:model",
          name: "model",
          display_name: "Model",
          category: "system_metric",
          source: "traces",
          sources: ["traces"],
          type: "string",
        },
      ],
      categoryCounts: {
        all: 2,
        system_metric: 1,
        eval_metric: 0,
        annotation_metric: 0,
        custom_attribute: 1,
        custom_column: 0,
      },
      categoryCountsExact: true,
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      hasNextPage: !search,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: !search ? fetchNextBasePage : vi.fn(),
      data: { pages: [] },
    }));
    const { anchorEl } = renderPanel({
      projectId: "project-query-base-catalog",
      source: "traces",
      tab: "trace",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    fireEvent.focus(screen.getByRole("combobox"));
    await screen.findByText("Model");
    await scrollQueryOptionsToEnd();

    expect(fetchNextBasePage).toHaveBeenCalledOnce();
    expect(
      exactAttributePropertiesMock.mock.calls.some(
        ([request]) => request.enabled === true,
      ),
    ).toBe(false);

    document.body.removeChild(anchorEl);
  });

  it("keeps Query-tab system values on the signed cursor route", async () => {
    const { anchorEl } = renderPanel({
      properties: [
        {
          id: "ended_reason",
          name: "Ended Reason",
          category: "system",
          type: "string",
          apiColType: "SYSTEM_METRIC",
        },
      ],
      projectId: "project-mudflap",
      source: "traces",
      tab: "voiceCalls",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = await selectQueryPhaseOption(
      "Ended Reason",
      "pick operator...",
    );

    await waitFor(() =>
      expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
        expect.objectContaining({
          propertyId: "system_attribute:voice_calls:ended_reason",
          metricName: "ended_reason",
          metricType: "system_metric",
          pageSize: FILTER_VALUE_PAGE_SIZE,
          source: "traces",
          enabled: true,
        }),
      ),
    );

    await selectQueryPhaseOption("contains", "type or pick value...");
    fireEvent.change(input, { target: { value: "customer" } });
    await waitFor(
      () =>
        expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
          expect.objectContaining({
            metricName: "ended_reason",
            metricType: "system_metric",
            pageSize: FILTER_VALUE_PAGE_SIZE,
            search: "customer",
            searchGesture: "customer",
            source: "traces",
          }),
        ),
      { timeout: 1_500 },
    );
    document.body.removeChild(anchorEl);
  });

  it("uses the Sessions adapter identity for Query-tab Session ID values", async () => {
    const { anchorEl } = renderPanel({
      properties: [
        {
          id: "session_id",
          name: "Session ID",
          category: "system",
          type: "string",
          apiColType: "SYSTEM_METRIC",
        },
      ],
      projectId: "project-sessions-query",
      source: "sessions",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    await selectQueryPhaseOption("Session ID", "pick operator...");

    await waitFor(() =>
      expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
        expect.objectContaining({
          propertyId: "system_attribute:sessions:session",
          metricName: "session",
          metricType: "system_metric",
          pageSize: FILTER_VALUE_PAGE_SIZE,
          source: "sessions",
          enabled: true,
        }),
      ),
    );

    document.body.removeChild(anchorEl);
  });

  it.each([
    [
      "tracing annotation",
      undefined,
      "annotation",
      "annotation_metric",
      "query-quality-label",
      "Query Quality Label",
    ],
    [
      "tracing dynamic eval",
      undefined,
      "eval",
      "eval_metric",
      "query-trace-eval",
      "Query Trace Eval",
    ],
    [
      "voice annotation",
      "voiceCalls",
      "annotation",
      "annotation_metric",
      "query-voice-label",
      "Query Voice Label",
    ],
    [
      "voice dynamic eval",
      "voiceCalls",
      "eval",
      "eval_metric",
      "query-voice-eval",
      "Query Voice Eval",
    ],
  ])(
    "sends %s Query value search to the cursor backend",
    async (_surface, tab, category, metricType, propertyId, propertyName) => {
      const property = {
        id: propertyId,
        name: propertyName,
        category,
        type: "string",
        apiColType: category === "annotation" ? "ANNOTATION" : "EVAL_METRIC",
      };
      const { anchorEl } = renderPanel({
        properties: [property],
        projectId: `project-query-${_surface}`,
        source: "traces",
        tab,
        showQueryTab: true,
      });

      fireEvent.click(screen.getByRole("tab", { name: "Query" }));
      const input = await selectQueryPhaseOption(
        propertyName,
        "pick operator...",
      );
      await selectQueryPhaseOption("contains", "type or pick value...");
      fireEvent.change(input, { target: { value: "needle" } });

      await waitFor(
        () =>
          expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
            expect.objectContaining({
              metricName: propertyId,
              metricType,
              pageSize: FILTER_VALUE_PAGE_SIZE,
              search: "needle",
              searchGesture: "needle",
              source: "traces",
              enabled: true,
            }),
          ),
        { timeout: 1_500 },
      );
      document.body.removeChild(anchorEl);
    },
  );

  it.each([
    ["tracing", undefined, "traces"],
    ["voice", "voiceCalls", "spans"],
  ])(
    "keeps %s Query-field pagination scroll-driven after an exact-prefix match",
    async (surface, tab, expectedAttributeSource) => {
      let hasNextPage = true;
      let data = [
        {
          id: "foo",
          name: "foo",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
        {
          id: "foo_archive",
          name: "foo_archive",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ];
      const fetchNextPage = vi.fn(async () => {
        data = [
          ...data,
          {
            id: "foo.bar",
            name: "foo.bar",
            category: "attribute",
            rawCategory: "custom_attribute",
            type: "string",
            apiColType: "SPAN_ATTRIBUTE",
          },
        ];
        hasNextPage = false;
      });
      exactAttributePropertiesMock.mockImplementation(({ search, source }) => ({
        data,
        isFetching: false,
        fetchNextPage,
        hasNextPage,
        isFetchingNextPage: false,
        fetchNextExactPage: vi.fn(),
        hasNextExactPage: false,
        isFetchingExactSearch: false,
        isFetchingNextExactPage: false,
        isFetchNextPageError: false,
        exactSearchError: null,
        queryReadState: "complete",
        browseStatus: hasNextPage ? "continuation" : "exhausted",
        pageCount: hasNextPage ? 1 : 2,
        exactSearchMatched: search === "foo",
        cursorRetryExhausted: false,
        debouncedSearch: search.trim(),
        refetch: vi.fn(),
        source,
      }));
      const { anchorEl, rerenderPanel } = renderPanel({
        properties: [],
        projectId: `project-query-${surface}`,
        source: "traces",
        tab,
        showQueryTab: true,
      });

      fireEvent.click(screen.getByRole("tab", { name: "Query" }));
      const input = screen.getByRole("combobox");
      fireEvent.focus(input);
      fireEvent.change(input, { target: { value: "foo" } });

      expect(await screen.findByText("foo")).toBeInTheDocument();
      expect(screen.getByText("foo_archive")).toBeInTheDocument();
      expect(exactAttributePropertiesMock).toHaveBeenCalledWith(
        expect.objectContaining({
          projectId: `project-query-${surface}`,
          search: "foo",
          source: expectedAttributeSource,
        }),
      );
      await scrollQueryOptionsToEnd();
      expect(fetchNextPage).toHaveBeenCalledOnce();
      rerenderPanel();

      expect(await screen.findByText("foo.bar")).toBeInTheDocument();
      expect(screen.queryByText("Load more fields")).not.toBeInTheDocument();
      document.body.removeChild(anchorEl);
    },
  );

  it.each([
    ["tracing", undefined],
    ["voice", "voiceCalls"],
  ])(
    "passes rapid %s Query-value re-entry separately from its settled request",
    async (_surface, tab) => {
      const promptSlugProperty = {
        id: "prompt_slug",
        name: "prompt_slug",
        category: "attribute",
        rawCategory: "custom_attribute",
        type: "string",
        attributeTypes: ["string"],
        attributeTypesExact: false,
        apiColType: "SPAN_ATTRIBUTE",
      };
      exactAttributePropertiesMock.mockImplementation(({ search }) => ({
        data: [promptSlugProperty],
        isFetching: false,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        fetchNextExactPage: vi.fn(),
        hasNextExactPage: false,
        isFetchingExactSearch: false,
        isFetchingNextExactPage: false,
        isFetchNextPageError: false,
        exactSearchError: null,
        queryReadState: "complete",
        browseStatus: "exhausted",
        pageCount: 1,
        exactSearchMatched: search === "prompt_slug",
        cursorRetryExhausted: false,
        debouncedSearch: search.trim(),
        refetch: vi.fn(),
      }));
      dashboardFilterValuesMock.mockImplementation((request) => ({
        ...defaultDashboardFilterValues(),
        data:
          request.metricName === "prompt_slug"
            ? [{ value: "Rejected-old", label: "Rejected-old" }]
            : [],
        hasNextPage:
          request.metricName === "prompt_slug" && request.search === "Rejected",
        isFetchNextPageError:
          request.metricName === "prompt_slug" && request.search === "Rejected",
      }));
      const { anchorEl } = renderPanel({
        properties: [promptSlugProperty],
        projectId: `project-${_surface}`,
        source: "traces",
        tab,
        showQueryTab: true,
      });

      fireEvent.click(screen.getByRole("tab", { name: "Query" }));
      const input = await selectQueryPhaseOption(
        "prompt_slug",
        "pick operator...",
      );
      fireEvent.change(input, { target: { value: "equals" } });
      fireEvent.click(await screen.findByRole("option", { name: /^equals$/i }));
      await waitFor(() =>
        expect(input).toHaveAttribute("placeholder", "type or pick value..."),
      );

      fireEvent.change(input, { target: { value: "Rejected" } });
      await waitFor(
        () =>
          expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
            expect.objectContaining({
              metricName: "prompt_slug",
              metricType: "custom_attribute",
              source: "traces",
              search: "Rejected",
              searchGesture: "Rejected",
            }),
          ),
        { timeout: 1_500 },
      );

      const callsBeforeRapidReentry =
        dashboardFilterValuesMock.mock.calls.length;
      // No wait between these changes: the debounced transport remains on the
      // cached failed `Rejected` query while raw gesture identity changes.
      fireEvent.change(input, { target: { value: "" } });
      fireEvent.change(input, { target: { value: "Rejected" } });

      await waitFor(() => {
        const rapidRequests = dashboardFilterValuesMock.mock.calls
          .slice(callsBeforeRapidReentry)
          .map(([request]) => request)
          .filter(({ metricName }) => metricName === "prompt_slug");
        expect(rapidRequests.map(({ searchGesture }) => searchGesture)).toEqual(
          expect.arrayContaining(["", "Rejected"]),
        );
        // Raw and settled identities are observably independent during this
        // rapid transition; the hook-level regression keeps `search` fixed to
        // the failed cached key and proves that this gesture retries it once.
        expect(
          rapidRequests.some(
            ({ search, searchGesture }) => search !== searchGesture,
          ),
        ).toBe(true);
        expect(rapidRequests.at(-1)).toEqual(
          expect.objectContaining({
            search: "Rejected",
            searchGesture: "Rejected",
          }),
        );
      });

      document.body.removeChild(anchorEl);
    },
  );

  it("keeps a cursor continuation distinct from the initial Query field load", async () => {
    propertyCatalogMock.mockReturnValue(settledPropertyCatalog());
    exactAttributePropertiesMock.mockReturnValue({
      data: [],
      isFetching: true,
      fetchNextPage: vi.fn(),
      hasNextPage: true,
      isFetchingNextPage: true,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount: 1,
      exactSearchMatched: false,
      cursorRetryExhausted: false,
      debouncedSearch: "",
      refetch: vi.fn(),
    });
    const { anchorEl } = renderPanel({
      properties: [],
      projectId: "project-coletia",
      source: "traces",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = screen.getByRole("combobox");
    expect(input).toHaveAttribute(
      "placeholder",
      "type to filter — e.g. field → operator → value",
    );
    expect(input).not.toHaveAttribute("placeholder", "loading fields...");

    fireEvent.focus(input);
    expect(
      screen.queryByText("Loading more fields..."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("keeps retained Query fields visible and labels an exact-search retry", async () => {
    const retryFieldSearch = vi.fn();
    exactAttributePropertiesMock.mockImplementation(({ search }) => ({
      data: [
        {
          id: "prompt_slug_archive",
          name: "prompt_slug_archive",
          category: "attribute",
          rawCategory: "custom_attribute",
          type: "string",
          apiColType: "SPAN_ATTRIBUTE",
        },
      ],
      isFetching: false,
      fetchNextPage: retryFieldSearch,
      hasNextPage: Boolean(search),
      isFetchingNextPage: false,
      isFetchNextPageError: Boolean(search),
      exactSearchError: search ? new Error("hidden backend detail") : null,
      queryReadState: "complete",
      browseStatus: search ? "continuation" : "exhausted",
      pageCount: 1,
      exactSearchMatched: false,
      cursorRetryExhausted: false,
      debouncedSearch: search.trim(),
      refetch: vi.fn(),
    }));
    const { anchorEl } = renderPanel({
      properties: [],
      projectId: "project-whatfix",
      source: "traces",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "prompt" } });

    expect(await screen.findByText("prompt_slug_archive")).toBeInTheDocument();
    expect(
      screen.getByText(
        "More fields could not be loaded. Retained matches remain available.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading fields" }),
    );
    expect(retryFieldSearch).toHaveBeenCalledOnce();
    expect(screen.queryByText("hidden backend detail")).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("labels and retries an initial Query-value failure", async () => {
    const refetch = vi.fn(() => Promise.resolve());
    dashboardFilterValuesMock.mockImplementation((request) => ({
      ...defaultDashboardFilterValues(),
      isError: Boolean(request.enabled),
      queryReadState: request.enabled ? "error" : "complete",
      refetch,
    }));
    const property = {
      id: "provider",
      name: "Provider",
      category: "system",
      type: "string",
      apiColType: "SYSTEM_METRIC",
    };
    const { anchorEl } = renderPanel({
      properties: [property],
      projectId: "project-query-retry",
      source: "traces",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    await selectQueryPhaseOption("Provider", "pick operator...");
    await selectQueryPhaseOption("contains", "type or pick value...");

    fireEvent.click(
      await screen.findByRole("button", { name: "Retry loading values" }),
    );
    expect(refetch).toHaveBeenCalledOnce();
    expect(
      screen.getByText(
        "Suggestions are temporarily unavailable. Enter an exact value or retry.",
      ),
    ).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("does not fetch or show an error for Query fields with static choices", async () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      isError: true,
      queryReadState: "error",
    });
    const fixedProperty = {
      id: "status",
      name: "Status",
      category: "system",
      type: "enum",
      choices: ["OK", "ERROR"],
    };
    const { anchorEl } = renderPanel({
      properties: [fixedProperty],
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    await selectQueryPhaseOption("Status", "pick operator...");

    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "status",
        enabled: false,
      }),
    );
    expect(
      screen.queryByText("Some results could not be loaded. Please try again."),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("never applies the previous field's search to a newly selected field", async () => {
    const properties = [
      {
        id: "alpha",
        name: "Alpha",
        category: "attribute",
        type: "string",
      },
      {
        id: "beta",
        name: "Beta",
        category: "attribute",
        type: "string",
      },
    ];
    const { anchorEl } = renderPanel({
      properties,
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = await selectQueryPhaseOption("Alpha", "pick operator...");
    await selectQueryPhaseOption("contains", "type or pick value...");
    fireEvent.change(input, { target: { value: "needle" } });
    await waitFor(
      () =>
        expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
          expect.objectContaining({ metricName: "alpha", search: "needle" }),
        ),
      { timeout: 1_200 },
    );
    fireEvent.keyDown(input, { key: "Enter" });

    await selectQueryPhaseOption("Beta", "pick operator...");
    await waitFor(() =>
      expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
        expect.objectContaining({ metricName: "beta", search: "" }),
      ),
    );
    const betaCalls = dashboardFilterValuesMock.mock.calls.filter(
      ([request]) => request.metricName === "beta",
    );
    expect(betaCalls.length).toBeGreaterThan(0);
    expect(betaCalls.every(([request]) => request.search !== "needle")).toBe(
      true,
    );

    document.body.removeChild(anchorEl);
  });

  it("resets partial Query state when the project changes", async () => {
    exactAttributePropertiesMock.mockImplementation(({ search = "" }) => ({
      ...defaultExactAttributeProperties(),
      data: [statusProperty],
      totalCount: 1,
      debouncedSearch: String(search).trim(),
    }));
    const { anchorEl, rerenderPanel } = renderPanel({
      properties: [statusProperty],
      projectId: "project-one",
      source: "traces",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    let input = await selectQueryPhaseOption("Status", "pick operator...");
    await selectQueryPhaseOption("contains", "type or pick value...");
    fireEvent.change(input, { target: { value: "project-one-value" } });
    await waitFor(
      () =>
        expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
          expect.objectContaining({
            projectIds: ["project-one"],
            metricName: "call.status",
            search: "project-one-value",
            enabled: true,
          }),
        ),
      { timeout: 1_500 },
    );

    const callsBeforeScopeChange = dashboardFilterValuesMock.mock.calls.length;
    rerenderPanel({ projectId: "project-two" });
    input = screen.getByRole("combobox");
    await waitFor(() =>
      expect(input).toHaveAttribute(
        "placeholder",
        "type to filter — e.g. field → operator → value",
      ),
    );
    await waitFor(() => {
      const projectTwoCalls = dashboardFilterValuesMock.mock.calls
        .slice(callsBeforeScopeChange)
        .map(([request]) => request)
        .filter(({ projectIds }) => projectIds?.[0] === "project-two");
      expect(projectTwoCalls.length).toBeGreaterThan(0);
      expect(
        projectTwoCalls.every(
          ({ enabled, metricName, search }) =>
            enabled === false && metricName === "" && search === "",
        ),
      ).toBe(true);
    });

    document.body.removeChild(anchorEl);
  });

  it("resets partial Query state when the catalog source changes", async () => {
    exactAttributePropertiesMock.mockImplementation(({ search = "" }) => ({
      ...defaultExactAttributeProperties(),
      data: [statusProperty],
      totalCount: 1,
      debouncedSearch: String(search).trim(),
    }));
    const { anchorEl, rerenderPanel } = renderPanel({
      properties: [statusProperty],
      projectId: "project-source-scope",
      source: "traces",
      showQueryTab: true,
    });

    fireEvent.click(screen.getByRole("tab", { name: "Query" }));
    const input = await selectQueryPhaseOption("Status", "pick operator...");
    await selectQueryPhaseOption("contains", "type or pick value...");
    fireEvent.change(input, { target: { value: "trace-source-value" } });
    await waitFor(
      () =>
        expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
          expect.objectContaining({
            projectIds: ["project-source-scope"],
            source: "traces",
            search: "trace-source-value",
            enabled: true,
          }),
        ),
      { timeout: 1_500 },
    );

    const callsBeforeScopeChange = dashboardFilterValuesMock.mock.calls.length;
    rerenderPanel({ source: "sessions" });
    const resetInput = screen.getByRole("combobox");
    await waitFor(() =>
      expect(resetInput).toHaveAttribute(
        "placeholder",
        "type to filter — e.g. field → operator → value",
      ),
    );
    await waitFor(() => {
      const sessionCalls = dashboardFilterValuesMock.mock.calls
        .slice(callsBeforeScopeChange)
        .map(([request]) => request)
        .filter(({ source }) => source === "sessions");
      expect(sessionCalls.length).toBeGreaterThan(0);
      expect(
        sessionCalls.every(
          ({ enabled, metricName, search }) =>
            enabled === false && metricName === "" && search === "",
        ),
      ).toBe(true);
    });

    document.body.removeChild(anchorEl);
  });

  it("explains a truthful terminal recent-value cap without incomplete copy", () => {
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "completed", label: "completed", type: "string" }],
      browseLimitReached: true,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    expect(
      screen.getByText(
        "Recent value limit reached. Search or enter an exact value.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/incomplete/i)).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("does not pin mixed attributes to only their dominant storage type", () => {
    const mixedProperty = {
      ...statusProperty,
      attributeTypes: ["string", "number"],
    };
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [mixedProperty],
    });

    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "call.status",
        metricType: "custom_attribute",
        attributeType: undefined,
      }),
    );

    document.body.removeChild(anchorEl);
  });

  it("does not pin a bounded singleton type hint", () => {
    const boundedProperty = {
      ...statusProperty,
      attributeTypes: ["string"],
      attributeTypesExact: false,
    };
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [boundedProperty],
    });

    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "call.status",
        metricType: "custom_attribute",
        attributeType: undefined,
      }),
    );

    document.body.removeChild(anchorEl);
  });

  it("pins a server-certified singleton type", () => {
    const exactProperty = {
      ...statusProperty,
      attributeTypes: ["string"],
      attributeTypesExact: true,
    };
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [exactProperty],
    });

    expect(dashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "call.status",
        metricType: "custom_attribute",
        attributeType: "string",
      }),
    );

    document.body.removeChild(anchorEl);
  });
});

describe("toStaticFilterProperty (spans Span Name)", () => {
  const nameField = { value: "name", label: "Trace Name", type: "string" };

  it("remaps the name field to span_name in spans view", () => {
    expect(toStaticFilterProperty(nameField, true)).toMatchObject({
      id: "span_name",
      name: "Span Name",
      type: "string",
    });
  });

  it("keeps the name field as name outside spans view", () => {
    expect(toStaticFilterProperty(nameField, false)).toMatchObject({
      id: "name",
      name: "Trace Name",
    });
  });

  it("does not remap non-name fields in spans view", () => {
    const field = { value: "status", label: "Status", type: "string" };
    expect(toStaticFilterProperty(field, true).id).toBe("status");
  });
});

describe("normalizeFilterRowOperator", () => {
  it("maps list operators to canonical equality panel operators before apply", () => {
    expect(
      normalizeFilterRowOperator({
        field: "status",
        fieldType: "categorical",
        operator: "in",
        value: ["OK"],
      }).operator,
    ).toBe("equals");

    expect(
      normalizeFilterRowOperator({
        field: "status",
        fieldType: "categorical",
        operator: "not_in",
        value: ["ERROR"],
      }).operator,
    ).toBe("not_equals");
  });

  it("keeps canonical number and date ops", () => {
    expect(
      normalizeFilterRowOperator({
        field: "latency_ms",
        fieldType: "number",
        operator: "equals",
        value: "100",
      }).operator,
    ).toBe("equals");

    expect(
      normalizeFilterRowOperator({
        field: "created_at",
        fieldType: "date",
        operator: "less_than",
        value: "2026-05-09T00:00",
      }).operator,
    ).toBe("less_than");
  });

  it("falls back to exact multi-select operators for restricted id fields", () => {
    expect(
      normalizeFilterRowOperator({
        field: "trace_id",
        fieldType: "string",
        operator: "contains",
        value: "abc",
      }).operator,
    ).toBe("in");

    expect(
      normalizeFilterRowOperator({
        field: "span_id",
        fieldType: "string",
        operator: "contains",
        value: "abc",
      }).operator,
    ).toBe("in");
  });

  it("keeps canonical annotation equality operators for the restricted annotator operator", () => {
    expect(
      normalizeFilterRowOperator({
        field: "annotator",
        fieldType: "annotator",
        operator: "equals",
        value: ["user-a", "user-b"],
      }).operator,
    ).toBe("equals");
  });

  it("preserves no-value operators for eval and annotation filter rows", () => {
    for (const fieldType of ["categorical", "thumbs", "annotator", "date"]) {
      expect(
        normalizeFilterRowOperator({
          field: `${fieldType}-field`,
          fieldType,
          operator: "is_null",
          value: "",
        }).operator,
      ).toBe("is_null");
    }
  });
});

describe("annotator annotation filter (TH-4710)", () => {
  it("does not show ended_reason for unrelated property search text (TH-5149)", () => {
    const properties = [
      {
        id: "ended_reason",
        name: "Ended Reason",
        category: "attribute",
        type: "string",
      },
      {
        id: "status",
        name: "Status",
        category: "system",
        type: "string",
      },
    ];

    expect(
      filterPropertiesForPicker({
        properties,
        search: "xqz-not-a-match",
      }),
    ).toEqual([]);
    expect(
      filterPropertiesForPicker({
        properties,
        search: "ended reason",
      }),
    ).toEqual([properties[0]]);
  });

  it("only exposes span-owned metrics when building span filter properties", () => {
    const metrics = [
      {
        name: "latency",
        display_name: "Latency",
        category: "system_metric",
        source: "traces",
        type: "number",
      },
      {
        name: "latency_ms",
        display_name: "Duration",
        category: "system_metric",
        source: "spans",
        sources: ["spans"],
        type: "number",
      },
    ];

    expect(
      buildTraceFilterProperties(metrics, { sourceScope: "traces" }).some(
        (property) => property.id === "latency_ms",
      ),
    ).toBe(false);

    expect(
      buildTraceFilterProperties(metrics, { sourceScope: "spans" }),
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "latency_ms",
          name: "Duration",
          type: "number",
        }),
      ]),
    );
  });

  it("preserves certified mixed attribute types from the unified catalog", () => {
    const [property] = buildTraceFilterProperties(
      [
        {
          name: "mixed.status",
          display_name: "Mixed status",
          property_id: "custom_attribute:mixed.status",
          category: "custom_attribute",
          source: "traces",
          sources: ["traces"],
          type: "json",
          attribute_types: ["string", "number"],
          attribute_types_exact: true,
        },
      ],
      { sourceScope: "traces" },
    );

    expect(property).toEqual(
      expect.objectContaining({
        id: "mixed.status",
        registryId: "custom_attribute:mixed.status",
        attributeTypes: ["string", "number"],
        attributeTypesExact: true,
      }),
    );
  });

  it("combines voice-call system fields with trace-derived attributes", () => {
    const metrics = [
      {
        name: "ended_reason",
        display_name: "Ended Reason",
        category: "system_metric",
        source: "voice_calls",
        sources: ["system", "voice_calls", "ended_reason"],
        type: "string",
      },
      {
        name: "customer.plan",
        display_name: "customer.plan",
        category: "custom_attribute",
        source: "traces",
        sources: ["attribute", "span", "traces", "string"],
        type: "string",
      },
      {
        name: "latency",
        display_name: "Latency",
        category: "system_metric",
        source: "traces",
        sources: ["system", "traces", "latency"],
        type: "number",
      },
    ];

    expect(
      buildTraceFilterProperties(metrics, { sourceScope: "voice_calls" }),
    ).toEqual([
      expect.objectContaining({
        id: "ended_reason",
        registryId: "system_attribute:voice_calls:ended_reason",
      }),
      expect.objectContaining({
        id: "customer.plan",
        registryId: "custom_attribute:customer.plan",
      }),
    ]);
  });

  it("does not expose simulation properties on a trace-source picker", () => {
    const metrics = [
      {
        name: "agent_definition",
        display_name: "Agent",
        category: "system_metric",
        source: "simulation",
        sources: ["simulation"],
        type: "string",
      },
      {
        name: "model",
        display_name: "Model",
        category: "system_metric",
        source: "traces",
        sources: ["traces"],
        type: "string",
      },
    ];

    expect(
      buildTraceFilterProperties(metrics, {
        isSimulator: true,
        sourceScope: "traces",
      }),
    ).toEqual([
      expect.objectContaining({
        id: "model",
        registryId: "system_attribute:traces:model",
      }),
    ]);
  });

  it("adds a global Annotator property inside annotation filters", () => {
    const properties = buildTraceFilterProperties([
      {
        name: "latency",
        display_name: "Latency",
        category: "system_metric",
        source: "traces",
        type: "number",
      },
      {
        name: "label-1",
        display_name: "Quality",
        category: "annotation_metric",
        source: "both",
        output_type: "numeric",
      },
    ]);

    const annotator = properties.find(
      (property) => property.id === "annotator",
    );
    expect(annotator).toMatchObject({
      name: "Annotator",
      category: "annotation",
      type: "annotator",
      apiColType: "SYSTEM_METRIC",
      allowCustomValue: false,
    });

    const annotatorIndex = properties.findIndex(
      (property) => property.id === "annotator",
    );
    const labelIndex = properties.findIndex(
      (property) => property.id === "label-1",
    );
    expect(annotatorIndex).toBeLessThan(labelIndex);
  });

  it.each(["traces", "spans", "voice_calls", "sessions"])(
    "keeps the global Annotator property when the first %s catalog page has no annotation definitions",
    (sourceScope) => {
      expect(
        buildTraceFilterProperties([], {
          sourceScope,
          includeGlobalAnnotator: true,
        }),
      ).toEqual([
        expect.objectContaining({
          id: "annotator",
          registryId: "annotation:annotator",
          category: "annotation",
          apiColType: "SYSTEM_METRIC",
          catalogSearchFallback: true,
        }),
      ]);
    },
  );

  it.each(["dataset", "simulation"])(
    "does not inject Annotator into the %s catalog",
    (sourceScope) => {
      expect(
        buildTraceFilterProperties([], {
          sourceScope,
          includeGlobalAnnotator: true,
        }),
      ).toEqual([]);
    },
  );

  it("maps every annotation label output type to the matching filter input type", () => {
    const properties = buildTraceFilterProperties([
      {
        name: "numeric-label",
        display_name: "Numeric",
        category: "annotation_metric",
        source: "both",
        output_type: "numeric",
      },
      {
        name: "star-label",
        display_name: "Star",
        category: "annotation_metric",
        source: "both",
        output_type: "star",
      },
      {
        name: "text-label",
        display_name: "Text",
        category: "annotation_metric",
        source: "both",
        output_type: "text",
      },
      {
        name: "thumbs-label",
        display_name: "Thumbs",
        category: "annotation_metric",
        source: "both",
        output_type: "thumbs_up_down",
      },
      {
        name: "category-label",
        display_name: "Category",
        category: "annotation_metric",
        source: "both",
        output_type: "categorical",
        choices: ["refund", "billing"],
      },
    ]);

    expect(properties).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "numeric-label", type: "number" }),
        expect.objectContaining({ id: "star-label", type: "number" }),
        expect.objectContaining({ id: "text-label", type: "text" }),
        expect.objectContaining({
          id: "thumbs-label",
          type: "thumbs",
          choices: ["Thumbs Up", "Thumbs Down"],
        }),
        expect.objectContaining({
          id: "category-label",
          type: "categorical",
          choices: ["refund", "billing"],
        }),
      ]),
    );
  });

  it("uses annotator email as secondary display text and searchable text", () => {
    const option = {
      value: "user-1",
      label: "Kartik",
      name: "Kartik",
      email: "kartik.nvj@futureagi.com",
      description: "kartik.nvj@futureagi.com",
    };

    expect(getPickerOptionSecondaryLabel(option)).toBe(
      "kartik.nvj@futureagi.com",
    );
    expect(getPickerOptionSearchText(option)).toContain("Kartik");
    expect(getPickerOptionSearchText(option)).toContain(
      "kartik.nvj@futureagi.com",
    );
    expect(
      getPickerOptionSecondaryLabel({
        value: "user-2",
        label: "reviewer@futureagi.com",
        email: "reviewer@futureagi.com",
      }),
    ).toBe("");
  });
});
