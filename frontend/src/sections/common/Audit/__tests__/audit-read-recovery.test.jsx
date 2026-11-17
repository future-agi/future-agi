// G2A-collab-03: failed audit reads must not masquerade as an empty history.
// Exercise the real query hook with a mocked transport; no backend is required.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuditLogPanel } from "../AuditDrawer";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: { develop: { w4: { audit: "/model-hub/audit/" } } },
}));
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
/* eslint-enable react/prop-types */
vi.mock("src/sections/common/export/datasetExport", () => ({
  saveFile: vi.fn(),
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));

const EMPTY_MESSAGE = "Nothing matches these filters.";
const PERSON = { id: "u1", name: "Maya Chen" };
const GROUPS = [
  {
    key: "access",
    label: "Access",
    count: 1,
    actions: ["dataset.access_changed"],
  },
];
const ENTRY = {
  id: "audit-1",
  at: "2026-11-09T10:00:00.000Z",
  action: "dataset.access_changed",
  action_label: "changed access",
  actor: PERSON,
  scope: { kind: "dataset", id: "d1", name: "Golden set" },
  diff: [],
};
const page = (entries = []) => ({
  data: {
    entries,
    total: entries.length,
    groups: GROUPS,
    actors: [PERSON],
    next_cursor: null,
  },
});
const readError = (status) =>
  Object.assign(new Error("Audit read failed"), { response: { status } });

let client;
beforeEach(() => {
  get.mockReset();
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false },
    },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

function renderPanel({ enabled = true } = {}) {
  return render(
    <QueryClientProvider client={client}>
      <AuditLogPanel
        scope="dataset:d1"
        title="Golden set"
        enabled={enabled}
      />
    </QueryClientProvider>,
  );
}

async function chooseFilter(label, option) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
  await waitFor(() =>
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
  );
}

describe("audit read recovery", () => {
  it.each([403, 404, 503])(
    "shows an error for an initial HTTP %s and retries the same request",
    async (status) => {
      get
        .mockRejectedValueOnce(readError(status))
        .mockResolvedValueOnce(page([ENTRY]));
      renderPanel();

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Couldn't load the audit log.",
      );
      expect(screen.getByTestId("audit-drawer")).toBeVisible();
      expect(screen.getByRole("combobox", { name: "Action" })).toBeVisible();
      expect(screen.queryByText(EMPTY_MESSAGE)).not.toBeInTheDocument();
      expect(screen.getByTestId("audit-export-csv")).toBeDisabled();
      const originalRequest = get.mock.calls[0];
      expect(originalRequest).toEqual([
        "/model-hub/audit/",
        { params: { scope: "dataset:d1", since: expect.any(String) } },
      ]);

      fireEvent.click(screen.getByRole("button", { name: "Retry" }));

      expect(await screen.findByTestId("audit-entry")).toHaveTextContent(
        "Maya Chen changed access",
      );
      expect(get).toHaveBeenCalledTimes(2);
      expect(get.mock.calls[1]).toEqual(originalRequest);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByText(EMPTY_MESSAGE)).not.toBeInTheDocument();
    },
  );

  it("preserves the scope, action, person and period when retrying a filtered read", async () => {
    get.mockResolvedValue(page([ENTRY]));
    renderPanel();
    await screen.findByTestId("audit-entry");
    const defaultSince = get.mock.calls[0][1].params.since;

    await chooseFilter("Action", "Access (1)");
    await screen.findByTestId("audit-entry");
    await chooseFilter("Person", PERSON.name);
    await screen.findByTestId("audit-entry");

    get.mockRejectedValueOnce(readError(503));
    await chooseFilter("Period", "Last 7 days");
    await screen.findByRole("alert");
    expect(screen.queryByText(EMPTY_MESSAGE)).not.toBeInTheDocument();
    const callsBeforeRetry = get.mock.calls.length;
    const failedRequest = get.mock.calls[callsBeforeRetry - 1];
    expect(failedRequest).toEqual([
      "/model-hub/audit/",
      {
        params: {
          scope: "dataset:d1",
          action: "dataset.access_changed",
          actor: "u1",
          since: new Date(
            Date.parse(defaultSince) + 23 * 24 * 60 * 60 * 1000,
          ).toISOString(),
        },
      },
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("audit-entry");

    expect(get).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(get.mock.calls[callsBeforeRetry]).toEqual(failedRequest);
    expect(screen.getByRole("combobox", { name: "Action" })).toHaveTextContent(
      "Access (1)",
    );
    expect(screen.getByRole("combobox", { name: "Person" })).toHaveTextContent(
      PERSON.name,
    );
    expect(screen.getByRole("combobox", { name: "Period" })).toHaveTextContent(
      "Last 7 days",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the legitimate empty state for a successful zero-entry response", async () => {
    get.mockResolvedValueOnce(page());
    renderPanel();

    expect(await screen.findByText(EMPTY_MESSAGE)).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("audit-entry")).not.toBeInTheDocument();
  });

  it.each([
    ["empty", []],
    ["populated", [ENTRY]],
  ])("reports a failed refresh of a cached %s history", async (_label, entries) => {
    get.mockResolvedValueOnce(page(entries));
    renderPanel();
    if (entries.length) await screen.findByTestId("audit-entry");
    else await screen.findByText(EMPTY_MESSAGE);

    get.mockRejectedValueOnce(readError(503));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["w4", "audit"] });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't load the audit log.",
    );
    expect(screen.queryByText(EMPTY_MESSAGE)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    if (entries.length) {
      expect(screen.getByTestId("audit-entry")).toHaveTextContent(
        "Maya Chen changed access",
      );
    }
  });

  it("does not claim an empty history while the initial read is pending", async () => {
    let resolveRead;
    get.mockImplementationOnce(
      () => new Promise((resolve) => { resolveRead = resolve; }),
    );
    renderPanel();

    expect(screen.getByRole("status")).toHaveTextContent("Loading audit log");
    expect(screen.queryByText(EMPTY_MESSAGE)).not.toBeInTheDocument();
    await act(async () => { resolveRead(page()); });
    expect(await screen.findByText(EMPTY_MESSAGE)).toBeVisible();
  });

  it("does not claim an empty history when the query has not been enabled", () => {
    renderPanel({ enabled: false });

    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText(EMPTY_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
