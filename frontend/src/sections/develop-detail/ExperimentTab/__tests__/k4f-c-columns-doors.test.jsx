// integration C2 (AG-02): one persistent Columns menu host answers every door
// (View › Columns › Edit, ⌘K Columns…, a header right-click), even when the
// door's own popover has unmounted; blanks sort last in both directions.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import { ColumnsMenuHost } from "src/sections/common/ux/oneview/ColumnsMenu";
import {
  openColumnsMenu,
  registerColumnsHost,
} from "src/sections/common/ux/oneview/columnsMenuHost";
import { useLayoutStore } from "src/sections/common/ux/oneview/layoutPrefs";
import {
  registeredCommands,
  useCommandStore,
} from "src/sections/common/CommandPalette/commandRegistry";
import { nullsLastComparator } from "../leaderboardColumns";

const COLS = [
  { id: "experiment", label: "Experiment", locked: true },
  { id: "success", label: "Success" },
  { id: "eval:correctness", label: "Correctness", defaultVisible: false },
];

beforeEach(() => {
  window.localStorage.clear();
  useLayoutStore.setState({ byKey: {} });
});

function Page({ withHost = true }) {
  const layout = useColumnLayout({
    surface: "leaderboard",
    scopeId: "ds-1",
    columns: COLS,
  });
  Page.layout = layout;
  return (
    <MemoryRouter>
      <div data-testid="grid-header" />
      {withHost ? (
        <ColumnsMenuHost
          layout={layout}
          getAnchor={() => document.querySelector("[data-testid=grid-header]")}
        />
      ) : null}
    </MemoryRouter>
  );
}

Page.propTypes = { withHost: PropTypes.bool };

describe("ColumnsMenuHost", () => {
  it("opens from openColumnsMenu (the View › Columns › Edit door)", () => {
    render(<Page />);
    expect(screen.queryByTestId("columns-menu-host")).toBeNull();
    let ok;
    act(() => {
      ok = Page.layout.openMenu();
    });
    expect(ok).toBe(true);
    expect(screen.getByTestId("columns-menu-host")).toBeTruthy();
    expect(screen.getByText("Correctness")).toBeTruthy();
  });

  it("wins over a door's own host and survives that host unmounting", () => {
    render(<Page />);
    let lowerOpened = 0;
    const unregister = registerColumnsHost(
      Page.layout.key,
      () => {
        lowerOpened += 1;
      },
      1,
    );
    unregister(); // the View popover closed on Edit
    act(() => {
      openColumnsMenu(Page.layout.key);
    });
    expect(lowerOpened).toBe(0);
    expect(screen.getByTestId("columns-menu-host")).toBeTruthy();
  });

  it("⌘K Columns… runs into the same host", () => {
    render(<Page />);
    const cmd = registeredCommands(useCommandStore.getState().sources).find(
      (c) => c.label === "Columns…",
    );
    expect(cmd).toBeTruthy();
    act(() => cmd.run());
    expect(screen.getByTestId("columns-menu-host")).toBeTruthy();
  });

  it("without a host the door reports it could not open", () => {
    render(<Page withHost={false} />);
    expect(openColumnsMenu(Page.layout.key)).toBe(false);
  });

  it("toggling a column in the host updates the layout", () => {
    render(<Page />);
    act(() => {
      Page.layout.openMenu();
    });
    expect(Page.layout.visibleIds).not.toContain("eval:correctness");
    act(() => Page.layout.show("eval:correctness"));
    expect(Page.layout.visibleIds).toContain("eval:correctness");
  });
});

describe("nullsLastComparator", () => {
  // AG Grid negates the comparator's result for a descending sort.
  const sortAs = (values, dir) =>
    [...values].sort((a, b) => {
      const r = nullsLastComparator(a, b, null, null, dir === "desc");
      return dir === "desc" ? -r : r;
    });
  const vals = [0.02, null, 0.005, undefined, 0.011, Number.NaN];
  it("ascending keeps blanks last", () => {
    const out = sortAs(vals, "asc");
    expect(out.slice(0, 3)).toEqual([0.005, 0.011, 0.02]);
    expect(out.slice(3).every((v) => v == null || Number.isNaN(v))).toBe(true);
  });
  it("descending keeps blanks last", () => {
    const out = sortAs(vals, "desc");
    expect(out.slice(0, 3)).toEqual([0.02, 0.011, 0.005]);
    expect(out.slice(3).every((v) => v == null || Number.isNaN(v))).toBe(true);
  });
  it("strings compare by locale, blanks last", () => {
    expect(sortAs(["b", null, "a"], "asc")).toEqual(["a", "b", null]);
    expect(sortAs(["b", null, "a"], "desc")).toEqual(["b", "a", null]);
  });
});
