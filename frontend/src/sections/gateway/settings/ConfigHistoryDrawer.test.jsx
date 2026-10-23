import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ConfigHistoryDrawer from "./ConfigHistoryDrawer";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The role check: activating a version is Owner or
// Admin only. The cases below run as an Admin unless one says otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

const TWO_VERSIONS = [
  { id: "cfg-2", version: 2, is_active: true, guardrails: {}, routing: {} },
  { id: "cfg-1", version: 1, is_active: false, guardrails: {}, routing: {} },
];

const mockActivate = vi.fn();

let mockHistoryReturn = {
  data: [],
  isLoading: false,
};

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfigHistory: () => mockHistoryReturn,
  useActivateOrgConfig: () => ({ mutate: mockActivate, isPending: false }),
}));

describe("ConfigHistoryDrawer", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s can view a version but Activate is disabled",
    (_who, level) => {
      mockOrgLevel = level;
      mockHistoryReturn = { data: TWO_VERSIONS, isLoading: false };
      render(<ConfigHistoryDrawer open onClose={vi.fn()} />);
      screen
        .getAllByRole("button", { name: "View" })
        .forEach((view) => expect(view).toBeEnabled());
      expectAdminOnly(screen.getByRole("button", { name: "Activate" }));
      expect(mockActivate).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can activate a version", (_who, level) => {
    mockOrgLevel = level;
    mockHistoryReturn = { data: TWO_VERSIONS, isLoading: false };
    render(<ConfigHistoryDrawer open onClose={vi.fn()} />);
    expectEditable(screen.getByRole("button", { name: "Activate" }));
  });

  beforeEach(() => {
    mockOrgLevel = 8;
    mockActivate.mockReset();
    mockHistoryReturn = {
      data: [],
      isLoading: false,
    };
  });

  it("renders snake_case history metadata and hides activate for active config", async () => {
    const user = userEvent.setup();
    const createdAt = "2026-04-14T08:34:37.245253Z";

    mockHistoryReturn = {
      data: [
        {
          id: "cfg-1",
          version: 48,
          is_active: true,
          created_at: createdAt,
          change_description: "Update guardrail pii-detector",
          guardrails: { rules: [] },
          routing: {},
          cache: {},
        },
      ],
      isLoading: false,
    };

    render(<ConfigHistoryDrawer open onClose={vi.fn()} />);

    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(
      screen.getByText(new Date(createdAt).toLocaleDateString()),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Update guardrail pii-detector"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /activate/i }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /view/i }));

    expect(screen.queryByText(/"providers"/)).not.toBeInTheDocument();
  });
});
