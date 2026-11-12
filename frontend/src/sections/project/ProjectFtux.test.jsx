import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderWithRouter, screen } from "src/utils/test-utils";

vi.mock("./NewProject/NewObserve", () => ({
  default: () => <div>Setup instructions</div>,
}));
vi.mock("./NewProject/NewExperiment", () => ({
  default: () => <div>Prototype setup</div>,
}));

import ProjectFtux from "./ProjectFtux";

const WAITING =
  "Waiting for your first trace. This page updates when it arrives.";

describe("ProjectFtux waiting cue (OBS-SW-100)", () => {
  it("tells a new Observe user the page is waiting for their first trace", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/observe" });

    expect(screen.getByText("Welcome to Observe")).toBeInTheDocument();
    expect(screen.getByText("Setup instructions")).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(WAITING);
  });

  it("does not show the trace waiting cue on the Prototype setup page", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/prototype" });

    expect(screen.getByText("Welcome to Prototype")).toBeInTheDocument();
    expect(screen.queryByText(WAITING)).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
