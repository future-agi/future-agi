import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import DatasetLoader from "./DatasetLoader";

vi.mock("src/components/svg-color/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

// When synthetic data generation fails, the data tab must say why, not only
// that something went wrong.
describe("DatasetLoader", () => {
  it("shows the reason synthetic data generation failed", () => {
    render(
      <DatasetLoader
        status="failed-to-generate-synthetic"
        isSyntheticDataset
        failureReason="Synthetic data generation limit reached for this billing period"
      />,
    );

    expect(
      screen.getByText("Failed to generate synthetic data"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Synthetic data generation limit reached for this billing period",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Something went wrong/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Configure Synthetic Data/ }),
    ).toBeInTheDocument();
  });

  it("falls back to the generic message when no reason was recorded", () => {
    render(
      <DatasetLoader
        status="failed-to-generate-synthetic"
        isSyntheticDataset
      />,
    );

    expect(
      screen.getByText(/Something went wrong while generating synthetic data/),
    ).toBeInTheDocument();
  });

  it("keeps the generating message while generation is running", () => {
    render(
      <DatasetLoader
        status="default-synthetic"
        isSyntheticDataset
        failureReason="LLM provider timed out"
      />,
    );

    expect(
      screen.getByText(
        "We are generating your synthetic dataset, this might take some time",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("LLM provider timed out"),
    ).not.toBeInTheDocument();
  });
});
