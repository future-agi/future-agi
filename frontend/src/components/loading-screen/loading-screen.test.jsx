import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "../../utils/test-utils";
import LoadingScreen from "./loading-screen";
import SplashScreen from "./splash-screen";
import LoadingTemplate from "../../sections/workbench/LoadingTemplate";

// \s also matches a non-breaking space, so "Mission&nbsp;Control" is caught.
const JOKES =
  /liftoff|oxygen|mission\s+control|warp|thrusters|hamsters|uplink|Systems\s+online|Calibrating/i;

describe("LoadingScreen", () => {
  it("renders without crashing", () => {
    render(<LoadingScreen />);
    expect(screen.getByText("Loading\u2026")).toBeInTheDocument();
  });

  it("exposes role=status for assistive technology", () => {
    render(<LoadingScreen />);
    const status = screen.getByRole("status");
    expect(status).toBeInTheDocument();
    expect(status).toHaveAttribute("aria-busy", "true");
  });

  it("applies custom sx props correctly", () => {
    render(
      <LoadingScreen
        data-testid="loading-wrapper"
        sx={{ backgroundColor: "red" }}
      />,
    );
    expect(screen.getByTestId("loading-wrapper")).toBeInTheDocument();
  });

  it("forwards additional props to the Box component", () => {
    render(<LoadingScreen data-testid="custom-loading-screen" />);
    const container = screen.getByTestId("custom-loading-screen");
    expect(container).toBeInTheDocument();
  });

  it("renders the rocket variant by default, with Loading and no joke line", () => {
    const { container } = render(<LoadingScreen />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading\u2026$/);
    expect(container.textContent).not.toMatch(JOKES);
  });

  it("renders the orbit variant with Loading and no joke line", () => {
    const { container } = render(<LoadingScreen variant="orbit" />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading\u2026$/);
    expect(container.textContent).not.toMatch(JOKES);
  });

  it("says Loading on every render, not a random line", () => {
    for (let i = 0; i < 20; i += 1) {
      const { unmount } = render(<LoadingScreen />);
      expect(screen.getByRole("status")).toHaveTextContent(/^Loading\u2026$/);
      unmount();
    }
  });

  it("renders a custom message override", () => {
    render(<LoadingScreen message="Custom message" />);
    expect(screen.getByText("Custom message")).toBeInTheDocument();
  });

  describe("accessibility", () => {
    it("has role=status with aria-busy on rocket variant", () => {
      render(<LoadingScreen />);
      const el = screen.getByRole("status");
      expect(el).toHaveAttribute("aria-live", "polite");
      expect(el).toHaveAttribute("aria-busy", "true");
    });

    it("has role=status with aria-busy on orbit variant", () => {
      render(<LoadingScreen variant="orbit" />);
      const el = screen.getByRole("status");
      expect(el).toHaveAttribute("aria-live", "polite");
      expect(el).toHaveAttribute("aria-busy", "true");
    });
  });

  it("the splash screen shows a Loading status line, no invented boot stages", () => {
    const { container } = render(<SplashScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading\u2026");
    expect(container.textContent).not.toMatch(
      /uplink|Authenticating mission|Calibrating evaluators|Systems online/,
    );
  });

  it("the splash screen says only the product name and Loading, with no themed subtitle", () => {
    const { container } = render(<SplashScreen />);
    expect(container.textContent).not.toMatch(JOKES);
    // All of its text: the wordmark, then the status line ("> Loading…").
    expect(container.textContent.replace(/\s+/g, " ")).toBe(
      "Future AGI>Loading…",
    );
  });

  it("the template loader says Loading with no joke line", () => {
    const { container } = render(<LoadingTemplate />);
    expect(screen.getByText("Loading\u2026")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(JOKES);
  });
});
