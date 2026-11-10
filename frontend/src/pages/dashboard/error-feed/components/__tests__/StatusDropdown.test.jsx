import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { StatusDropdown } from "../ErrorMetadataPanel";

const mocked = vi.hoisted(() => ({ mutate: vi.fn() }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useUpdateErrorFeedIssue: () => ({ mutate: mocked.mutate }),
  useCreateLinearIssue: vi.fn(),
  useErrorFeedSidebar: vi.fn(),
  useLinearTeams: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

describe("manual status intent", () => {
  it("lets a user explicitly select the existing For review status", () => {
    render(<StatusDropdown clusterId="S-TEST" current="for_review" />);
    fireEvent.click(screen.getByRole("button", { name: "Change status" }));
    expect(
      screen.getAllByRole("menuitem").map((item) => item.textContent),
    ).toEqual(["Escalating", "Acknowledged", "For review", "Resolved"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "For review" }));
    expect(mocked.mutate).toHaveBeenCalledWith({
      clusterId: "S-TEST",
      status: "for_review",
    });
  });

  it("shows an unknown status with the review fallback", () => {
    render(<StatusDropdown clusterId="S-TEST" current="unknown" />);
    expect(
      screen.getByRole("button", { name: "Change status" }),
    ).toHaveTextContent("For review");
    expect(screen.queryByText("Escalating")).not.toBeInTheDocument();
  });
});
