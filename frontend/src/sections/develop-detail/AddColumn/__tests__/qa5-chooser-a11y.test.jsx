import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "src/utils/test-utils";
import { useAddColumnDrawerStore } from "../../states";

vi.mock("src/pages/dashboard/Develop/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ setActionSource: vi.fn() }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("../Expression/ExpressionColumnDialog", () => ({
  default: () => null,
}));
import AddColumnDrawer from "../AddColumnDrawer";

afterEach(() =>
  act(() => useAddColumnDrawerStore.setState({ openAddColumnDrawer: false })),
);

it("enters a named dialog, keeps Tab inside, uses keyboard category controls and closes on Escape", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <button
        type="button"
        onClick={() =>
          useAddColumnDrawerStore.setState({ openAddColumnDrawer: true })
        }
      >
        Open chooser
      </button>
      <AddColumnDrawer />
    </QueryClientProvider>,
  );
  const trigger = screen.getByRole("button", { name: "Open chooser" });
  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Add Columns" });
  await waitFor(() =>
    expect(dialog.contains(document.activeElement)).toBe(true),
  );
  for (let i = 0; i < 24; i += 1) {
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  }
  const all = screen.getByRole("radio", { name: "All" });
  all.focus();
  await user.keyboard("{ArrowDown}");
  expect(screen.getByRole("radio", { name: "Static Columns" })).toBeChecked();
  const text = screen.getByRole("button", { name: "Text" });
  text.focus();
  await user.keyboard("{Enter}");
  expect(
    screen.getByRole("textbox", { name: "Column name" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Close add columns" }),
  ).toBeInTheDocument();
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(trigger).toHaveFocus();
});
