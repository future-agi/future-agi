import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// TH-8084 option 1: invites on a self-hosted install, licensed or not, are
// shared as links (no mail delivery assumed), so "Resend invite" is hidden.
// Cloud keeps it.

const h = vi.hoisted(() => ({ mode: "oss" }));

vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: h.mode,
    isOSS: h.mode === "oss",
    isEE: h.mode === "ee",
    isCloud: h.mode === "cloud",
    isSelfHosted: h.mode !== "cloud",
    isLoading: false,
    isSuccess: true,
  }),
}));

import ShowActionMenus from "../ShowActionMenus";

const MENUS = {
  Pending: [
    { action: "resend-invite", title: "Resend invite" },
    { action: "cancel-invite", title: "Cancel invite" },
  ],
};

function renderMenu(mode) {
  h.mode = mode;
  render(
    <ShowActionMenus
      id="m"
      actionRef={{ current: document.body }}
      open
      onClose={() => {}}
      data={{ status: "Pending" }}
      setOpenActionForm={() => {}}
      menusByStatus={MENUS}
    />,
  );
}

describe("ShowActionMenus resend-invite", () => {
  it.each(["oss", "ee"])("%s: hides Resend invite", (mode) => {
    renderMenu(mode);
    expect(screen.queryByText("Resend invite")).toBeNull();
    expect(screen.getByText("Cancel invite")).toBeTruthy();
  });

  it("cloud: keeps Resend invite", () => {
    renderMenu("cloud");
    expect(screen.getByText("Resend invite")).toBeTruthy();
  });
});
