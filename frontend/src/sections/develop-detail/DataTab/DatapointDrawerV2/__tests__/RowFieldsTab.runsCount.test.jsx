// The Row tab's Generated section says how often the row ran in experiments,
// from the count the Row view hands it: nothing while the count is not
// known, "Not run in any experiment yet" at 0, and above 0 a link that opens
// the Runs tab. Where runs are not offered it says the shared line. The count
// is the row runs read's own summary: the Row view's read and the Runs tab's
// default view are one read. The Runs tab shows the count as a badge above 0
// only.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { render as renderApp } from "src/utils/test-utils";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";
import { useRowRuns } from "src/api/develop/row-view";
import RowPanelShell from "src/sections/common/RowPanel/RowPanelShell";
import RowFieldsTab from "../RowFieldsTab";

vi.mock("../OutputCard", () => ({ default: () => null }));
vi.mock("../RowFieldsStrip", () => ({ default: () => null }));
vi.mock("../RowSectionBar", () => ({ default: () => null }));
vi.mock("../MetadataSection", () => ({ default: () => null }));
vi.mock("../rowSummary", () => ({ rowSummaryParts: () => [] }));
vi.mock("src/sections/common/Collab/FieldCollabMenu", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Collab/FieldTranslation", () => ({
  default: () => null,
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useGetDatasetDetail: () => ({ data: undefined }),
}));
vi.mock("src/sections/common/ux/oneview/useOutline", () => ({
  default: () => ({
    isOpen: () => true,
    toggle: () => {},
    allOpen: true,
    collapseAll: () => {},
    expandAll: () => {},
  }),
}));
vi.mock("src/sections/develop/realBackendCaps", () => ({
  gridWritesClassic: () => true,
}));

// One generated column, so the Generated section shows.
const MODEL = {
  input: [],
  expected: [],
  outputs: [{ column: { id: "c-out", name: "answer" }, value: "Paris" }],
  evals: [],
  human: [],
  metadata: [],
  noRole: [],
  rowMetadata: { value: {} },
};
const EDIT = {
  canEdit: false,
  editingId: null,
  start: () => {},
  revert: () => {},
  apply: () => {},
  metaKey: "__row_metadata__",
};
const VIEW = { mode: "formatted", diffOn: false, asOf: null, collapseTick: 0 };
const NOT_RUN = "Not run in any experiment yet";
const alsoRan = () => screen.queryByRole("button", { name: /^Also ran in/ });

const quietClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });
const SAVED_ROW = { row_id: "r1", row_meta: { counts: { runs: 7 } } };
const mount = (runsCount, onOpenRuns = () => {}, row = SAVED_ROW) =>
  render(
    <QueryClientProvider client={quietClient()}>
      <MemoryRouter initialEntries={["/dashboard/develop/ds-1"]}>
        <Routes>
          <Route
            path="/dashboard/develop/:dataset"
            element={
              <RowFieldsTab
                model={MODEL}
                row={row}
                view={VIEW}
                edit={EDIT}
                runsCount={runsCount}
                onOpenRuns={onOpenRuns}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
});

describe("the Generated section's runs line", () => {
  // The section's other reads stay pending; only the count is under test.
  let pending;
  beforeEach(() => {
    pending = vi.spyOn(axios, "get").mockReturnValue(new Promise(() => {}));
  });
  afterEach(() => {
    pending.mockRestore();
  });

  it("says nothing about runs while the count is not known", () => {
    mount(undefined);
    expect(screen.getByTestId("row-section-outputs")).toBeInTheDocument();
    expect(alsoRan()).toBeNull();
    expect(screen.queryByText(NOT_RUN)).toBeNull();
  });

  it("at 0, says the row has not run in any experiment yet", () => {
    mount(0);
    expect(screen.getByText(NOT_RUN)).toBeInTheDocument();
    expect(alsoRan()).toBeNull();
  });

  it("above 0, names the count and opens the Runs tab", () => {
    const onOpenRuns = vi.fn();
    mount(2, onOpenRuns);
    const link = screen.getByRole("button", {
      name: "Also ran in 2 experiment runs → Runs",
    });
    expect(screen.queryByText(NOT_RUN)).toBeNull();
    fireEvent.click(link);
    expect(onOpenRuns).toHaveBeenCalledTimes(1);
  });

  it("at 1, names one run", () => {
    mount(1);
    expect(
      screen.getByRole("button", {
        name: "Also ran in 1 experiment run → Runs",
      }),
    ).toBeInTheDocument();
  });

  it.each(["new-1", "imp-1"])(
    "for %s, a row not on the server yet, says it has not run in any experiment yet",
    (id) => {
      mount(0, () => {}, { row_id: id, order: null, __pending: "new" });
      expect(screen.getByText(NOT_RUN)).toBeInTheDocument();
      expect(alsoRan()).toBeNull();
    },
  );

  it("where runs are not offered, says the shared line and no count", () => {
    setCollabOfferForTest({ all: false, madeReal: [] });
    mount(2);
    expect(screen.getByText(NOT_IN_RELEASE)).toBeInTheDocument();
    expect(alsoRan()).toBeNull();
    expect(screen.queryByText(NOT_RUN)).toBeNull();
  });
});

describe("the read the count comes from", () => {
  it("the Row view's read and the Runs tab's default view are one read", async () => {
    const get = vi.spyOn(axios, "get").mockResolvedValue({
      data: { result: { summary: { runs: 2 }, metrics: [], runs: [] } },
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    /* eslint-disable react/prop-types */
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    /* eslint-enable react/prop-types */
    try {
      // As RowView asks, then as the Runs tab's default view asks.
      const { result } = renderHook(
        () => [
          useRowRuns("d1", "r1", {}, {}),
          useRowRuns("d1", "r1", { scope: "all", sort: "newest" }),
        ],
        { wrapper },
      );
      // Wait for the shared answer, then count the reads that were sent.
      await waitFor(() =>
        expect(result.current[0].data?.summary?.runs).toBe(2),
      );
      const runsCalls = get.mock.calls.filter(([url]) =>
        /\/rows\/r1\/runs\//.test(String(url)),
      );
      expect(runsCalls).toHaveLength(1);
      expect(result.current[1].data?.summary?.runs).toBe(2);
    } finally {
      get.mockRestore();
      client.clear();
    }
  });

  it("is never sent for a new or imported row, which is not on the server yet", async () => {
    const get = vi.spyOn(axios, "get").mockResolvedValue({
      data: { result: { summary: { runs: 2 }, metrics: [], runs: [] } },
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    /* eslint-disable react/prop-types */
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    /* eslint-enable react/prop-types */
    try {
      // As RowView asks for each.
      const { result } = renderHook(
        () => [
          useRowRuns("d1", "new-1", {}, {}),
          useRowRuns("d1", "imp-1", {}, {}),
        ],
        { wrapper },
      );
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      const runsCalls = get.mock.calls.filter(([url]) =>
        /\/rows\/[^/]+\/runs\//.test(String(url)),
      );
      expect(runsCalls).toEqual([]);
      expect(result.current[0].fetchStatus).toBe("idle");
      expect(result.current[1].fetchStatus).toBe("idle");
    } finally {
      get.mockRestore();
      client.clear();
    }
  });
});

describe("the Runs tab's badge", () => {
  const runsTabText = (badge) => {
    renderApp(
      <RowPanelShell
        open
        onClose={() => {}}
        header={{ title: "Row 12" }}
        tabs={[
          { value: "row", label: "Row" },
          { value: "runs", label: "Runs", badge },
        ]}
        tab="row"
        onTab={() => {}}
        grid={<div>GRID</div>}
      >
        <div>Row body</div>
      </RowPanelShell>,
    );
    return screen.getByTestId("row-tab-runs").textContent;
  };

  it("shows no badge while the count is not known", () => {
    expect(runsTabText(undefined)).toBe("Runs");
  });

  it("shows no badge at 0", () => {
    expect(runsTabText(0)).toBe("Runs");
  });

  it("shows the count above 0", () => {
    expect(runsTabText(2)).toBe("Runs2");
  });
});
