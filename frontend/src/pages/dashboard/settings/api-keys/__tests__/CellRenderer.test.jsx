import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import CellRenderer from "../CellRenderer";
import { KEY_SWITCH_DENIED } from "src/sections/keys/keys.constants";

const openMenu = async (data) => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CellRenderer column={{ colDef: { field: "id" } }} data={data} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button"));
};

describe("API key actions", () => {
  it("disables turning off a key the user may not switch, and says why", async () => {
    await openMenu({
      id: "k1",
      key_name: "Teammate key",
      enabled: true,
      can_switch: false,
    });

    const disable = screen.getByRole("menuitem", { name: "Disable Key" });
    expect(disable).toHaveAttribute("aria-disabled", "true");
    await userEvent.hover(disable.closest("div"));
    await waitFor(() =>
      expect(screen.getByRole("tooltip")).toHaveTextContent(KEY_SWITCH_DENIED),
    );
  });

  it("keeps turning off available for a key the user may switch", async () => {
    await openMenu({
      id: "k2",
      key_name: "My key",
      enabled: true,
      can_switch: true,
    });

    expect(
      screen.getByRole("menuitem", { name: "Disable Key" }),
    ).not.toHaveAttribute("aria-disabled", "true");
  });
});
