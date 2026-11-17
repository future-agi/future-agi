// Keys page first-run polish: the default key name and the row actions button.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import CreateApiKey from "src/sections/keys/view/CreateApiKey";
import ActionMenu from "src/sections/keys/view/ActionMenu";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  endpoints: {
    keys: {
      generateSecretKey: "/accounts/key/generate_secret_key/",
      disablekey: "/synthetic/disable/",
      deleteKey: "/synthetic/delete/",
      enableKey: "/synthetic/enable/",
    },
  },
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { id: "fixture-person" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "fixture-org" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "fixture-workspace",
    currentWorkspaceName: "Fixture workspace",
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Keys create dialog default name", () => {
  // Local time, so the expected text does not depend on the time zone.
  const now = new Date(2026, 10, 10, 14, 15, 30, 123);
  const readable = () =>
    `Personal key, ${new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    })
      .format(now)
      .replace(/\s+/g, " ")}`;

  it("pre-fills a name a person would write, dated to the second in the viewer's locale", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(now);
    render(<CreateApiKey open onClose={vi.fn()} />);
    const value = screen.getByLabelText(/Key name/).value;
    expect(value).toBe(readable());
    expect(value).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
    expect(value).not.toMatch(/Z$/);
  });

  it("numbers the default when the loaded list already has that name", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(now);
    const base = readable();
    render(
      <CreateApiKey
        open
        onClose={vi.fn()}
        existingKeyNames={["Another key", base, `${base} (2)`]}
      />,
    );
    expect(screen.getByLabelText(/Key name/).value).toBe(`${base} (3)`);
  });

  it("keeps the plain default when only a numbered copy is in the list", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(now);
    const base = readable();
    render(
      <CreateApiKey
        open
        onClose={vi.fn()}
        existingKeyNames={[`${base} (2)`, `${base} `]}
      />,
    );
    expect(screen.getByLabelText(/Key name/).value).toBe(base);
  });
});

describe("Keys row actions button", () => {
  const row = {
    id: "fixture-key-id",
    type: "user",
    key_name: "Fixture key",
  };

  it("names the button for its key and says on hover what it opens", async () => {
    render(<ActionMenu data={{ ...row, enabled: true }} />);
    const button = screen.getByRole("button", {
      name: "Actions for Fixture key",
    });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    fireEvent.mouseOver(button);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Disable or delete this key",
    );
  });

  it("says re-enable on hover for a disabled key", async () => {
    render(<ActionMenu data={{ ...row, enabled: false }} />);
    fireEvent.mouseOver(
      screen.getByRole("button", { name: "Actions for Fixture key" }),
    );
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Re-enable or delete this key",
    );
  });

  it("opens a menu named by its button", async () => {
    render(<ActionMenu data={{ ...row, enabled: true }} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Actions for Fixture key" }),
    );
    const menu = await screen.findByRole("menu", {
      name: "Actions for Fixture key",
    });
    expect(within(menu).getByText("Disable key")).toBeInTheDocument();
    expect(within(menu).getByText("Delete key")).toBeInTheDocument();
  });
});
