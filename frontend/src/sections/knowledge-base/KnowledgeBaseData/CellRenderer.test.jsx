import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { render } from "src/utils/test-utils";

vi.mock("src/components/iconify", () => ({
  default: React.forwardRef(function Iconify(props, ref) {
    return <span ref={ref} data-testid="kb-file-error-icon" {...props} />;
  }),
}));

import { ProcessingStatusCell } from "./CellRenderer";

// Both the knowledge base list and the files table inside one render this.
describe("knowledge base ProcessingStatusCell", () => {
  it("shows why a failed file failed", async () => {
    const error =
      "Knowledge bases need the model serving service to embed documents";
    render(<ProcessingStatusCell value="Failed" data={{ error }} />);

    expect(screen.getByText("Failed")).toBeInTheDocument();
    fireEvent.mouseOver(screen.getByTestId("kb-file-error-icon"));
    expect(await screen.findByText(error)).toBeInTheDocument();
  });

  it("shows no empty tooltip when a failure has no reason", () => {
    render(<ProcessingStatusCell value="Failed" data={{}} />);

    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByTestId("kb-file-error-icon")).not.toBeInTheDocument();
  });

  it("shows no error affordance for completed files", () => {
    render(
      <ProcessingStatusCell
        value="Completed"
        data={{ error: "stale error" }}
      />,
    );

    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.queryByTestId("kb-file-error-icon")).not.toBeInTheDocument();
  });
});
