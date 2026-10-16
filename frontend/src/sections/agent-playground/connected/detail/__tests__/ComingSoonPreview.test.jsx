import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ComingSoonPreview from "../ComingSoonPreview";

describe("ComingSoonPreview", () => {
  const renderPreview = () =>
    render(
      <ComingSoonPreview
        title="File structure"
        description="Browse the files we read from your agent."
      >
        <button type="button">Inside the preview</button>
      </ComingSoonPreview>,
    );

  it("names the area and says what it will do", () => {
    renderPreview();
    expect(
      screen.getByRole("region", { name: "File structure" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();
    expect(
      screen.getByText("Browse the files we read from your agent."),
    ).toBeInTheDocument();
  });

  it("keeps the preview out of reach of keyboard and screen readers", () => {
    renderPreview();
    expect(
      screen.queryByRole("button", { name: "Inside the preview" }),
    ).toBeNull();
    const preview = screen.getByTestId("coming-soon-preview");
    expect(preview).toHaveAttribute("aria-hidden", "true");
    expect(preview).toHaveAttribute("inert");
  });
});
