import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  renderWithRouter,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";

vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import ActionsCellRenderer from "../UserCellRenderers/ActionsCellRenderer";

// View Traces / View Sessions open the user's detail page. That page defaults
// to Today, so the menu must carry the Users list's date window with it.
const openFromMenu = async (label, search = "") => {
  renderWithRouter(<ActionsCellRenderer data={{ user_id: "alice" }} />, {
    route: `/dashboard/users${search}`,
  });
  await userEvent.click(screen.getByRole("button"));
  await userEvent.click(await screen.findByText(label));
  await waitFor(() =>
    expect(window.location.pathname).toBe("/dashboard/users/alice"),
  );
  return new URLSearchParams(window.location.search);
};

describe("ActionsCellRenderer opens the user on the list's date window", () => {
  const listWindow = {
    dateFilter: ["2026-10-01 00:00:00", "2026-10-31 23:59:59"],
    dateOption: "Custom",
  };

  it.each([
    { label: "View Sessions", userTab: "sessions" },
    { label: "View Traces", userTab: "traces" },
  ])(
    "$label keeps userTab and forwards the selected window",
    async ({ label, userTab }) => {
      const current = await openFromMenu(
        label,
        `?${new URLSearchParams({ userDateFilter: JSON.stringify(listWindow) })}`,
      );

      expect(current.get("userTab")).toBe(userTab);
      expect(current.get("sessionDateFilter")).toBe(JSON.stringify(listWindow));
      expect(current.get("primaryTraceDateFilter")).toBe(
        JSON.stringify(listWindow),
      );
    },
  );

  it("forwards the list's default Past 7D window when none is in the URL", async () => {
    const current = await openFromMenu("View Sessions");

    expect(current.get("userTab")).toBe("sessions");
    const sessionWindow = current.get("sessionDateFilter");
    expect(sessionWindow).not.toBeNull();
    expect(JSON.parse(sessionWindow).dateOption).toBe("7D");
    expect(current.get("primaryTraceDateFilter")).toBe(sessionWindow);
  });
});
