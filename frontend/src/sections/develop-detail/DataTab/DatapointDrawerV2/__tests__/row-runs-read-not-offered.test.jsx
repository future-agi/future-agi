// Runs across experiments are listed in the launch registry but a
// deployment does not offer them: the row runs route is absent. The row runs
// read is never sent, whatever its caller asks, and the row panel keeps a
// turned-off tab in its strip: disabled, named by its label and described
// by the shared line.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  renderHook,
  screen,
} from "@testing-library/react";
import { render } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";
import { useRowRuns } from "src/api/develop/row-view";
import RowPanelShell from "src/sections/common/RowPanel/RowPanelShell";

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);
const runsCalls = (spy) =>
  spy.mock.calls.filter(([url]) => /\/rows\/r1\/runs\//.test(String(url)));
// Let any request the hook would send leave first.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

let get;
beforeEach(() => {
  // A deployment that has made nothing real.
  setCollabOfferForTest({ all: false, madeReal: [] });
  get = vi.spyOn(axios, "get").mockResolvedValue({
    data: { result: { summary: {}, metrics: [], runs: [] } },
  });
});
afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
  // Restore only this file's spy: restoring every mock would also empty the
  // test setup's window.matchMedia stub, which the row panel reads.
  get.mockRestore();
});

describe("the row runs read where runs are not offered", () => {
  it("is never sent, whatever the caller asks", async () => {
    renderHook(() => useRowRuns("d1", "r1"), { wrapper });
    renderHook(
      () => useRowRuns("d1", "r1", { scope: "all" }, { enabled: true }),
      { wrapper },
    );
    await settle();
    expect(runsCalls(get)).toEqual([]);
  });

  it("is sent where the registry makes runs real", async () => {
    setCollabOfferForTest({ all: false, madeReal: ["dataset.rowExtras.runs"] });
    renderHook(() => useRowRuns("d1", "r1"), { wrapper });
    await settle();
    expect(runsCalls(get)).toHaveLength(1);
  });
});

describe("the row runs read with this build's own claims", () => {
  it("is sent once, to the row's runs route", async () => {
    setCollabOfferForTest({ all: false });
    renderHook(() => useRowRuns("d1", "r1"), { wrapper });
    await settle();
    const calls = runsCalls(get);
    expect(calls).toHaveLength(1);
    expect(String(calls[0][0])).toMatch(
      /\/model-hub\/datasets\/d1\/rows\/r1\/runs\/$/,
    );
  });
});

describe("a turned-off tab in the row panel", () => {
  const mount = (onTab) =>
    render(
      <RowPanelShell
        open
        onClose={() => {}}
        header={{ title: "Row 12" }}
        tabs={[
          { value: "row", label: "Row" },
          { value: "runs", label: "Runs", off: NOT_IN_RELEASE },
          { value: "annotations", label: "Annotations", badge: 0 },
        ]}
        tab="row"
        onTab={onTab}
        grid={<div>GRID</div>}
      >
        <div>Row body</div>
      </RowPanelShell>,
    );

  it("stays in the strip, disabled, named by its label and described by the shared line", () => {
    mount(vi.fn());
    const runs = screen.getByRole("tab", { name: "Runs" });
    expect(runs).toBeDisabled();
    expect(runs).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(runs).toHaveAttribute("data-testid", "row-tab-runs");
  });

  it("does not open when pressed; its neighbours do", () => {
    const onTab = vi.fn();
    mount(onTab);
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    expect(onTab).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "Annotations" }));
    expect(onTab).toHaveBeenCalledWith("annotations");
  });
});
