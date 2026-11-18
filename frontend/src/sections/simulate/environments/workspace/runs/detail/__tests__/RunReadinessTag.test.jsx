import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RunReadinessTag from "../RunReadinessTag";

const row = (id, status, extra = {}) => ({
  id,
  label: id,
  status,
  measured: true,
  reason: null,
  ...extra,
});

const READY = {
  answer: { kind: "ready" },
  rows: [
    row("start", "supported"),
    row("tool_routing", "supported"),
    // Unmeasured, and a Stop not seen yet: neither is a limit.
    row("files", "untested", { measured: false }),
    row("cancel", "untested"),
  ],
};

const LIMITED = {
  answer: { kind: "blocked" },
  rows: [
    row("start", "supported", { label: "Agent starts" }),
    row("tool_routing", "blocked", {
      label: "Controlled tool routing",
      reason: "Failed its probe: issue_refund",
    }),
  ],
};

const HELD = {
  requested: 10,
  running: 8,
  held_back: { count: 2, keys: ["r1", "r2"], truncated: false, by: {} },
  reasons: { tool_routing: "Failed its probe: issue_refund" },
};

describe("RunReadinessTag", () => {
  it("renders nothing for a run that kept no readiness", () => {
    const { container } = render(<RunReadinessTag snapshot={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("reads ready when nothing was held back", () => {
    render(<RunReadinessTag snapshot={READY} selection={null} />);

    expect(screen.getByText("Ready when started")).toBeInTheDocument();
  });

  it("counts a Partial row on a ready answer instead of reading ready", async () => {
    const partial = {
      answer: { kind: "ready" },
      rows: [
        row("start", "supported"),
        row("files", "partial", { label: "Files", reason: "PDF only" }),
      ],
    };
    render(<RunReadinessTag snapshot={partial} selection={null} />);

    await userEvent.click(screen.getByText("Started with 1 limit"));
    expect(screen.queryByText("Ready when started")).toBeNull();
    expect(screen.getByText("Files: Partial, PDF only")).toBeInTheDocument();
  });

  it("counts the rows not Supported and lists them with the held-back cases", async () => {
    render(<RunReadinessTag snapshot={LIMITED} selection={HELD} />);

    await userEvent.click(screen.getByText("Started with 1 limit"));

    expect(
      screen.getByText(
        "Controlled tool routing: Blocked, Failed its probe: issue_refund",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Agent starts:/)).not.toBeInTheDocument();
    expect(screen.getByText("2 of 10 cases held back")).toBeInTheDocument();
  });

  it("counts neither an unmeasured row nor a Stop not seen yet as a limit", () => {
    const snapshot = {
      ...LIMITED,
      rows: [
        ...LIMITED.rows,
        row("files", "untested", { measured: false }),
        row("cancel", "untested"),
      ],
    };
    render(<RunReadinessTag snapshot={snapshot} selection={HELD} />);

    expect(screen.getByText("Started with 1 limit")).toBeInTheDocument();
  });

  it("is not ready when cases were held back, even with every row Supported", () => {
    render(<RunReadinessTag snapshot={READY} selection={HELD} />);

    expect(
      screen.getByText("Started with 2 cases held back"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/0 limits/)).not.toBeInTheDocument();
  });
});
