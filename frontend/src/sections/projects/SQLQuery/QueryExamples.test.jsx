import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import QueryExamples from "./QueryExamples";

const examples = [
  {
    id: "cost-tokens-by-model",
    title: "Cost and tokens by model",
    description: "Spans, total tokens and total cost per model.",
    sql: "SELECT model, count() AS span_count FROM spans GROUP BY model",
    datasets: ["spans"],
  },
  {
    id: "derived-metric-over-time",
    title: "Derived metric over time",
    description: "Latest committed value per window.",
    sql: "SELECT metric_id FROM derived_metrics",
    datasets: ["derived_metrics"],
  },
];
const datasets = [
  { name: "spans", available: true },
  { name: "derived_metrics", available: false },
];

describe("QueryExamples", () => {
  it("lists every example and uses one with a single click", () => {
    const onUse = vi.fn();
    render(
      <QueryExamples examples={examples} datasets={datasets} onUse={onUse} />,
    );
    expect(screen.getByText("Example queries (2)")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Example queries" });
    expect(list.querySelectorAll('[role="button"]')).toHaveLength(2);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Use example: Cost and tokens by model",
      }),
    );
    expect(onUse).toHaveBeenCalledTimes(1);
    expect(onUse).toHaveBeenCalledWith(examples[0]);
  });

  it("disables an example whose dataset is unavailable and says why", () => {
    const onUse = vi.fn();
    render(
      <QueryExamples examples={examples} datasets={datasets} onUse={onUse} />,
    );
    const blocked = screen.getByRole("button", {
      name: "Use example: Derived metric over time",
    });
    expect(blocked).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("Needs derived_metrics (unavailable)"),
    ).toBeInTheDocument();
    fireEvent.click(blocked);
    expect(onUse).not.toHaveBeenCalled();
  });

  it("searches descriptions and datasets while retaining the vetted SQL", () => {
    const onUse = vi.fn();
    const { rerender } = render(
      <QueryExamples
        examples={examples}
        datasets={datasets}
        onUse={onUse}
        search="per model"
      />,
    );
    expect(screen.getByText(examples[0].description)).toBeVisible();
    expect(screen.queryByText(examples[1].title)).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: `Use example: ${examples[0].title}` }),
    );
    expect(onUse).toHaveBeenCalledWith(examples[0]);
    rerender(
      <QueryExamples
        examples={examples}
        datasets={datasets}
        onUse={onUse}
        search="no match"
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "No matching examples",
    );
  });

  it("renders nothing without examples", () => {
    const { container } = render(
      <QueryExamples examples={[]} datasets={datasets} onUse={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
