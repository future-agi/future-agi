import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { Button, Tab } from "@mui/material";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import Section from "./Section";
import DataTablePagination from "../data-table/DataTablePagination";
import MetricStrip from "../metric-strip/MetricStrip";
import SideDrawer from "../side-drawer/SideDrawer";
import { SegmentedTabs } from "../tabs/tabs";

const theme = createTheme({
  palette: palette("dark"),
  typography,
  customShadows: customShadows("dark"),
});
const wrap = (node) =>
  render(<ThemeProvider theme={theme}>{node}</ThemeProvider>);
describe("canonical foundation behavior", () => {
  it("retains pagination callbacks, boundaries and loading feedback", () => {
    const change = vi.fn();
    const size = vi.fn();
    wrap(
      <DataTablePagination
        page={0}
        pageSize={10}
        total={21}
        onPageChange={change}
        onPageSizeChange={size}
        loading
      />,
    );
    expect(screen.getByLabelText("Previous page").disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Next page"));
    expect(change).toHaveBeenCalledWith(1);
    fireEvent.change(screen.getByLabelText("Rows per page"), {
      target: { value: "25" },
    });
    expect(size).toHaveBeenCalledWith(25);
    expect(screen.getByRole("status").textContent).toContain("Loading page");
  });
  it("does not invent metric values or navigation tiles", () => {
    wrap(
      <MetricStrip
        items={[
          { label: "Latency", value: null },
          { label: "Errors", value: 0 },
        ]}
      />,
    );
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("supports keyboard panel activation but not disabled panels", () => {
    const action = vi.fn();
    const { rerender } = wrap(<Section title="Open result" onClick={action} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(action).toHaveBeenCalledTimes(1);
    rerender(<Section title="Open result" disabled onClick={action} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(action).toHaveBeenCalledTimes(1);
  });
  it("keeps tab selection controlled by the caller", () => {
    const change = vi.fn();
    wrap(
      <SegmentedTabs value="first" onChange={change}>
        <Tab value="first" label="First" />
        <Tab value="second" label="Second" />
      </SegmentedTabs>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Second" }));
    expect(change.mock.calls[0][1]).toBe("second");
  });
  it("closes the top drawer on Escape without closing its parent", () => {
    function Nested() {
      const [inner, setInner] = useState(false);
      return (
        <SideDrawer open PaperProps={{ "aria-label": "Outer" }}>
          <Button onClick={() => setInner(true)}>Open inner</Button>
          <SideDrawer
            open={inner}
            onClose={() => setInner(false)}
            PaperProps={{ "aria-label": "Inner" }}
          >
            <Button>Inner action</Button>
          </SideDrawer>
        </SideDrawer>
      );
    }
    wrap(<Nested />);
    fireEvent.click(screen.getByText("Open inner"));
    const inner = screen.getByRole("dialog", { name: "Inner" });
    fireEvent.keyDown(inner, { key: "Escape", keyCode: 27 });
    expect(screen.getByText("Open inner")).toBeTruthy();
  });
});
