/* eslint-disable react/prop-types */
import React, { useEffect } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import LegacyParamScrub from "../LegacyParamScrub";
import { useLayoutPrefs, useLayoutStore } from "../layoutPrefs";
import useColumnLayout from "../useColumnLayout";
import ColumnsMenu from "../ColumnsMenu";
import Section from "../Section";
import Hint, { HintRecall, showTipsOnThisPage, useHintStore } from "../Hint";
import {
  registeredCommands,
  useCommandStore,
} from "src/sections/common/CommandPalette/commandRegistry";

beforeEach(() => {
  window.localStorage.clear();
  useLayoutStore.setState({ byKey: {} });
  useHintStore.setState({ mounted: [], dismissed: {} });
});

const labels = () =>
  registeredCommands(useCommandStore.getState().sources).map((c) => c.label);

describe("LegacyParamScrub", () => {
  function Where() {
    const loc = useLocation();
    return (
      <span data-testid="loc">
        {loc.pathname}
        {loc.search}
        {loc.hash}|{JSON.stringify(loc.state)}
      </span>
    );
  }
  it("removes only `ui` (replace), keeping other params, the hash and state", () => {
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/d/1",
            search: "?ui=simple&tab=data&row=4",
            hash: "#c",
            state: { from: "x" },
          },
        ]}
      >
        <LegacyParamScrub />
        <Where />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("loc").textContent).toBe(
      '/d/1?tab=data&row=4#c|{"from":"x"}',
    );
  });
  it("leaves a link without `ui` alone", () => {
    render(
      <MemoryRouter initialEntries={["/d/1?tab=data"]}>
        <LegacyParamScrub />
        <Where />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("loc").textContent).toBe("/d/1?tab=data|null");
  });
});

describe("useLayoutPrefs", () => {
  it("two components of one surface stay in sync and the layout persists", () => {
    let a;
    let b;
    function A() {
      a = useLayoutPrefs("results", "e1");
      return null;
    }
    function B() {
      b = useLayoutPrefs("results", "e1");
      return <span data-testid="b">{String(b[0].sections.charts)}</span>;
    }
    render(
      <>
        <A />
        <B />
      </>,
    );
    act(() => a[1]({ sections: { charts: false } }));
    expect(screen.getByTestId("b").textContent).toBe("false");
    expect(
      JSON.parse(window.localStorage.getItem("fa.layout.proto-user.results.e1"))
        .sections,
    ).toEqual({ charts: false });
    act(() => b[2]());
    expect(screen.getByTestId("b").textContent).toBe("undefined");
    expect(window.localStorage.getItem("fa.layout.proto-user.results.e1")).toBe(
      null,
    );
  });
});

const COLS = [
  { id: "name", label: "Name", locked: true, defaultPinned: "left" },
  { id: "rows", label: "Rows" },
  { id: "owner", label: "Owner", defaultVisible: false },
  { id: "tags", label: "Tags", defaultVisible: false },
  { id: "cost", label: "Cost", defaultVisible: false },
];

describe("useColumnLayout + ColumnsMenu", () => {
  it("shows the hidden chip, registers ⌘K Columns…, hides and resets", () => {
    let layout;
    function Grid() {
      layout = useColumnLayout({ surface: "list", columns: COLS });
      return (
        <MemoryRouter>
          <ColumnsMenu layout={layout} />
          <span data-testid="vis">{layout.visibleIds.join(",")}</span>
        </MemoryRouter>
      );
    }
    render(<Grid />);
    expect(screen.getByText("3 hidden")).toBeTruthy();
    expect(labels()).toContain("Columns…");
    expect(labels()).not.toContain("Reset columns");
    act(() => layout.hide("rows"));
    expect(screen.getByTestId("vis").textContent).toBe("name");
    expect(screen.getByText("4 hidden")).toBeTruthy();
    expect(labels()).toContain("Reset columns");
    act(() => layout.hide("name"));
    expect(screen.getByTestId("vis").textContent).toBe("name");
    act(() => layout.reset());
    expect(screen.getByTestId("vis").textContent).toBe("name,rows");
    // Opening from the chip filters to the hidden ones.
    fireEvent.click(screen.getByText("3 hidden"));
    expect(screen.getByText("Hidden (3)")).toBeTruthy();
    expect(screen.queryByText("Rows")).toBeNull();
    expect(screen.getByText("Owner")).toBeTruthy();
  });
});

describe("Section", () => {
  it("remembers the toggle, keeps its summary when collapsed, registers ⌘K", () => {
    const ui = () => (
      <Section
        id="charts"
        surface="leaderboard"
        title="Charts"
        summary="best #2D 91.0%"
      >
        <div>chart body</div>
      </Section>
    );
    const { unmount } = render(ui());
    expect(screen.getByText("chart body")).toBeTruthy();
    expect(labels()).toContain("Hide Charts");
    fireEvent.click(screen.getByRole("button", { name: /Charts/ }));
    expect(labels()).toContain("Show Charts");
    expect(screen.getAllByText("best #2D 91.0%").length).toBeGreaterThan(0);
    unmount();
    expect(labels()).not.toContain("Show Charts");
    // Reload (new store): still collapsed.
    useLayoutStore.setState({ byKey: {} });
    render(ui());
    expect(screen.queryByText("chart body")).toBeNull();
    expect(
      screen
        .getByRole("button", { name: /Charts/ })
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });
});

describe("Hint", () => {
  it("one visible per screen; × hides it for good; recall + Show tips bring it back", () => {
    render(
      <>
        <HintRecall id="first-run" />
        <Hint id="first-run">Pick two ways to run the same tasks.</Hint>
        <Hint id="second">Second tip</Hint>
      </>,
    );
    expect(
      screen.getByText("Pick two ways to run the same tasks."),
    ).toBeTruthy();
    expect(screen.queryByText("Second tip")).toBeNull();
    expect(screen.queryByLabelText("Show the tip for this page")).toBeNull();
    fireEvent.click(screen.getByLabelText("Got it · hide this tip"));
    expect(
      screen.queryByText("Pick two ways to run the same tasks."),
    ).toBeNull();
    expect(
      JSON.parse(window.localStorage.getItem("fa.hints.proto-user")),
    ).toEqual(["first-run"]);
    // The next tip takes its place; the recall button appears.
    expect(screen.getByText("Second tip")).toBeTruthy();
    expect(screen.getByLabelText("Show the tip for this page")).toBeTruthy();
    act(() => showTipsOnThisPage(null));
    expect(
      screen.getByText("Pick two ways to run the same tasks."),
    ).toBeTruthy();
    expect(window.localStorage.getItem("fa.hints.proto-user")).toBe(null);
  });

  it("doneWhen hides it once the user did the taught action", () => {
    function Page({ done }) {
      useEffect(() => {}, [done]);
      return (
        <Hint id="select" doneWhen={done}>
          Point at a row to select it.
        </Hint>
      );
    }
    const { rerender } = render(<Page done={false} />);
    expect(screen.getByText("Point at a row to select it.")).toBeTruthy();
    rerender(<Page done />);
    expect(screen.queryByText("Point at a row to select it.")).toBeNull();
    rerender(<Page done={false} />);
    expect(screen.queryByText("Point at a row to select it.")).toBeNull();
    expect(
      JSON.parse(window.localStorage.getItem("fa.hints.proto-user")),
    ).toEqual(["select"]);
  });
});
