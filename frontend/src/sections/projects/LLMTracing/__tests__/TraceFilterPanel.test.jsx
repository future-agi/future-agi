import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import TraceFilterPanel, {
  buildManualAttributeProperty,
  buildQueryPropertyEntries,
  buildTraceFilterProperties,
  filterPropertiesForPicker,
  findTraceFilterProperty,
  getTraceFilterFields,
  mergeTraceFilterProperties,
  mergeRetainedAttributeProperties,
  normalizeFilterRowOperator,
  shouldUseRetainedAttributePages,
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

const defaultDashboardFilterValues = () => ({
  data: [],
  isLoading: false,
  isError: false,
  queryReadState: "complete",
  fetchNextPage: vi.fn(),
  hasNextPage: false,
  isFetchingNextPage: false,
  refetch: vi.fn(),
});

beforeEach(() => {
  dashboardFilterValuesMock.mockReturnValue(defaultDashboardFilterValues());
  exactAttributePropertiesMock.mockReturnValue({
    data: [],
    isFetching: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    isFetchNextPageError: false,
    queryReadState: "complete",
    browseStatus: "exhausted",
    pageCount: 1,
    exactSearchMatched: false,
    cursorRetryExhausted: false,
    debouncedSearch: "",
    refetch: vi.fn(),
  });
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

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({
    parseQuery: parseQueryMock,
    loading: false,
    error: null,
  }),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: dashboardFilterValuesMock,
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
}) {
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const panel = () => (
    <QueryClientProvider client={queryClient}>
      <TraceFilterPanel
        anchorEl={anchorEl}
        open={open}
        onClose={onClose}
        onApply={onApply}
        currentFilters={[...currentFilters]}
        properties={properties}
        showQueryTab={showQueryTab}
        projectId={projectId}
        source={source}
      />
    </QueryClientProvider>
  );
  const utils = render(panel());
  return {
    anchorEl,
    onApply,
    onClose,
    ...utils,
    rerenderPanel: () => utils.rerender(panel()),
  };
}

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

describe("voice-call property search aliases", () => {
  const properties = getTraceFilterFields("voiceCalls").map((field) =>
    toStaticFilterProperty(field),
  );

  it("finds the displayed cost field by its Live Preview response key", () => {
    expect(
      filterPropertiesForPicker({ properties, search: "cost_cents" }),
    ).toEqual([
      expect.objectContaining({
        id: "cost_cents",
        name: "Cost (cents)",
        apiColType: "SYSTEM_METRIC",
      }),
    ]);
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

  it("finds the provider Call ID globally even after browsing Attributes", () => {
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
        id: "call_id",
        name: "Call ID",
        category: "system",
        apiColType: "SYSTEM_METRIC",
      }),
    ]);
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

  it("keeps raw call_id discovery reachable beside the canonical system id", () => {
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
    ).not.toBeInTheDocument();
    const loadMore = screen.getByRole("button", {
      name: "Load more attributes",
    });
    fireEvent.click(loadMore);
    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("keeps loading for the Call ID label until an older raw Call ID key is certified", () => {
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
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
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
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
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
      screen.getByRole("button", { name: "Load more attributes" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("terminates only after the backend certifies the exact raw trace.id key", () => {
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
      // Deliberately adversarial: the UI must use backend certification rather
      // than punctuation-normalized display matching to suppress this flag.
      hasNextPage: true,
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
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
    expect(fetchNextPage).not.toHaveBeenCalled();
    document.body.removeChild(anchorEl);
  });

  it("resets a browsed category when property search starts", () => {
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
        pageSize: 10,
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

    fireEvent.click(
      screen.getByRole("button", { name: "Show 10 more properties" }),
    );
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

  it("loads one cursor page per natural downward gesture without draining or requiring an up-scroll", () => {
    const fetchNextPage = vi.fn();
    let attributeCount = 10;
    let pageCount = 1;
    exactAttributePropertiesMock.mockImplementation(() => ({
      data: Array.from({ length: attributeCount }, (_, index) => ({
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
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      browseStatus: "continuation",
      pageCount,
      debouncedSearch: "",
    }));
    const { anchorEl, rerenderPanel } = renderPanel({ properties: [] });

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

    expect(fetchNextPage).toHaveBeenCalledOnce();

    // A successful page adds rows. The next downward gesture may advance from
    // the new bottom directly; no artificial up-scroll is needed.
    attributeCount = 20;
    pageCount = 2;
    rerenderPanel();
    Object.defineProperty(propertyList, "scrollTop", {
      configurable: true,
      value: 380,
    });
    Object.defineProperty(propertyList, "scrollHeight", {
      configurable: true,
      value: 600,
    });
    fireEvent.scroll(propertyList);
    fireEvent.scroll(propertyList);
    expect(fetchNextPage).toHaveBeenCalledTimes(2);

    // An exact empty continuation still increments the page revision. It must
    // unlock one further gesture without auto-draining every remaining page.
    pageCount = 3;
    rerenderPanel();
    fireEvent.scroll(propertyList);
    fireEvent.scroll(propertyList);
    expect(fetchNextPage).toHaveBeenCalledTimes(3);
    expect(
      screen.queryByText(/results are incomplete/i),
    ).not.toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("offers an explicit fallback when attribute scrolling cannot advance", () => {
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
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      debouncedSearch: "",
    });
    const { anchorEl } = renderPanel({ properties: [] });

    fireEvent.click(screen.getByRole("button", { name: "Property" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );

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
      screen.getByText("Recent values — search or enter an exact value."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/results are incomplete/i),
    ).not.toBeInTheDocument();

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

  it("loads the next value page when the options list reaches the bottom", () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "completed", label: "completed" }],
      hasNextPage: true,
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    const optionsList = document.querySelector(
      "[data-filter-value-options-list]",
    );
    Object.defineProperties(optionsList, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });
    fireEvent.scroll(optionsList);

    expect(fetchNextPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("hides a stale Load more control after exhaustion proves no next value", () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "CONVERSATION", label: "CONVERSATION" }],
      // Model the dev failure: the last response is terminal, while a stale
      // continuation flag still says there is another page. Terminal browse
      // metadata must win.
      browseStatus: "exhausted",
      hasNextPage: true,
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    expect(screen.getByText("CONVERSATION")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();

    const optionsList = document.querySelector(
      "[data-filter-value-options-list]",
    );
    Object.defineProperties(optionsList, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });
    fireEvent.scroll(optionsList);
    expect(fetchNextPage).not.toHaveBeenCalled();

    document.body.removeChild(anchorEl);
  });

  it("keeps Load more actionable at a resumable limit_reached checkpoint", () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "CONVERSATION", label: "CONVERSATION" }],
      browseStatus: "limit_reached",
      hasNextPage: true,
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(fetchNextPage).toHaveBeenCalledOnce();

    document.body.removeChild(anchorEl);
  });

  it("does not drain every value cursor page from one bottom-scroll gesture", () => {
    const fetchNextPage = vi.fn();
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "completed", label: "completed" }],
      hasNextPage: true,
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    const optionsList = document.querySelector(
      "[data-filter-value-options-list]",
    );
    Object.defineProperties(optionsList, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });

    // Inertial scrolling can emit more bottom events after a fast page has
    // resolved. Only the first event may auto-advance this open picker.
    fireEvent.scroll(optionsList);
    fireEvent.scroll(optionsList);
    fireEvent.scroll(optionsList);
    expect(fetchNextPage).toHaveBeenCalledOnce();

    // Leaving the edge and deliberately returning is a new pagination
    // gesture, so scroll-to-load continues to work page by page.
    Object.defineProperty(optionsList, "scrollTop", {
      configurable: true,
      value: 80,
    });
    fireEvent.scroll(optionsList);
    Object.defineProperty(optionsList, "scrollTop", {
      configurable: true,
      value: 180,
    });
    fireEvent.scroll(optionsList);
    expect(fetchNextPage).toHaveBeenCalledTimes(2);

    // Exact continuation remains explicitly available; this is not a result
    // cap and does not hide later unique values.
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(fetchNextPage).toHaveBeenCalledTimes(3);

    document.body.removeChild(anchorEl);
  });

  it("coalesces scroll and Load more while the same value page is in flight", () => {
    const fetchNextPage = vi.fn(() => new Promise(() => {}));
    dashboardFilterValuesMock.mockReturnValue({
      ...defaultDashboardFilterValues(),
      data: [{ value: "completed", label: "completed" }],
      hasNextPage: true,
      fetchNextPage,
    });
    const { anchorEl } = renderPanel({
      currentFilters,
      properties: [statusProperty],
    });

    openValuePicker();
    const optionsList = document.querySelector(
      "[data-filter-value-options-list]",
    );
    Object.defineProperties(optionsList, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });

    fireEvent.scroll(optionsList);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

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
            pageSize: 10,
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
