import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "src/utils/test-utils";
import ProductionDataSheet from "../ProductionDataSheet";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  close: vi.fn(),
  empty: [],
}));
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

const PREVIEW = "/tracer/dataset/preview_sources/";
const TRACE = {
  span_id: "root-span",
  trace_id: "trace-1",
  session_id: null,
  name: "chat turn",
  start_time: "2026-11-09T10:00:00Z",
};
let client;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.get.mockResolvedValue({
    data: { result: { projects: [{ id: "p", name: "P" }] } },
  });
  // A new user's first traces carry no session ID and no evaluation: only
  // the unnarrowed traces preview returns anything.
  mocks.post.mockImplementation(async (_path, body) => ({
    data: {
      result: {
        items:
          body.source_kind === "traces" && body.failures_only === false
            ? [TRACE]
            : [],
        limited: false,
        limit: 200,
      },
    },
  }));
});
afterEach(() => {
  cleanup();
  client.clear();
});

function mount(initial) {
  render(
    <QueryClientProvider client={client}>
      <ProductionDataSheet
        initial={initial}
        onClose={mocks.close}
        setDirty={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

it("FR-DATASETS-001 names the empty narrowing and shows all traces in one click", async () => {
  const user = userEvent.setup();
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(PREVIEW, {
      project: "p",
      source_kind: "sessions",
      failures_only: true,
    }),
  );
  expect(
    await screen.findByText(
      /no sessions with failing evaluations in the last 7 days in P\b/i,
    ),
  ).toBeVisible();
  expect(
    screen.queryByText("No matching data in the last 7 days."),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show all traces" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenLastCalledWith(PREVIEW, {
      project: "p",
      source_kind: "traces",
      failures_only: false,
    }),
  );
  expect(
    await screen.findByRole("checkbox", { name: "chat turn · root-span" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Source" })).toHaveTextContent(
    "Traces",
  );
  expect(
    screen.getByRole("checkbox", { name: "Failing evaluations only" }),
  ).not.toBeChecked();
  expect(
    screen.queryByRole("button", { name: "Show all traces" }),
  ).not.toBeInTheDocument();
});

it("FR-DATASETS-001 keeps keyboard focus on Source after Show all traces", async () => {
  const user = userEvent.setup();
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  const showAll = await screen.findByRole("button", {
    name: "Show all traces",
  });
  act(() => showAll.focus());
  await user.keyboard("{Enter}");
  await waitFor(() =>
    expect(mocks.post).toHaveBeenLastCalledWith(PREVIEW, {
      project: "p",
      source_kind: "traces",
      failures_only: false,
    }),
  );
  const sourceField = screen.getByRole("combobox", { name: "Source" });
  expect(sourceField).toHaveFocus();
  expect(
    await screen.findByRole("checkbox", { name: "chat turn · root-span" }),
  ).toBeInTheDocument();
  expect(sourceField).toHaveFocus();
  expect(sourceField).toHaveTextContent("Traces");
  expect(
    screen.queryByRole("listbox", { name: "Source" }),
  ).not.toBeInTheDocument();
});

it("FR-DATASETS-001 points to Observe when the project has no traces in the last 7 days", async () => {
  const user = userEvent.setup();
  mocks.post.mockResolvedValue({
    data: { result: { items: [], limited: false, limit: 200 } },
  });
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  expect(
    await screen.findByText(
      "No sessions with failing evaluations in the last 7 days in P.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Open Observe" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show all traces" }));
  expect(
    await screen.findByText("No traces in the last 7 days in P."),
  ).toBeVisible();
  expect(
    screen.getByText("Send traces from your app to P, then import them here."),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Show all traces" }),
  ).not.toBeInTheDocument();
  const openObserve = screen.getByRole("link", { name: "Open Observe" });
  expect(openObserve).toHaveAttribute("href", "/dashboard/observe");
  await user.click(openObserve);
  expect(mocks.close).toHaveBeenCalledWith(true);
});

it("FR-DATASETS-001 names the newest traces it checked when the preview is limited", async () => {
  mocks.post.mockImplementation(async (_path, body) => ({
    data: {
      result: body.failures_only
        ? { items: [], limited: true, limit: 200 }
        : { items: [TRACE], limited: false, limit: 200 },
    },
  }));
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  expect(
    await screen.findByText(
      "In P, none of the newest 200 traces in a session has a failing evaluation.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText(/in the last 7 days in P/),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Show all traces" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Open Observe" }),
  ).not.toBeInTheDocument();
});

it("FR-DATASETS-001 points a workspace with no Observe project to Observe setup", async () => {
  mocks.get.mockResolvedValue({ data: { result: { projects: [] } } });
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  expect(await screen.findByText(/no observe projects/i)).toBeVisible();
  expect(screen.getByRole("link", { name: "Set up Observe" })).toHaveAttribute(
    "href",
    "/dashboard/observe",
  );
  expect(
    screen.queryByRole("combobox", { name: "Project" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText("No matching data in the last 7 days."),
  ).not.toBeInTheDocument();
  expect(mocks.post).not.toHaveBeenCalled();
});

it("FR-DATASETS-001 shows the reading status while projects load", async () => {
  mocks.get.mockReturnValue(new Promise(() => {}));
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  expect(await screen.findByRole("status")).toHaveTextContent(
    /reading observe data/i,
  );
  expect(
    screen.queryByText("No matching data in the last 7 days."),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/^No .* in the last 7 days/),
  ).not.toBeInTheDocument();
  expect(mocks.post).not.toHaveBeenCalled();
});

it("FR-DATASETS-001 does not claim there is no data when the preview fails", async () => {
  mocks.post.mockRejectedValue(new Error("Observe is unavailable"));
  mount({ source: { kind: "sessions" }, target: { new: { name: "" } } });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Observe is unavailable",
  );
  expect(
    screen.queryByText(/^No .* in the last 7 days/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Show all traces" }),
  ).not.toBeInTheDocument();
});

it("FR-DATASETS-001 keeps failing-first for Add rows on an existing dataset", async () => {
  mount({ source: { kind: "sessions" }, target: { dataset_id: "existing" } });
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith(PREVIEW, {
      project: "p",
      source_kind: "sessions",
      failures_only: true,
    }),
  );
  expect(
    screen.getByRole("checkbox", { name: "Failing evaluations only" }),
  ).toBeChecked();
});
