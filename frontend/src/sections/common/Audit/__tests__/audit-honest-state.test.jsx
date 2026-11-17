// The real server has no /model-hub/audit/ route. On the real backend the
// audit panel (the dataset ⋯ menu and Command-K drawer, Settings › Audit log)
// sends no read and says so; the prototype keeps reading the audit log.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "src/utils/test-utils";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import AuditDrawer, { AuditLogPanel } from "../AuditDrawer";
import { workspaceAuditScope } from "../auditModel";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: { develop: { w4: { audit: "/model-hub/audit/" } } },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/export/datasetExport", () => ({
  saveFile: vi.fn(),
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));

const AUDIT_PATH = "/model-hub/audit/";
const HONEST =
  "Audit events are not recorded on this server. A row's changes are listed in its Activity tab.";
const EMPTY_PAGE = {
  data: { entries: [], total: 0, groups: [], actors: [], next_cursor: null },
};
const auditReads = () => get.mock.calls.filter(([url]) => url === AUDIT_PATH);

let client;
beforeEach(() => {
  get.mockReset();
  get.mockResolvedValue(EMPTY_PAGE);
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false },
    },
  });
});
afterEach(() => {
  setRealBackendForTests(null);
  cleanup();
  client.clear();
});

const withClient = (ui) =>
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);

// DatasetHeaderBar: ⋯ "Audit log" and Command-K "Open the dataset's audit log".
const openDatasetDrawer = () =>
  withClient(
    <AuditDrawer
      open
      onClose={vi.fn()}
      scope="dataset:d1"
      title="Golden set"
    />,
  );
// WorkspaceAuditLog: Settings › Audit log.
const openWorkspacePage = () =>
  withClient(
    <AuditLogPanel scope={workspaceAuditScope("ws1")} title="Support Agents" />,
  );

describe("audit log on the real backend", () => {
  it("the dataset drawer says audit events are not recorded and sends no read", async () => {
    setRealBackendForTests(true);
    openDatasetDrawer();
    await act(async () => {});

    expect(screen.getByText(HONEST)).toBeVisible();
    expect(screen.getByText("Audit log · Golden set")).toBeVisible();
    expect(screen.getByLabelText("Close audit log")).toBeVisible();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByTestId("audit-export-csv")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(auditReads()).toHaveLength(0);
    expect(get).not.toHaveBeenCalled();
  });

  it("Settings › Audit log says the same and sends no read", async () => {
    setRealBackendForTests(true);
    openWorkspacePage();
    await act(async () => {});

    expect(screen.getByText(HONEST)).toBeVisible();
    expect(screen.getByText("Audit log · Support Agents")).toBeVisible();
    expect(screen.queryByLabelText("Close audit log")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(auditReads()).toHaveLength(0);
    expect(get).not.toHaveBeenCalled();
  });
});

describe("audit log in the prototype", () => {
  it.each([
    ["the dataset drawer", openDatasetDrawer, "dataset:d1"],
    ["Settings › Audit log", openWorkspacePage, "workspace:ws1"],
  ])("%s still reads the audit log", async (_label, open, scope) => {
    setRealBackendForTests(false);
    open();

    await waitFor(() => expect(auditReads()).toHaveLength(1));
    expect(auditReads()[0][1].params.scope).toBe(scope);
    expect(
      await screen.findByText("Nothing matches these filters."),
    ).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Action" })).toBeVisible();
    expect(screen.queryByText(HONEST)).not.toBeInTheDocument();
  });
});
