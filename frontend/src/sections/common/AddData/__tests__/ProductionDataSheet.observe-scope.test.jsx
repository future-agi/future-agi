import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ProductionDataSheet from "../ProductionDataSheet";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), empty: [] }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: { project: { listProjects: () => "/projects/" } },
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: mocks.empty }),
  useGetDatasetColumns: () => ({ data: mocks.empty }),
}));
vi.mock("../useProductionImport", () => ({
  default: () => ({
    submit: vi.fn(),
    busy: false,
    pending: null,
    error: null,
    setError: vi.fn(),
  }),
}));

const filters = [
  {
    column_id: "name",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: "chosen cohort",
    },
  },
  {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: ["2026-08-01T00:00:00Z", "2026-08-03T00:00:00Z"],
    },
  },
];
let client;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.get.mockResolvedValue({
    data: {
      result: {
        projects: [
          { id: "project", name: "Project" },
          { id: "other", name: "Other project" },
        ],
      },
    },
  });
  mocks.post.mockResolvedValue({
    data: { result: { items: [], limited: true, limit: 200 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

function mountSource(source) {
  render(
    <QueryClientProvider client={client}>
      <ProductionDataSheet
        initial={{ source }}
        onClose={vi.fn()}
        setDirty={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

it("OBS-SW-45 previews all matching spans even when the exclusion list is empty", async () => {
  mountSource({
    kind: "traces",
    source_kind: "spans",
    project_id: "project",
    select_all: true,
    span_ids: [],
    filters,
  });
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(
      "/tracer/dataset/preview_sources/",
      {
        project: "project",
        source_kind: "spans",
        failures_only: false,
        filters,
        exclude_ids: [],
      },
    ),
  );
  expect(screen.getByRole("combobox", { name: "Source" })).toHaveTextContent(
    "Spans",
  );
  expect(await screen.findByText(/up to 200 matching/i)).toBeVisible();
  expect(
    screen.queryByText(/No matching data in the last 7 days/),
  ).not.toBeInTheDocument();
});

it("OBS-SW-45 sends deselections to the server before the preview cap", async () => {
  mountSource({
    kind: "traces",
    source_kind: "traces",
    project_id: "project",
    select_all: true,
    trace_ids: ["excluded-trace"],
    filters,
  });
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(
      "/tracer/dataset/preview_sources/",
      {
        project: "project",
        source_kind: "traces",
        failures_only: false,
        filters,
        exclude_ids: ["excluded-trace"],
      },
    ),
  );
});

it("OBS-SW-45 clears the handed-over cohort when the project changes", async () => {
  const user = userEvent.setup();
  mountSource({
    kind: "traces",
    source_kind: "spans",
    project_id: "project",
    select_all: true,
    span_ids: ["excluded-span"],
    filters,
  });
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(
      "/tracer/dataset/preview_sources/",
      expect.objectContaining({ filters }),
    ),
  );
  await user.click(screen.getByRole("combobox", { name: "Project" }));
  await user.click(
    await screen.findByRole("option", { name: "Other project" }),
  );
  await waitFor(() =>
    expect(mocks.post).toHaveBeenLastCalledWith(
      "/tracer/dataset/preview_sources/",
      { project: "other", source_kind: "spans", failures_only: false },
    ),
  );
});

const spans = [
  { span_id: "S1", trace_id: "T1", name: "first" },
  { span_id: "S2", trace_id: "T2", name: "second" },
  { span_id: "S3", trace_id: "T3", name: "third" },
];
function previewItems(items) {
  mocks.post.mockResolvedValue({
    data: { result: { items, limited: false, limit: 200 } },
  });
}

it("OBS-SW-190 opens with the spans chosen in Observe already ticked", async () => {
  previewItems(spans);
  mountSource({
    kind: "traces",
    project_id: "project",
    span_ids: ["S1", "S2", "S3"],
  });
  for (const item of spans)
    expect(
      await screen.findByRole("checkbox", {
        name: `${item.name} · ${item.span_id}`,
      }),
    ).toBeChecked();
  expect(
    screen.getByRole("button", { name: "Create dataset with 3 rows" }),
  ).toBeInTheDocument();
});

it("OBS-SW-190 opens with the root spans of chosen traces already ticked", async () => {
  previewItems([{ span_id: "root-1", trace_id: "T1", name: "Trace root" }]);
  mountSource({ kind: "traces", project_id: "project", trace_ids: ["T1"] });
  expect(
    await screen.findByRole("checkbox", { name: "Trace root · root-1" }),
  ).toBeChecked();
  expect(
    screen.getByRole("button", { name: "Create dataset with 1 row" }),
  ).toBeInTheDocument();
});

it("OBS-SW-190 lets the user untick a handed-over span", async () => {
  const user = userEvent.setup();
  previewItems(spans);
  mountSource({
    kind: "traces",
    project_id: "project",
    span_ids: ["S1", "S2", "S3"],
  });
  await user.click(
    await screen.findByRole("checkbox", { name: "second · S2" }),
  );
  expect(
    screen.getByRole("checkbox", { name: "second · S2" }),
  ).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "first · S1" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "third · S3" })).toBeChecked();
  expect(
    screen.getByRole("button", { name: "Create dataset with 2 rows" }),
  ).toBeInTheDocument();
});

it("OBS-SW-190 leaves an all-matching preview unticked", async () => {
  previewItems(spans);
  mountSource({
    kind: "traces",
    source_kind: "spans",
    project_id: "project",
    select_all: true,
    span_ids: [],
    filters,
  });
  for (const item of spans)
    expect(
      await screen.findByRole("checkbox", {
        name: `${item.name} · ${item.span_id}`,
      }),
    ).not.toBeChecked();
  expect(
    screen.getByRole("button", { name: "Create dataset with 0 rows" }),
  ).toBeInTheDocument();
});
