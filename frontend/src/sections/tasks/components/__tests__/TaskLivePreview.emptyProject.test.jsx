import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import { render, screen, waitFor } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  readQuery: mocks.get,
  default: { get: mocks.get },
  endpoints: {
    project: {
      getCallLogs: "/calls/",
      getTracesForObserveProject: () => "/traces/",
      getSpansForObserveProject: () => "/spans/",
      projectSessionList: () => "/sessions/",
      getTrace: (id) => `/traces/${id}/`,
      getObservationSpan: (id) => `/span-details/${id}/`,
      getVoiceCallDetail: "/calls/detail/",
      traceSession: "/sessions/",
      projectExperimentDetail: (id) => `/projects/${id}/`,
    },
  },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));
vi.mock("src/components/inline-audio/inline-row-audio", () => ({
  InlineAudio: () => null,
  RecordingGroup: () => null,
}));

import TaskLivePreview from "../TaskLivePreview";

const PROJECT_ID = "00000000-0000-4000-8000-000000000111";
const FILTER_HINT = "Adjust filters to see matching data";
const NO_DATA_HINT =
  "This project has no data in this time window yet. Choose New incoming data to evaluate it as it arrives.";
const NO_DATA_CONTINUOUS_HINT =
  "This project has no data in this time window yet. New data will be evaluated as it arrives.";

const STATUS_FILTER = {
  id: "status",
  property: "status",
  propertyId: "status",
  fieldCategory: "system",
  apiColType: "SYSTEM_METRIC",
  filterConfig: {
    filterType: "text",
    filterOp: "equals",
    filterValue: "OK",
  },
};

// A filter row the user added but never completed is not a filter: it never
// reaches the list request, so it cannot be what emptied the preview.
const BLANK_FILTER = {
  id: "blank",
  property: "",
  filterConfig: { filterType: "text", filterOp: "equals" },
};

const EMPTY_SPAN_PAGE = {
  data: {
    status: true,
    result: {
      config: [],
      table: [],
      metadata: {
        total_rows: 0,
        total_rows_is_lower_bound: false,
        has_more: false,
        next_cursor: null,
      },
    },
  },
};

const SPAN_ROW = {
  span_id: "span-1",
  trace_id: "trace-1",
  project_id: PROJECT_ID,
  start_time: "2026-09-01T12:01:02.123456Z",
  observation_type: "SPAN",
  service_name: "test-service",
  _version: "1",
};

const SPAN_PAGE_WITH_ROW = {
  data: {
    status: true,
    result: {
      config: [],
      table: [SPAN_ROW],
      metadata: {
        total_rows: 1,
        total_rows_is_lower_bound: false,
        has_more: false,
        next_cursor: null,
      },
    },
  },
};

function PreviewHarness({ filters, runType, onTestStateChange }) {
  const { control } = useForm({
    defaultValues: {
      filters,
      startDate: null,
      endDate: null,
      evalsDetails: [],
      rowType: "spans",
      runType,
    },
  });
  return (
    <TaskLivePreview
      control={control}
      projectId={PROJECT_ID}
      onTestStateChange={onTestStateChange}
    />
  );
}

PreviewHarness.propTypes = {
  filters: PropTypes.array.isRequired,
  runType: PropTypes.string.isRequired,
  onTestStateChange: PropTypes.func.isRequired,
};

const renderPreview = ({ filters = [], runType = "historical" } = {}) => {
  const onTestStateChange = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <PreviewHarness
        filters={filters}
        runType={runType}
        onTestStateChange={onTestStateChange}
      />
    </QueryClientProvider>,
  );
  return { onTestStateChange };
};

describe("TaskLivePreview on a project with no data yet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue(EMPTY_SPAN_PAGE);
  });

  it("says the project has no data yet instead of blaming filters nobody set", async () => {
    const { onTestStateChange } = renderPreview();

    expect(await screen.findByText(NO_DATA_HINT)).toBeVisible();
    expect(screen.getByText("No matching rows")).toBeVisible();
    expect(screen.queryByText(FILTER_HINT)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(onTestStateChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ noDataYet: true }),
      ),
    );
  });

  it("treats an unfinished filter row as no filter", async () => {
    const { onTestStateChange } = renderPreview({ filters: [BLANK_FILTER] });

    expect(await screen.findByText(NO_DATA_HINT)).toBeVisible();
    expect(screen.queryByText(FILTER_HINT)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(onTestStateChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ noDataYet: true }),
      ),
    );
  });

  it("does not suggest New incoming data once it is already chosen", async () => {
    renderPreview({ runType: "continuous" });

    expect(await screen.findByText(NO_DATA_CONTINUOUS_HINT)).toBeVisible();
    expect(screen.queryByText(NO_DATA_HINT)).not.toBeInTheDocument();
    expect(screen.queryByText(FILTER_HINT)).not.toBeInTheDocument();
  });

  it("still points at the filters when a filter is what matched nothing", async () => {
    const { onTestStateChange } = renderPreview({ filters: [STATUS_FILTER] });

    expect(await screen.findByText(FILTER_HINT)).toBeVisible();
    expect(screen.queryByText(NO_DATA_HINT)).not.toBeInTheDocument();
    const sentFilters = JSON.parse(mocks.get.mock.calls[0][1].params.filters);
    expect(sentFilters).toEqual([
      expect.objectContaining({ column_id: "status" }),
    ]);
    await waitFor(() =>
      expect(onTestStateChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ noDataYet: false }),
      ),
    );
  });

  it("reports that the project has data once a row comes back", async () => {
    mocks.get.mockImplementation(async (url) => {
      if (url === "/spans/") return SPAN_PAGE_WITH_ROW;
      throw new Error(`Not needed by this test: ${url}`);
    });
    const { onTestStateChange } = renderPreview();

    await waitFor(() =>
      expect(onTestStateChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ noDataYet: false }),
      ),
    );
    expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();
  });

  it("gives no answer about the project while the list is still loading", async () => {
    mocks.get.mockImplementation(() => new Promise(() => {}));
    const { onTestStateChange } = renderPreview();

    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    expect(onTestStateChange).toHaveBeenCalled();
    expect(
      onTestStateChange.mock.calls.map(([state]) => state.noDataYet),
    ).toEqual(onTestStateChange.mock.calls.map(() => null));
    expect(screen.queryByText(NO_DATA_HINT)).not.toBeInTheDocument();
  });
});
