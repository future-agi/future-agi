import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const navigate = vi.fn();

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));
vi.mock("../AddNewVersionDrawer", () => ({ default: () => null }));
vi.mock("src/components/VersionList/VersionList", () => ({
  default: () => null,
}));

const { default: VersionManagement } = await import("../VersionManagement");

describe("VersionManagement", () => {
  beforeEach(() => navigate.mockReset());

  it("sends a connected agent's versions to its agent page", () => {
    render(<VersionManagement agentId="agent-1" connected versions={[]} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Manage versions on the agent page" }),
    );

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/agents/connected/agent-1/versions",
    );
    expect(
      screen.queryByRole("button", { name: "Create new version" }),
    ).not.toBeInTheDocument();
  });

  it("keeps creating versions here for any other agent", () => {
    render(<VersionManagement agentId="agent-1" versions={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "Create new version" }));

    expect(navigate).not.toHaveBeenCalled();
  });
});
