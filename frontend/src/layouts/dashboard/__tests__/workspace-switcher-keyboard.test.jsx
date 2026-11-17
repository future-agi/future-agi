import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import WorkspaceSwitcher from "../WorkspaceSwitcher/WorkspaceSwitcher";

const h = vi.hoisted(() => ({ switchOrganization: vi.fn(), switchWorkspace: vi.fn(), updateTheme: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "a", currentOrganizationDisplayName: "Acme", currentOrganizationRole: "Member", orgLevel: 1, switchOrganization: h.switchOrganization }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "w1", currentWorkspaceDisplayName: "First workspace", currentWorkspaceRole: "Member", switchWorkspace: h.switchWorkspace, updateWorkspaceName: vi.fn() }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [{ id: "w1", name: "First workspace" }, { id: "w2", name: "Second workspace" }], fetchNextPage: vi.fn(), isPending: false }),
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { data: { organizations: [{ id: "a", name: "Acme" }, { id: "b", name: "Other organization" }] } }, isPending: false }),
  useMutation: ({ mutationFn }) => ({ mutate: mutationFn, isPending: false }),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: { name: "Member", ws_enabled: true }, logout: vi.fn() }) }));
vi.mock("src/auth/sign-out", () => ({ signOutToSignIn: vi.fn() }));
vi.mock("src/components/settings", () => ({ useSettingsContext: () => ({ themeMode: "light", onUpdate: h.updateTheme }) }));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: { organizations: { list: "/organizations" } } }));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, PropertyName: {}, trackEvent: vi.fn() }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => true,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/pages/dashboard/settings/UserManagementV2/AllActionForm", () => ({ default: () => null }));
vi.mock("../WorkspaceSwitcher/CreateOrganizationModal", () => ({ default: () => null }));
vi.mock("src/hooks/use-scroll-end", () => ({ useScrollEnd: () => ({ current: null }) }));

function Location() { return <output data-testid="path">{useLocation().pathname}</output>; }
function mount() {
  render(<MemoryRouter initialEntries={["/dashboard/gateway/models"]}><WorkspaceSwitcher /><Location /></MemoryRouter>);
}
async function open() {
  const trigger = screen.getByRole("button", { name: "Switch workspace" });
  trigger.focus();
  await userEvent.keyboard("{Enter}");
  return trigger;
}
async function tabTo(name) {
  const target = screen.getByRole("button", { name, exact: true });
  for (let i = 0; i < 16 && document.activeElement !== target; i += 1) await userEvent.tab();
  expect(target).toHaveFocus();
  return target;
}
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("R3-SHELL-DX-04 keyboard shell menus", () => {
  it("opens the organization submenu, chooses another organization and restores focus without a mouse", async () => {
    mount();
    await open();
    const organization = await tabTo("Acme");
    await userEvent.keyboard("{Enter}");
    const choices = await screen.findByRole("group", { name: "Organizations" });
    await waitFor(() => expect(within(choices).getByRole("button", { name: "Acme" })).toHaveFocus());
    await userEvent.tab();
    await userEvent.keyboard(" ");
    expect(h.switchOrganization).toHaveBeenCalledWith("b");
    await waitFor(() => expect(organization).toHaveFocus());
  });

  it("uses Escape and ArrowLeft to go back, then restores the outer trigger", async () => {
    mount();
    const trigger = await open();
    const theme = await tabTo("Theme");
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("group", { name: "Appearance" });
    await userEvent.keyboard("{ArrowLeft}");
    expect(theme).toHaveFocus();
    await userEvent.keyboard(" ");
    await screen.findByRole("group", { name: "Appearance" });
    await userEvent.keyboard("{Escape}");
    expect(theme).toHaveFocus();
    expect(screen.queryByRole("group", { name: "Appearance" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("changes workspace and appearance with native keyboard activation", async () => {
    mount();
    await open();
    await tabTo("First workspace");
    await userEvent.keyboard("{Enter}");
    const choices = await screen.findByRole("group", { name: "Workspaces" });
    await waitFor(() => expect(within(choices).getByRole("button", { name: "First workspace" })).toHaveFocus());
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(h.switchWorkspace).toHaveBeenCalledWith("w2", "w1");
    await tabTo("Theme");
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("group", { name: "Appearance" });
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(h.updateTheme).toHaveBeenCalledWith("themeMode", "dark");
  });

  it("reaches organization settings and logout using Tab", async () => {
    mount();
    await open();
    await tabTo("Organization settings");
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("path")).toHaveTextContent("/dashboard/settings/workspace");
    await open();
    await tabTo("Log out");
    await userEvent.keyboard(" ");
    expect(await screen.findByRole("dialog", { name: /Are you sure you want to logout/ })).toBeVisible();
    expect(h.switchOrganization).not.toHaveBeenCalled();
  });
});
