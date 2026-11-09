import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import { screen, userEvent, waitFor, within } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import KeyManagementSection from "./KeyManagementSection";
import { renderPage as render } from "../guardrails/__tests__/helpers";

const state = vi.hoisted(() => ({
  orgLevel: 15,
  keys: [],
  revoke: vi.fn(),
  update: vi.fn(),
  sync: vi.fn(),
}));

// Keep the actual role hook, table, drawer and edit/revoke dialogs. All data
// and assets are synthetic; this suite must never need a network request.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: state.orgLevel,
    currentOrganizationId: "org-a",
    isReady: true,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "signed-in-user" } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock(
  "ag-grid-react",
  async () => await import("../providers/__tests__/grid-mock"),
);
vi.mock("../providers/ui/CreditBalanceChip", () => ({ default: () => null }));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("./CreateKeyDialog", () => ({ default: () => null }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "fixture-gateway" }),
}));
vi.mock("./hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: state.keys, isLoading: false }),
  useApiKeyDetail: (id) => ({
    data: state.keys.find((key) => key.id === id),
    isLoading: false,
  }),
  useSyncApiKeys: () => ({ mutate: state.sync, isPending: false }),
  useRevokeApiKey: () => ({ mutate: state.revoke, isPending: false }),
  useRotateApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateApiKey: () => ({
    mutate: state.update,
    isPending: false,
    isError: false,
  }),
}));
vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));

const roles = [
  { role: "Owner", level: 15, canEdit: true },
  { role: "Admin", level: 8, canEdit: true },
  { role: "Member", level: 3, canEdit: false },
  { role: "Viewer", level: 1, canEdit: false },
  { role: "Unknown", level: null, canEdit: false },
];

beforeEach(() => {
  vi.clearAllMocks();
  state.orgLevel = 15;
  state.keys = ["First", "Second"].map((name) => ({
    id: `fixture-${name.toLowerCase()}`,
    name: `${name} key`,
    status: "active",
    owner: `${name} owner`,
    user: "peer-user",
    organization: "org-a",
    allowedModels: [],
    allowedProviders: [],
  }));
});

async function tabTo(user, target) {
  for (let i = 0; i < 20 && document.activeElement !== target; i += 1) {
    await user.tab();
  }
  expect(target).toHaveFocus();
}

function detailButton(name) {
  return screen.getByRole("button", { name: `View details for ${name} key` });
}

async function openSecondKey(user, activation) {
  await tabTo(user, detailButton("First"));
  await tabTo(user, detailButton("Second"));
  expect(detailButton("Second")).toHaveFocus();
  await user.keyboard(activation);
  expect(
    await screen.findByRole("heading", { name: "Second key" }),
  ).toBeVisible();
}

describe("API key keyboard focus", () => {
  it.each(["light", "dark"])(
    "shows a persistent DS focus ring in %s mode",
    async (mode) => {
      const theme = createTheme({ palette: palette(mode) });
      const user = userEvent.setup();
      render(<KeyManagementSection />, { mode });
      const first = detailButton("First");
      const second = detailButton("Second");
      await tabTo(user, first);
      expect(first).toHaveClass("Mui-focusVisible");
      expect(first).toHaveStyle({
        outline: `2px solid ${theme.palette.primary.main}`,
        outlineOffset: "2px",
      });
      await tabTo(user, second);
      expect(second).toHaveFocus();
      expect(first).not.toHaveClass("Mui-focusVisible");
      expect(second).toHaveStyle({
        outline: `2px solid ${theme.palette.primary.main}`,
        outlineOffset: "2px",
      });
    },
  );
});

describe.each(roles)(
  "API key keyboard access as $role",
  ({ level, canEdit }) => {
    it.each(["{Enter}", " "])(
      "opens the selected row with %s without changing write permissions",
      async (activation) => {
        state.orgLevel = level;
        const user = userEvent.setup();
        render(<KeyManagementSection />);
        const sync = screen.getByRole("button", { name: "Sync from gateway" });
        const create = screen.getByRole("button", { name: "Create key" });
        expect(sync).toHaveProperty("disabled", !canEdit);
        expect(create).toHaveProperty(
          "disabled",
          !(typeof level === "number" && level >= 1),
        );
        await openSecondKey(user, activation);
        const edit = screen.getByRole("button", { name: "Edit" });
        const revoke = screen.getByRole("button", { name: "Revoke" });
        expect(edit).toHaveProperty("disabled", !canEdit);
        expect(revoke).toHaveProperty("disabled", !canEdit);
        if (canEdit) {
          await tabTo(user, edit);
          await tabTo(user, revoke);
        } else {
          // Read-only roles can navigate the drawer, but cannot tab into writes.
          for (let i = 0; i < 6; i += 1) {
            await user.tab();
            expect(edit).not.toHaveFocus();
            expect(revoke).not.toHaveFocus();
          }
        }
        expect(state.update).not.toHaveBeenCalled();
        expect(state.revoke).not.toHaveBeenCalled();
        expect(state.sync).not.toHaveBeenCalled();
        await user.keyboard("{Escape}");
        await waitFor(() => expect(detailButton("Second")).toHaveFocus());
      },
    );
  },
);

describe.each(roles.filter(({ canEdit }) => canEdit))(
  "API key actions as $role",
  ({ level }) => {
    it("reaches Edit and Revoke confirmations using only the keyboard", async () => {
      state.orgLevel = level;
      const user = userEvent.setup();
      render(<KeyManagementSection />);
      await openSecondKey(user, "{Enter}");
      const edit = screen.getByRole("button", { name: "Edit" });
      await tabTo(user, edit);
      await user.keyboard(" ");
      const dialog = await screen.findByRole("dialog", {
        name: "Edit API key",
      });
      expect(within(dialog).getByRole("textbox", { name: /Name/ })).toHaveValue(
        "Second key",
      );
      await user.keyboard("{Escape}");
      await waitFor(() => expect(edit).toHaveFocus());
      // Focus restores before the dialog's exit transition unhides the drawer.
      const revoke = await screen.findByRole("button", { name: "Revoke" });
      await tabTo(user, revoke);
      await user.keyboard("{Enter}");
      const confirmation = await screen.findByRole("dialog", {
        name: "Revoke Second key?",
      });
      expect(within(confirmation).getByText(/Second key/)).toBeVisible();
      await tabTo(
        user,
        within(confirmation).getByRole("button", { name: "Cancel" }),
      );
      await user.keyboard(" ");
      await waitFor(() => expect(revoke).toHaveFocus());
      await waitFor(() => expect(confirmation).not.toBeInTheDocument());
      expect(revoke).toHaveFocus();
      expect(state.revoke).not.toHaveBeenCalled();
      expect(state.update).not.toHaveBeenCalled();
    });
  },
);

it("keeps inactive key details keyboard-readable without write actions", async () => {
  state.keys[1].status = "revoked";
  const user = userEvent.setup();
  render(<KeyManagementSection />);
  await openSecondKey(user, " ");
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
});

it("preserves pointer activation on the rest of the row", async () => {
  const user = userEvent.setup();
  render(<KeyManagementSection />);
  await user.click(screen.getByRole("cell", { name: "Second owner" }));
  expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
});
