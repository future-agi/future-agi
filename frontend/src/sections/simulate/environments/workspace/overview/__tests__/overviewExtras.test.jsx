import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";

import { render, renderWithRouter } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import OverviewPanel from "../OverviewPanel";
import SourceToSandboxMap from "../SourceToSandboxMap";

describe("Overview extras wired into OverviewPanel", () => {
  const connected = {
    agent: { via: "endpoint", values: {} },
    scenarios: [],
  };

  it("renders the source-to-sandbox map with each row's sandbox target", () => {
    renderWithRouter(
      <OverviewPanel env={MOCK_WORLD} envState={connected} onGo={vi.fn()} agentConnected />,
      { route: "/x" },
    );
    // The product has always shown what each tool and rule becomes in the
    // sandbox; the port keeps it.
    expect(screen.getByText("How the world was built")).toBeInTheDocument();
    expect(screen.queryByText("Origin soon")).toBeNull();
    expect(screen.getAllByText(/enforced at the sandbox boundary|requires supervisor approval|blocks late cancels/).length).toBeGreaterThan(0);
  });
});

describe("SourceToSandboxMap", () => {
  // The sample adds Vel's hints and Other parties.
  const renderMap = (props = {}) => render(<SourceToSandboxMap env={MOCK_WORLD} sample {...props} />);

  it("renders the source and sandbox columns for the env", () => {
    renderMap();
    expect(screen.getByText("How the world was built")).toBeInTheDocument();
    expect(screen.getByText("Read from source")).toBeInTheDocument();
    expect(screen.getByText("In the sandbox world")).toBeInTheDocument();
    // a mapped tool row and a seeded store row (issue_refund is unique to the
    // tools group; verify_identity also stands in as the external-service actor)
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
    expect(screen.getByText("customers · 240")).toBeInTheDocument();
  });

  it("is a read-only ledger — no resolve homework (that moved to the Contract tab)", () => {
    renderMap();
    // Every tool row states a sandbox target rather than holding one open with a
    // "Needs your answer" prompt; the read/write override lives on Contract now.
    expect(screen.queryByText("Needs your answer")).toBeNull();
    expect(screen.queryByRole("button", { name: /confirm read-only/i })).toBeNull();
  });

  it("reflects a tool-effect override set on the Contract tab (you-confirmed + overridden target)", () => {
    // issue_refund heuristically classifies as a write ("writes to the sandbox").
    // A human override to read-only on Contract stores the singular "read"; the
    // map normalizes it and shows the overridden target + a "you confirmed" mark.
    renderMap({ envState: { toolResolutions: { issue_refund: "read" } } });
    expect(screen.getByText("you confirmed")).toBeInTheDocument();
    expect(screen.getAllByText("reads from fixture state").length).toBeGreaterThan(0);
  });
});
