import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import Button from "@mui/material/Button";
import SurfaceIcon from "../SurfaceIcon";
import EmptyState from "../EmptyState";
import StatusChip from "../../workspace/runs/StatusChip";
import { PULSING_DOT_CLASS } from "../../workspace/runs/runs.constants";
import LivePill from "../../workspace/LivePill";

describe("SurfaceIcon", () => {
  it("renders the voice icon with the voice tone", () => {
    render(<SurfaceIcon surface="voice" />);
    const icon = screen.getByRole("img", { name: "Voice" });
    expect(icon).toBeInTheDocument();
    // Voice tone = BUILD_TONES.accent = #7857FC = rgb(120, 87, 252).
    expect(icon).toHaveStyle({ color: "rgb(120, 87, 252)" });
  });

  it("falls back to the first surface for an unknown id", () => {
    render(<SurfaceIcon surface="not-a-surface" />);
    expect(screen.getByRole("img", { name: "Voice" })).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("shows the title, body and action", () => {
    render(
      <EmptyState
        title="Nothing here yet"
        body="Add something to get started."
        action={<Button>Do it</Button>}
      />
    );
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByText("Add something to get started.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Do it" })).toBeInTheDocument();
  });
});

describe("StatusChip", () => {
  it("renders the running label with a pulsing dot", () => {
    const { container } = render(<StatusChip status="running" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(container.querySelector(`.${PULSING_DOT_CLASS}`)).toBeTruthy();
  });

  it("renders a settled status without a pulsing dot", () => {
    const { container } = render(<StatusChip status="passed" />);
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(container.querySelector(`.${PULSING_DOT_CLASS}`)).toBeNull();
  });

  it("falls back to Queued for an unknown status", () => {
    const { container } = render(<StatusChip status="mystery" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(container.querySelector(`.${PULSING_DOT_CLASS}`)).toBeNull();
  });
});

describe("LivePill", () => {
  it("renders the Live label", () => {
    render(<LivePill />);
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});
