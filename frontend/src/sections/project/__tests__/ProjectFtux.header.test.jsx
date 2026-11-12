// The Observe first-run page reads as a work screen,
// not a centred hero. The title is the page's level-1 heading in the shared
// page header, with its one-line purpose; the waiting cue is a status chip in
// the header; the decorative logo glyph above the title is gone; the setup
// steps follow the header. Every piece of text stays.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderWithRouter, screen } from "src/utils/test-utils";

vi.mock("../NewProject/NewObserve", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div>Setup instructions</div>,
}));
vi.mock("../NewProject/NewExperiment", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div>Prototype setup</div>,
}));

import ProjectFtux from "../ProjectFtux";

const WAITING =
  "Waiting for your first trace. This page updates when it arrives.";
const PURPOSE =
  "Send traces from your agents to inspect every step, debug failures and track cost and latency.";

const comesBefore = (a, b) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

const nearestCommonAncestor = (a, b) => {
  let node = a.parentElement;
  while (node && !node.contains(b)) node = node.parentElement;
  return node;
};

describe("ProjectFtux page header", () => {
  it("titles the Observe first-run page with one level-1 page heading", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/observe" });

    expect(
      screen.getByRole("heading", { level: 1, name: "Welcome to Observe" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("keeps the purpose line and the waiting status in the header, above the setup steps", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/observe" });

    const heading = screen.getByRole("heading", {
      level: 1,
      name: "Welcome to Observe",
    });
    const purpose = screen.getByText(PURPOSE);
    const status = screen.getByRole("status");
    const steps = screen.getByText("Setup instructions");

    expect(status.textContent).toBe(WAITING);
    // The live dot is drawn beside the text and hidden from screen readers.
    expect(status.querySelector('[aria-hidden="true"]')).not.toBeNull();

    // Title, purpose and status form one header block that does not hold
    // the setup steps, and the steps come after it.
    const header = nearestCommonAncestor(heading, status);
    expect(header).not.toBeNull();
    expect(header.contains(purpose)).toBe(true);
    expect(header.contains(steps)).toBe(false);
    expect(comesBefore(heading, purpose)).toBe(true);
    expect(comesBefore(header, steps)).toBe(true);
  });

  it("drops the decorative logo glyph above the title", () => {
    const { container } = renderWithRouter(<ProjectFtux />, {
      route: "/dashboard/observe",
    });

    expect(container.querySelector('img[src="/favicon/logo.svg"]')).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("gives the Prototype first-run page the same header without the trace status", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/prototype" });

    expect(
      screen.getByRole("heading", { level: 1, name: "Welcome to Prototype" }),
    ).toBeInTheDocument();
    expect(screen.getByText(PURPOSE)).toBeInTheDocument();
    expect(screen.getByText("Prototype setup")).toBeInTheDocument();
    expect(screen.queryByText(WAITING)).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
