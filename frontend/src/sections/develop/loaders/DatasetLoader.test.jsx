import { describe, test, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import DatasetLoader from "./DatasetLoader";

describe("DatasetLoader", () => {
  const baseProps = {
    isSyntheticDataset: true,
    status: "failed-to-generate-synthetic",
  };

  test("renders failure reason from props when provided", () => {
    const reason = "Usage limit exceeded for organization";
    render(<DatasetLoader {...baseProps} failureReason={reason} />);

    expect(screen.getByText(reason)).toBeInTheDocument();
  });

  test("renders default fallback description when failureReason is omitted", () => {
    render(<DatasetLoader {...baseProps} />);

    expect(
      screen.getByText(
        "Something went wrong while generating synthetic data. Please click on configure rto re-generate or edit configuration",
      ),
    ).toBeInTheDocument();
  });

  test("renders default fallback description when failureReason is null", () => {
    render(<DatasetLoader {...baseProps} failureReason={null} />);

    expect(
      screen.getByText(
        "Something went wrong while generating synthetic data. Please click on configure rto re-generate or edit configuration",
      ),
    ).toBeInTheDocument();
  });

  test("renders default fallback description when failureReason is an empty string", () => {
    render(<DatasetLoader {...baseProps} failureReason="" />);

    // Empty string is falsy, so fallback should be shown
    expect(
      screen.getByText(
        "Something went wrong while generating synthetic data. Please click on configure rto re-generate or edit configuration",
      ),
    ).toBeInTheDocument();
  });

  test("shows failed title when status is failed-to-generate-synthetic", () => {
    render(<DatasetLoader {...baseProps} failureReason="KB indexing failed" />);

    expect(
      screen.getByText("Failed to generate synthetic data"),
    ).toBeInTheDocument();
  });

  test("does not show progress bar in failed state", () => {
    render(<DatasetLoader {...baseProps} failureReason="Some error" />);

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  test("shows progress bar when generating (non-failed status)", () => {
    render(
      <DatasetLoader
        {...baseProps}
        status="default-synthetic"
        failureReason={null}
      />,
    );

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  test("renders long failure messages without truncation", () => {
    const longReason =
      "Error processing column 'user_email': data type mismatch in row 42. " +
      "Expected string but received integer value 12345. " +
      "This occurred during the validation phase of synthetic data generation.";
    render(<DatasetLoader {...baseProps} failureReason={longReason} />);

    expect(screen.getByText(longReason)).toBeInTheDocument();
  });
});
