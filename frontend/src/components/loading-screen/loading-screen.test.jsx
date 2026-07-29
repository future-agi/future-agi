import { describe, it, expect } from "vitest";
import { render, screen } from "../../utils/test-utils";
import LoadingScreen from "./loading-screen";

describe("LoadingScreen", () => {
  it("renders without crashing", () => {
    render(<LoadingScreen />);
    expect(screen.getByText("Preparing for liftoff")).toBeInTheDocument();
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
  });

  it("renders a custom message override", () => {
    render(<LoadingScreen message="Custom message" />);
    expect(screen.getByText("Custom message")).toBeInTheDocument();
  });

  describe("accessibility", () => {
    it("renders a container element", () => {
      render(<LoadingScreen data-testid="a11y-check" />);
      expect(screen.getByTestId("a11y-check")).toBeInTheDocument();
    });
  });
});
