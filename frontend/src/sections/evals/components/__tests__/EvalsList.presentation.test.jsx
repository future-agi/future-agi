import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import EvalsListView from "../EvalsListView";
import { evalPresentationTheme } from "./evalPresentationTheme";

const h = vi.hoisted(() => ({
  list: {
    items: [
      {
        id: "eval-1",
        name: "User evaluation",
        owner: "user",
        eval_type: "llm",
      },
    ],
    total: 1,
  },
}));
vi.mock("../../hooks/useEvalsList", () => ({
  useEvalsList: () => ({ data: h.list, isLoading: false }),
  useEvalsListCharts: () => ({ data: null, isLoading: false }),
  useBulkDeleteEvals: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: () => ({ data: [] }),
}));
vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: { develop: { eval: { getEvalNames: "/fixture-names/" } } },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/filter-panel/FilterPanel", () => ({
  default: () => null,
}));
vi.mock("../DisplayOptionsPopover", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/sections/project/VolumeBarChart", () => ({ default: () => null }));
vi.mock("../ErrorRateSparkline", () => ({ default: () => null }));

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/dashboard/evaluations");
});
afterEach(cleanup);

const mount = (mode = "light") =>
  render(<EvalsListView />, { theme: evalPresentationTheme(mode) });

describe("evaluation list keyboard presentation", () => {
  it("opens the existing detail destination with Enter on the name link", async () => {
    const user = userEvent.setup();
    mount();
    const link = screen.getByRole("link", { name: "User evaluation" });
    expect(link).toHaveAttribute("href", "/dashboard/evaluations/eval-1");
    // Enter the real DataGrid's roving focus, rather than tabbing every cell.
    const cell = link.closest("[role=cell], [role=gridcell]");
    fireEvent.focus(cell);
    link.focus();
    await user.keyboard("{Enter}");
    expect(window.location.pathname).toBe("/dashboard/evaluations/eval-1");
  });

  it("preserves row click navigation outside the name link", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByText("User evaluation").closest("[role=row]"));
    expect(window.location.pathname).toBe("/dashboard/evaluations/eval-1");
  });

  it("does not navigate the current page on a modified name-link click", () => {
    mount();
    fireEvent.click(screen.getByRole("link", { name: "User evaluation" }), {
      ctrlKey: true,
    });
    expect(window.location.pathname).toBe("/dashboard/evaluations");
  });

  it.each(["light", "dark"])(
    "provides semantic inset focus outlines for cells and headers in %s mode",
    (mode) => {
      const theme = evalPresentationTheme(mode);
      const { container } = mount(mode);
      // jsdom cannot paint focus indicators; inspect the emitted rules. Actual
      // keyboard focus visibility at 1024/1280/1440 is a cloud screenshot gate.
      const rules = Array.from(document.styleSheets).flatMap((sheet) =>
        Array.from(sheet.cssRules),
      );
      const grid = container.querySelector(".MuiDataGrid-root");
      for (const target of [".MuiDataGrid-cell", ".MuiDataGrid-columnHeader"]) {
        const findRule = (state) =>
          rules.find((candidate) => {
            if (!candidate.selectorText?.includes(`${target}${state}`)) {
              return false;
            }
            const rootSelector = candidate.selectorText.split(" ")[0];
            return grid.matches(rootSelector);
          });
        const rule = findRule(":focus-visible");
        expect(rule).toBeDefined();
        // A grouped :focus-within selector would also ring pointer clicks and
        // draw a second outline around the name link's own keyboard ring.
        expect(
          rule.selectorText
            .split(",")
            .every((selector) => selector.trim().endsWith(":focus-visible")),
        ).toBe(true);
        expect(rule.style.outline).toBe(
          `2px solid ${theme.palette.border.focus}`,
        );
        expect(rule.style["outline-offset"]).toBe("-2px");
        // Suppress MUI's default pointer/ancestor outline too. When focus is
        // delegated to a child, the child owns its keyboard indicator.
        const quietRule = findRule(":not(:focus-visible)");
        expect(quietRule).toBeDefined();
        expect(quietRule.style.outline).toBe("none");
      }
    },
  );
});
