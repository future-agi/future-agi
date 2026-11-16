import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";
import { USER_FILTER_FIELDS } from "../../observeFilterConfig";

const mocks = vi.hoisted(() => ({ read: vi.fn(), catalog: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.read },
  readQuery: mocks.read,
  endpoints: {
    dashboard: {
      metrics: "/tracer/dashboard/metrics/",
      filterValues: "/tracer/dashboard/filter_values/",
    },
  },
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal<typeof import("src/hooks/useDashboards")>()),
  usePropertyCatalog: (options: unknown) => mocks.catalog(options),
}));
vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector: (state: unknown) => unknown) =>
    selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

const definition = (
  name: string,
  category: string,
  propertyId: string,
  label = name,
) => ({
  name,
  display_name: label,
  category,
  property_id: propertyId,
  type: "string",
  source: "traces",
  sources: ["traces"],
});
const nativeTag = definition(
  "tag",
  "system_metric",
  "system_attribute:traces:tag",
  "Tag",
);
const clients: QueryClient[] = [];

const valuesPage = (values: string[], nextCursor: string | null = null) => ({
  data: {
    result: {
      values: values.map((value) => ({ value, label: value, type: "string" })),
      has_more: Boolean(nextCursor),
      next_cursor: nextCursor,
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
    },
  },
});

function mountToolbar({
  projectId = "project-a",
  allowWorkspaceScope = false,
} = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const onApply = vi.fn();
  function Toolbar() {
    const [open, setOpen] = useState(false);
    return (
      <ObserveToolbar
        inline
        mode="users"
        projectId={projectId}
        allowWorkspaceScope={allowWorkspaceScope}
        filterFields={USER_FILTER_FIELDS}
        isFilterOpen={open}
        onFilterToggle={() => setOpen(!open)}
        onApplyExtraFilters={onApply}
      />
    );
  }
  const rendered = render(
    <QueryClientProvider client={client}>
      <Toolbar />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  return { ...rendered, onApply };
}

async function chooseProperty(name: string) {
  fireEvent.click(screen.getByRole("button", { name: "Property" }));
  const option = await waitFor(() => {
    const element = document.querySelector(
      `[data-filter-property-option="${name}"]`,
    );
    expect(element).toBeTruthy();
    return element as HTMLElement;
  });
  fireEvent.click(option);
  const trigger = document.querySelector(
    `[data-filter-value-trigger="${name}"]`,
  );
  expect(trigger).toBeTruthy();
  fireEvent.click(trigger as HTMLElement);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.catalog.mockReturnValue({
    metrics: [nativeTag],
    categoryCounts: {
      all: 1,
      system_metric: 1,
      custom_attribute: 0,
      eval_metric: 0,
      annotation_metric: 0,
      custom_column: 0,
    },
    categoryCountsExact: true,
    legacyFallbackRequired: false,
    isLoading: false,
    isFetching: false,
    isError: false,
    isSuccess: true,
    hasNextPage: false,
    data: { pages: [] },
  });
  mocks.read.mockResolvedValue(
    valuesPage(["sdk-tag", "manual-tag", "legacy-tag"]),
  );
});

afterEach(() => {
  for (const client of clients.splice(0)) client.clear();
});

describe("Users toolbar native Tag suggestion transport", () => {
  it("pairs the trace registry with traces and applies a selected suggestion as native membership", async () => {
    const { onApply } = mountToolbar();
    await chooseProperty("tag");
    expect(
      await screen.findByRole("checkbox", { name: "sdk-tag" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "manual-tag" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "legacy-tag" }),
    ).toBeInTheDocument();
    expect(mocks.read).toHaveBeenCalledWith(
      "/tracer/dashboard/filter_values/",
      expect.objectContaining({
        params: expect.objectContaining({
          property_id: "system_attribute:traces:tag",
          metric_type: "system_metric",
          metric_name: "tag",
          source: "traces",
          project_ids: "project-a",
        }),
      }),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "manual-tag" }));
    await waitFor(() =>
      expect(onApply).toHaveBeenCalledWith([
        expect.objectContaining({
          column_id: "tag",
          property_id: "system_attribute:traces:tag",
          filter_config: expect.objectContaining({
            col_type: "SYSTEM_METRIC",
            filter_value: ["manual-tag"],
          }),
        }),
      ]),
    );
  });

  it("keeps source, registry and project scope on searched cursor continuations", async () => {
    mocks.read.mockImplementation(async (_url, { params }) => {
      if (params.search === "wanted" && !params.cursor)
        return valuesPage([], "signed-next");
      return valuesPage([params.search ? "wanted-manual" : "sdk-tag"]);
    });
    mountToolbar();
    await chooseProperty("tag");
    await screen.findByRole("checkbox", { name: "sdk-tag" });
    fireEvent.change(screen.getByPlaceholderText("Search values..."), {
      target: { value: "wanted" },
    });
    expect(
      await screen.findByRole("checkbox", { name: "wanted-manual" }),
    ).toBeInTheDocument();
    const searched = mocks.read.mock.calls
      .map(([, { params }]) => params)
      .filter((params) => params.search);
    expect(searched).toHaveLength(2);
    for (const params of searched)
      expect(params).toMatchObject({
        property_id: "system_attribute:traces:tag",
        metric_type: "system_metric",
        source: "traces",
        project_ids: "project-a",
        search: "wanted",
      });
    expect(searched[0].cursor).toBeUndefined();
    expect(searched[1].cursor).toBe("signed-next");
  });

  it.each([
    [
      "user_id_type",
      "system_metric",
      "system_attribute:traces:user_id_type",
      "system_attribute:users:user_id_type",
      "sessions",
    ],
    [
      "tag",
      "custom_attribute",
      "custom_attribute:tag",
      "custom_attribute:tag",
      "traces",
    ],
    [
      "tags",
      "custom_attribute",
      "custom_attribute:tags",
      "custom_attribute:tags",
      "traces",
    ],
    [
      "tag",
      "system_metric",
      "system_attribute:users:tag",
      "system_attribute:users:tag",
      "sessions",
    ],
    [
      "status",
      "system_metric",
      "system_attribute:traces:status",
      "system_attribute:traces:status",
      "sessions",
    ],
  ])(
    "preserves %s / %s / %s namespace transport",
    async (name, category, registryId, expectedId, source) => {
      mocks.catalog.mockReturnValue({
        ...mocks.catalog(),
        metrics: [definition(name, category, registryId, "Tag")],
      });
      const { onApply } = mountToolbar();
      await chooseProperty(name);
      fireEvent.click(
        await screen.findByRole("checkbox", { name: /^sdk-tag/ }),
      );
      expect(mocks.read).toHaveBeenCalledWith(
        "/tracer/dashboard/filter_values/",
        expect.objectContaining({
          params: expect.objectContaining({
            property_id: expectedId,
            metric_type: category,
            source,
            project_ids: "project-a",
          }),
        }),
      );
      await waitFor(() =>
        expect(onApply).toHaveBeenCalledWith([
          expect.objectContaining({
            column_id: name,
            property_id: expectedId,
            filter_config: expect.objectContaining({
              col_type:
                category === "custom_attribute"
                  ? "SPAN_ATTRIBUTE"
                  : "SYSTEM_METRIC",
            }),
          }),
        ]),
      );
    },
  );

  it.each([
    ["project-b", false, "project-b"],
    ["", true, ""],
  ])(
    "preserves authorized caller scope %s / workspace=%s",
    async (projectId, allowWorkspaceScope, expectedProjects) => {
      mountToolbar({ projectId, allowWorkspaceScope });
      await chooseProperty("tag");
      await screen.findByRole("checkbox", { name: "sdk-tag" });
      expect(mocks.read).toHaveBeenCalledWith(
        "/tracer/dashboard/filter_values/",
        expect.objectContaining({
          params: expect.objectContaining({
            property_id: "system_attribute:traces:tag",
            source: "traces",
            project_ids: expectedProjects,
          }),
        }),
      );
    },
  );

  it("does not turn a denied scope into suggestions or a different-source fallback", async () => {
    mocks.read.mockRejectedValue({
      response: { status: 403, data: { code: "permission_denied" } },
    });
    mountToolbar({ projectId: "denied-project" });
    await chooseProperty("tag");
    expect(
      await screen.findByRole("button", { name: "Retry" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "sdk-tag" }),
    ).not.toBeInTheDocument();
    expect(mocks.read).toHaveBeenCalledTimes(1);
    expect(mocks.read.mock.calls[0][1].params).toMatchObject({
      source: "traces",
      project_ids: "denied-project",
    });
  });
});
