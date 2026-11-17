import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import WorkspaceErrorBoundary from "../WorkspaceErrorBoundary";

const Boom = () => {
  throw new Error("kaboom");
};

describe("WorkspaceErrorBoundary", () => {
  it("renders children when nothing throws", () => {
    render(
      <WorkspaceErrorBoundary>
        <div>workspace ok</div>
      </WorkspaceErrorBoundary>,
    );
    expect(screen.getByText("workspace ok")).toBeInTheDocument();
  });

  it("catches a render error and shows a recoverable fallback instead of blanking", () => {
    // React logs caught render errors to console.error; silence it for the assertion.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <WorkspaceErrorBoundary>
        <Boom />
      </WorkspaceErrorBoundary>,
    );
    expect(screen.getByText(/couldn.t be displayed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to environments" })).toBeInTheDocument();
    spy.mockRestore();
  });
});
