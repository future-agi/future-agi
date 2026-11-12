import React, { forwardRef } from "react";
import { act, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { GatewayGrid } from "..";

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

// An AG Grid api whose centre columns are `total` px wide, scrolled so that
// `left`..`right` is on screen.
const fakeApi = (total, left, right, state = []) => ({
  getHorizontalPixelRange: () => ({ left, right }),
  getDisplayedCenterColumns: () => [{ getActualWidth: () => total }],
  getColumnState: () => state,
});

const CUE = "gateway-grid-more-right";

function renderGrid(props) {
  return render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <GatewayGrid
        rowData={[{ id: "r1" }]}
        columnDefs={[{ field: "id" }]}
        {...props}
      />
    </ThemeProvider>,
  );
}

// A grid wider than its box scrolls sideways, but the
// scrollbar is hidden until hover on most systems, so nothing said so.
describe("GatewayGrid sideways-scroll cue (layout)", () => {
  afterEach(() => {
    grid.props = null;
    window.localStorage.clear();
  });

  it("shades the right edge while columns lie past it, and clears at the end", () => {
    renderGrid({ scrollCue: true });
    act(() => grid.props.onGridSizeChanged({ api: fakeApi(804, 0, 568) }));
    expect(screen.getByTestId(CUE)).toBeInTheDocument();
    act(() => grid.props.onBodyScroll({ api: fakeApi(804, 236, 804) }));
    expect(screen.queryByTestId(CUE)).toBeNull();
  });

  it("shows no cue when every column fits", () => {
    renderGrid({ scrollCue: true });
    act(() => grid.props.onFirstDataRendered({ api: fakeApi(804, 0, 900) }));
    expect(screen.queryByTestId(CUE)).toBeNull();
  });

  it("still calls the page's grid handlers and keeps saving the user's layout", () => {
    const onBodyScroll = vi.fn();
    const onFirstDataRendered = vi.fn();
    renderGrid({
      scrollCue: true,
      stateKey: "cue-test",
      onBodyScroll,
      onFirstDataRendered,
    });
    const saved = [{ colId: "id", width: 300 }];
    const api = fakeApi(804, 0, 568, saved);
    act(() => grid.props.onBodyScroll({ api }));
    act(() => grid.props.onFirstDataRendered({ api }));
    expect(onBodyScroll).toHaveBeenCalledTimes(1);
    expect(onFirstDataRendered).toHaveBeenCalledTimes(1);
    act(() => grid.props.onColumnResized({ api, source: "uiColumnResized" }));
    expect(
      JSON.parse(window.localStorage.getItem("gateway-grid:cue-test")),
    ).toEqual(saved);
  });

  it("leaves a grid that does not ask for the cue as it was", () => {
    const onBodyScroll = vi.fn();
    renderGrid({ onBodyScroll });
    expect(grid.props.onBodyScroll).toBe(onBodyScroll);
    expect(grid.props.onGridSizeChanged).toBeUndefined();
    expect(grid.props.scrollCue).toBeUndefined();
  });
});
