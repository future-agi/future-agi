// The key actions menu closes when one of its actions opens a confirmation,
// so the menu does not stay open behind "Delete key?" (or Disable, or
// Re-enable). Both keys pages: Keys (ActionMenu) and Settings > API keys (its
// cell renderer's Actions column). An open dialog hides the rest of the page
// from role queries, so the menu is looked for with hidden: true.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ActionMenu from "src/sections/keys/view/ActionMenu";
import SettingsKeyCell from "src/pages/dashboard/settings/api-keys/CellRenderer";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  endpoints: {
    keys: {
      disablekey: "/accounts/key/disable_key/",
      deleteKey: "/accounts/key/delete_secret_key/",
      enableKey: "/accounts/key/enable_key/",
    },
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

function withClient(node) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

const row = { id: "fixture-key-id", type: "user", key_name: "Fixture key" };

async function openFromMenu(button, menuText) {
  fireEvent.click(button);
  const menu = await screen.findByRole("menu");
  fireEvent.click(within(menu).getByText(menuText));
  return screen.findByRole("dialog");
}

const menuGone = () =>
  waitFor(() =>
    expect(screen.queryByRole("menu", { hidden: true })).toBeNull(),
  );

describe("Keys: the actions menu closes when its dialog opens", () => {
  it.each([
    [true, "Delete key", "Delete key?"],
    [true, "Disable key", "Disable key?"],
    [false, "Re-enable key", "Re-enable key?"],
  ])("enabled=%s: %s", async (enabled, menuText, title) => {
    render(withClient(<ActionMenu data={{ ...row, enabled }} />));
    const button = screen.getByRole("button", {
      name: "Actions for Fixture key",
    });
    const dialog = await openFromMenu(button, menuText);
    expect(within(dialog).getByRole("heading")).toHaveTextContent(title);
    await menuGone();
    expect(button).not.toHaveAttribute("aria-expanded", "true");
  });
});

describe("Settings > API keys: the menu closes when its dialog opens", () => {
  it.each([
    [true, "Delete Key", "Delete Key?"],
    [true, "Disable Key", "Disable Key?"],
    [false, "Re-enable key", "Re-Enable API Key"],
  ])("enabled=%s: %s", async (enabled, menuText, title) => {
    render(
      withClient(
        <SettingsKeyCell
          data={{ ...row, enabled }}
          column={{ colDef: { field: "id" } }}
        />,
      ),
    );
    const button = screen.getByRole("button", { name: "Actions" });
    const dialog = await openFromMenu(button, menuText);
    expect(within(dialog).getByRole("heading")).toHaveTextContent(title);
    await menuGone();
  });
});
