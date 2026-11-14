import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ActionMenu from "../ActionMenu";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), delete: vi.fn() },
  endpoints: {
    keys: {
      enableKey: "/accounts/key/enable_key/",
      disablekey: "/accounts/key/disable_key/",
      deleteKey: "/accounts/key/delete_secret_key/",
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

const openMenu = (data) => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ActionMenu data={data} onRefresh={vi.fn()} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button"));
};

describe("ActionMenu expiry", () => {
  it("offers Re-enable for a key an admin disabled", () => {
    openMenu({
      id: "k1",
      key_name: "disabled",
      type: "user",
      enabled: false,
      expires_at: null,
      is_expired: false,
    });

    expect(screen.getByText("Re-enable key")).toBeInTheDocument();
  });

  it("does not offer Re-enable for an expired key", () => {
    openMenu({
      id: "k2",
      key_name: "expired",
      type: "user",
      enabled: false,
      expires_at: "2026-01-15T12:00:00Z",
      is_expired: true,
    });

    expect(screen.queryByText("Re-enable key")).not.toBeInTheDocument();
    expect(screen.getByText("Delete Key")).toBeInTheDocument();
  });
});
