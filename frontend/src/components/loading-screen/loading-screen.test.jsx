import { describe, it, expect } from "vitest";
import { render, screen } from "../../utils/test-utils";
import LoadingScreen from "./loading-screen";

describe("LoadingScreen", () => {
  it("renders without crashing", () => {
    render(<LoadingScreen />);
    expect(screen.getByText("Preparing for liftoff")).toBeInTheDocument();
  });

  it("exposes role=status for assistive technology", () => {
    render(<LoadingScreen />);
    const status = screen.getByRole("status");
    expect(status).toBeInTheDocument();
    expect(status).toHaveAttribute("aria-busy", "true");
  });

  it("applies custom sx props correctly", () => {
    render(<LoadingScreen data-testid="loading-wrapper" sx={{ backgroundColor: "red" }} />);
    expect(screen.getByTestId("loading-wrapper")).toBeInTheDocument();
  });

  it("forwards additional props to the Box component", () => {
    render(<LoadingScreen data-testid="custom-loading-screen" />);
    const container = screen.getByTestId("custom-loading-screen");
    expect(container).toBeInTheDocument();
  });

  it("renders the rocket variant by default", () => {
    render(<LoadingScreen />);
    expect(screen.getByText("Preparing for liftoff")).toBeInTheDocument();
  });

  it("renders the orbit variant with standby text", () => {
    render(<LoadingScreen variant="orbit" />);
    expect(screen.getByText("Standing by")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
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
});
