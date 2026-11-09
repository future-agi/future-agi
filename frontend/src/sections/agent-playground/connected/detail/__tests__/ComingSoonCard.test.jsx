import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ComingSoonCard from "../ComingSoonCard";

describe("ComingSoonCard", () => {
  it("renders no unbuilt area, description, chip or action", () => {
    render(
      <ComingSoonCard
        title="File structure"
        description="Browse the files we read from your agent."
      />,
    );
    expect(screen.queryByRole("region", { name: "File structure" })).toBeNull();
    expect(screen.queryByText("File structure")).toBeNull();
    expect(screen.queryByLabelText("Coming soon")).toBeNull();
    expect(
      screen.queryByText("Browse the files we read from your agent."),
    ).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
