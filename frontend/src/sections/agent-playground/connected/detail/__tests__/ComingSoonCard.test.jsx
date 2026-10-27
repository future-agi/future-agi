import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ComingSoonCard from "../ComingSoonCard";

describe("ComingSoonCard", () => {
  it("names the area, marks it Coming soon and says what it will do", () => {
    render(
      <ComingSoonCard
        title="File structure"
        description="Browse the files we read from your agent."
      />,
    );
    const region = screen.getByRole("region", { name: "File structure" });
    expect(region).toHaveTextContent("File structure");
    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();
    expect(
      screen.getByText("Browse the files we read from your agent."),
    ).toBeInTheDocument();
  });
});
