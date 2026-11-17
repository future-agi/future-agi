import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import NotInReleaseCard from "../NotInReleaseCard";

describe("NotInReleaseCard", () => {
  it("names the area, says Not available in this release and what it does", () => {
    render(
      <NotInReleaseCard
        title="File structure"
        description="Browse the files we read from your agent."
      />,
    );
    expect(NOT_IN_RELEASE).toBe("Not available in this release");
    const region = screen.getByRole("region", { name: "File structure" });
    expect(region).toHaveTextContent("File structure");
    expect(screen.getByText("Not available in this release")).toBeVisible();
    expect(
      screen.getByText("Browse the files we read from your agent."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });
});
