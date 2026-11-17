/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import AuthClassicLayout from "../classic";

const { rootProps } = vi.hoisted(() => ({ rootProps: vi.fn() }));

vi.mock("src/utils/Mixpanel", () => ({
  Events: { pageView: "page-view" },
  getPageViewEvent: vi.fn(),
  trackEvent: vi.fn(),
}));

// Inspect the root Stack's styling contract, not jsdom's nonexistent layout.
vi.mock("@mui/material/Stack", () => ({
  default: function Stack({ children, ...props }) {
    rootProps(props);
    return <main>{children}</main>;
  },
}));

const theme = createTheme();

function renderLayout() {
  render(
    <MemoryRouter initialEntries={["/auth/jwt/login"]}>
      <AuthClassicLayout>
        <div>Auth form</div>
      </AuthClassicLayout>
    </MemoryRouter>,
  );
  return rootProps.mock.lastCall[0];
}

function stylesAtWidth(sx, width) {
  const resolved = theme.unstable_sx(sx);
  const styles = {};
  for (const [key, value] of Object.entries(resolved)) {
    const minWidth = key.match(/^@media \(min-width:(\d+)px\)$/);
    if (minWidth) {
      if (width >= Number(minWidth[1])) Object.assign(styles, value);
    } else {
      styles[key] = value;
    }
  }
  return styles;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AuthClassicLayout responsive root", () => {
  it("removes the 1200px minimum and occupies the available width", () => {
    const { sx } = renderLayout();
    expect(sx.minWidth).toBe(0);
    expect(sx.width).toBe(1);
  });

  it("keeps the full-height main landmark, children, and background", () => {
    const { component, sx } = renderLayout();
    expect(component).toBe("main");
    expect(screen.getByText("Auth form")).toBeTruthy();
    expect(sx).toMatchObject({
      minHeight: "100vh",
      background: "url('/assets/illustrations/auth-background.png')",
      backgroundRepeat: "no-repeat",
      backgroundSize: "100% 100%",
    });
  });

  it.each([
    [390, "column"],
    [768, "column"],
    [1280, "row"],
  ])("uses %s px styling with %s direction", (width, flexDirection) => {
    const { direction, sx } = renderLayout();
    expect(
      stylesAtWidth({ flexDirection: direction, ...sx }, width),
    ).toMatchObject({
      flexDirection,
    });
  });
});
