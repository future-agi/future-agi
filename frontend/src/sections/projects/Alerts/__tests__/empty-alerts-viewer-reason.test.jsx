import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import EmptyAlerts from "../components/EmptyAlerts";

const auth = vi.hoisted(() => ({ role: "Viewer" }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  handleOnDocsClicked: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const REASON =
  "Viewers can't create alerts. Ask a workspace admin for Member access.";

describe("EmptyAlerts — view-only teammates (W10-DESIGN-SYSTEM-U03)", () => {
  beforeEach(() => {
    auth.role = "Viewer";
  });

  it.each(["Viewer", "workspace_viewer"])(
    "tells a %s why Create alert is disabled",
    (role) => {
      auth.role = role;
      render(<EmptyAlerts onStartCreatingAlerts={vi.fn()} mainPage />);

      expect(
        screen.getByRole("button", { name: "Create alert" }),
      ).toBeDisabled();
      expect(screen.getByText(REASON)).toBeInTheDocument();
    },
  );

  it("shows no reason when the teammate can create alerts", () => {
    auth.role = "Member";
    render(<EmptyAlerts onStartCreatingAlerts={vi.fn()} mainPage />);

    expect(screen.getByRole("button", { name: "Create alert" })).toBeEnabled();
    expect(screen.queryByText(/can't create alerts/)).not.toBeInTheDocument();
  });
});
