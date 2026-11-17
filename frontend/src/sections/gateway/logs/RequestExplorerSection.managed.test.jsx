import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import RequestExplorerSection from "./RequestExplorerSection";

// The Filters panel names the managed provider label only on a deployment
// that offers managed models, or while the loaded logs hold a managed call
// (FilterPanel.provider.test.jsx). The Logs page tells it both: from the
// managed models status the console reads (until the status says managed
// models are offered, the panel is told they are not), and from the rows
// the table loaded, by their billing mode, as the Provider cell names them.

const { status, table } = vi.hoisted(() => ({
  status: { data: undefined },
  table: { results: [] },
}));

vi.mock("../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: status.data }),
}));
vi.mock("./columns/useRequestColumns", () => ({
  default: () => ({
    columns: [],
    pickerEntries: { builtin: [], custom: [] },
    stale: [],
    visibleCount: 10,
    totalCount: 10,
    atCap: false,
    storageKey: "key",
    declarationStatus: "success",
    declarationsAvailable: true,
    declarationsFetching: false,
    reservedDeclared: false,
    unavailableCustomCount: 0,
    toggle: vi.fn(),
    move: vi.fn(),
    remove: vi.fn(),
    reset: vi.fn(),
    retryDeclarations: vi.fn(),
  }),
}));
const Table = ({ onResults }) => {
  React.useEffect(() => {
    onResults?.(table.results);
  }, [onResults]);
  return null;
};
Table.propTypes = { onResults: PropTypes.func };
vi.mock("./RequestTable", () => ({ default: (props) => <Table {...props} /> }));
vi.mock("./RequestVolumeStrip", () => ({ default: () => null }));
vi.mock("./SessionExplorer", () => ({ default: () => null }));
vi.mock("./RequestDetailDrawer", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { gateway: { requestLogExport: "/export", requestLogs: "/logs" } },
}));

const Panel = ({ open, managedOffered, managedLogged }) =>
  open ? (
    <div>
      <div>{`Filters panel managedOffered=${String(managedOffered)}`}</div>
      <div>{`Filters panel managedLogged=${String(managedLogged)}`}</div>
    </div>
  ) : null;
Panel.propTypes = {
  open: PropTypes.bool,
  managedOffered: PropTypes.bool,
  managedLogged: PropTypes.bool,
};
vi.mock("./FilterPanel", () => ({ default: (props) => <Panel {...props} /> }));

const openFilters = async () => {
  window.history.pushState({}, "", "/dashboard/gateway/logs");
  render(
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>
        <RequestExplorerSection />
      </QueryClientProvider>
    </HelmetProvider>,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Filters" }));
};

describe("Logs Filters panel: managed models", () => {
  beforeEach(() => {
    status.data = undefined;
    table.results = [];
  });

  it("tells the panel the deployment offers no managed models", async () => {
    status.data = { available: false, enabled: false };
    await openFilters();
    expect(
      screen.getByText("Filters panel managedOffered=false"),
    ).toBeInTheDocument();
  });

  it("tells the panel when managed models are offered", async () => {
    status.data = { available: true, enabled: true };
    await openFilters();
    expect(
      screen.getByText("Filters panel managedOffered=true"),
    ).toBeInTheDocument();
  });

  it("names no managed provider while the status is unknown", async () => {
    await openFilters();
    expect(
      screen.getByText("Filters panel managedOffered=false"),
    ).toBeInTheDocument();
  });

  it("tells the panel when a loaded row is a managed call", async () => {
    status.data = { available: false, enabled: false };
    table.results = [
      { id: "a", provider: "openai", billing_mode: "byok" },
      { id: "b", provider: "openrouter", billing_mode: "managed" },
    ];
    await openFilters();
    expect(
      screen.getByText("Filters panel managedLogged=true"),
    ).toBeInTheDocument();
  });

  it("tells the panel no loaded row is a managed call", async () => {
    status.data = { available: false, enabled: false };
    table.results = [
      { id: "a", provider: "openai", billing_mode: "byok" },
      { id: "c", provider: "openrouter" },
    ];
    await openFilters();
    expect(
      screen.getByText("Filters panel managedLogged=false"),
    ).toBeInTheDocument();
  });
});
