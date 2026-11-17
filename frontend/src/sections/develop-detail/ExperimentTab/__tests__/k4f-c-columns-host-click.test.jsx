// integration C2 review 2 (S4): ticking a column's checkbox INSIDE the persistent
// Columns host (opened from a door) shows the column, and unticking hides
// it again — the user's click path, not a layout call.
import React from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import { ColumnsMenuHost } from "src/sections/common/ux/oneview/ColumnsMenu";
import { openColumnsMenu } from "src/sections/common/ux/oneview/columnsMenuHost";
import { useLayoutStore } from "src/sections/common/ux/oneview/layoutPrefs";

const COLS = [
  { id: "experiment", label: "Experiment", locked: true },
  { id: "success", label: "Success" },
  { id: "eval:correctness", label: "Correctness", defaultVisible: false },
];

beforeEach(() => {
  window.localStorage.clear();
  useLayoutStore.setState({ byKey: {} });
});

function Page() {
  const layout = useColumnLayout({
    surface: "leaderboard",
    scopeId: "ds-click",
    columns: COLS,
  });
  Page.layout = layout;
  return (
    <MemoryRouter>
      <div data-testid="grid-header" />
      <div data-testid="visible">{layout.visibleIds.join(",")}</div>
      <ColumnsMenuHost
        layout={layout}
        getAnchor={() => document.querySelector("[data-testid=grid-header]")}
      />
    </MemoryRouter>
  );
}

describe("the Columns host's own checkbox", () => {
  it("ticking Correctness shows it; unticking hides it", () => {
    render(<Page />);
    act(() => {
      openColumnsMenu(Page.layout.key);
    });
    const host = screen.getByTestId("columns-menu-host");
    expect(screen.getByTestId("visible").textContent).not.toContain(
      "eval:correctness",
    );
    const box = within(host).getByRole("checkbox", {
      name: "Show Correctness",
    });
    expect(box.checked).toBe(false);
    act(() => {
      fireEvent.click(box);
    });
    expect(screen.getByTestId("visible").textContent).toContain(
      "eval:correctness",
    );
    expect(
      within(screen.getByTestId("columns-menu-host")).getByRole("checkbox", {
        name: "Show Correctness",
      }).checked,
    ).toBe(true);
    act(() => {
      fireEvent.click(
        within(screen.getByTestId("columns-menu-host")).getByRole("checkbox", {
          name: "Show Correctness",
        }),
      );
    });
    expect(screen.getByTestId("visible").textContent).not.toContain(
      "eval:correctness",
    );
  });
});
