// HO results R2 review fix: the toolbar switch always carries its words
// (`Hide rows with no output`), toggles the one expression pill, and its
// hidden measuring twin is never a second control.
import React, { useRef } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import HideEmptySwitch from "../HideEmptySwitch";
import { HIDE_EMPTY_EXPR } from "../hideEmpty";

const patch = vi.fn();
let pills = [];
vi.mock("src/sections/experiment-detail/results/useResultsUrlState", () => ({
  default: () => ({ pills, patch: (...a) => patch(...a) }),
}));
vi.mock("src/api/develop/run-results", () => ({
  runResultsKey: (id) => ["run-results", id],
  fetchResultIds: (_id, view) =>
    Promise.resolve(view.pills?.length ? ["r1", "r2"] : ["r1", "r2", "r3"]),
}));
// The count is read where the read can answer it: the prototype's mock
// (a real server refuses the expression, hide-empty-server-filters.test.jsx).
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => true,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children, title }) => (
    <span data-tip={typeof title === "string" ? title : ""}>{children}</span>
  ),
}));

const view = { runIds: ["a"], pills: [] };
const renderSwitch = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <HideEmptySwitch datasetId="d1" view={view} />
    </QueryClientProvider>,
  );

describe("HideEmptySwitch", () => {
  beforeEach(() => {
    patch.mockReset();
    pills = [];
  });

  it("is one labelled switch that adds the pill", async () => {
    renderSwitch();
    const box = screen.getByRole("checkbox", {
      name: "Hide rows with no output",
    });
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByTestId("results-hide-empty").textContent).toContain(
      "Hide rows with no output",
    );
    await waitFor(() =>
      expect(
        screen
          .getByTestId("results-hide-empty")
          .getAttribute("data-hidden-rows"),
      ).toBe("1"),
    );
    fireEvent.click(box);
    expect(patch).toHaveBeenCalledWith({
      filters: JSON.stringify([{ type: "expression", expr: HIDE_EMPTY_EXPR }]),
    });
  });

  it("on, turning it off removes the pill", () => {
    pills = [{ type: "expression", expr: HIDE_EMPTY_EXPR }];
    renderSwitch();
    const box = screen.getByRole("checkbox", {
      name: "Hide rows with no output",
    });
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(patch).toHaveBeenCalledWith({ filters: null });
  });

  // The row is an ancestor (the toolbar's parent), measured after the
  // commit; jsdom has no layout, so each box's width is its data-w (the
  // hidden twin, the full labelled switch, is 200 px).
  const InRow = ({ row, toolbar }) => {
    const ref = useRef(null);
    return (
      <QueryClientProvider client={new QueryClient()}>
        <div data-w={row}>
          <div ref={ref} data-w={toolbar} data-toolbar="">
            <HideEmptySwitch
              datasetId="d1"
              view={view}
              rowOf={() => ref.current?.parentElement ?? null}
            />
          </div>
        </div>
      </QueryClientProvider>
    );
  };
  InRow.propTypes = { row: () => null, toolbar: () => null };
  const stubWidths = () =>
    vi
      .spyOn(Element.prototype, "getBoundingClientRect")
      .mockImplementation(function rect() {
        const own = this.getAttribute?.("data-w");
        const twin =
          this.getAttribute?.("aria-hidden") === "true" &&
          this.parentElement?.hasAttribute("data-toolbar");
        const width = own != null ? Number(own) : twin ? 200 : 0;
        return {
          width,
          height: 20,
          left: 0,
          right: width,
          top: 0,
          bottom: 20,
          x: 0,
          y: 0,
        };
      });

  it("shows with its words when its toolbar row has room", async () => {
    const spy = stubWidths();
    render(<InRow row={1000} toolbar={300} />);
    expect(
      await screen.findByRole("checkbox", { name: "Hide rows with no output" }),
    ).toBeTruthy();
    expect(screen.getByTestId("results-hide-empty").textContent).toContain(
      "Hide rows with no output",
    );
    spy.mockRestore();
  });

  it("is left out of a full row (the View menu line carries it)", async () => {
    const spy = stubWidths();
    render(<InRow row={400} toolbar={390} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByTestId("results-hide-empty")).toBeNull();
    spy.mockRestore();
  });

  it("is absent with no run in view", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <HideEmptySwitch datasetId="d1" view={{ runIds: [] }} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
