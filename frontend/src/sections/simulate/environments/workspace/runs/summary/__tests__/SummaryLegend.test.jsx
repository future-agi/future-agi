import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import SummaryLegend from "../SummaryLegend";

const EVALS = [
  "Task success",
  "Policy adherence",
  "Lead qualification",
  "Tone",
  "Brevity",
].map((name, i) => ({ id: `e${i}`, name, color: "#16A34A" }));

describe("SummaryLegend", () => {
  it("shows the first three evals and folds the rest into a +N more chip", () => {
    render(<SummaryLegend evals={EVALS} onHighlight={vi.fn()} />);
    expect(screen.getByText("Task success")).toBeInTheDocument();
    expect(screen.getByText("Lead qualification")).toBeInTheDocument();
    expect(screen.queryByText("Tone")).toBeNull();
    expect(screen.getByText("+2 more")).toBeInTheDocument();
  });

  it("lists the folded evals on hovering the chip", async () => {
    const user = userEvent.setup();
    render(<SummaryLegend evals={EVALS} onHighlight={vi.fn()} />);

    await user.hover(screen.getByText("+2 more"));

    expect(await screen.findByText("Tone")).toBeInTheDocument();
    expect(screen.getByText("Brevity")).toBeInTheDocument();
  });

  it("highlights an eval while hovered and clears it on leave", () => {
    const onHighlight = vi.fn();
    render(<SummaryLegend evals={EVALS} onHighlight={onHighlight} />);
    const item = screen.getByText("Task success");
    fireEvent.mouseEnter(item);
    expect(onHighlight).toHaveBeenLastCalledWith("e0");
    fireEvent.mouseLeave(item);
    expect(onHighlight).toHaveBeenLastCalledWith(null);
  });

  it("is not a control — hiding evals is the eval picker's job", () => {
    render(<SummaryLegend evals={EVALS} onHighlight={vi.fn()} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows no chip when every eval fits", () => {
    render(<SummaryLegend evals={EVALS.slice(0, 3)} onHighlight={vi.fn()} />);
    expect(screen.queryByText(/more$/)).toBeNull();
  });

  it("caps the +N more list's height and scrolls it, so a long eval set stays on screen", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 25 }, (_, i) => ({
      id: `m${i}`,
      name: `Eval ${i}`,
      color: "#16A34A",
    }));
    render(<SummaryLegend evals={many} onHighlight={vi.fn()} />);

    await user.hover(screen.getByText("+22 more"));
    const list = (await screen.findByText("Eval 24")).closest(
      "[data-legend-overflow]",
    );

    expect(window.getComputedStyle(list).maxHeight).toBe("240px");
    expect(window.getComputedStyle(list).overflowY).toBe("auto");
  });
});
