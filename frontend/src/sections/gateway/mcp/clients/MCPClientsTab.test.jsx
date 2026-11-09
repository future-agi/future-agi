import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import MCPClientsTab from "./MCPClientsTab";
import axios from "src/utils/axios";

const context = vi.hoisted(() => ({ org: "org-a", level: 3 }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), delete: vi.fn() },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "member-a" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: context.org,
    orgLevel: context.level,
  }),
}));
const row = (extra = {}) => ({
  id: 1,
  client_name: "Synthetic desktop",
  client_id: "synthetic-client",
  client_kind: "dcr",
  user: { id: "member-a", name: "Member A" },
  organization: { id: "org-a", name: "Organization A" },
  resource: "https://gateway.example.test/mcp",
  scopes: ["mcp:tools"],
  granted_at: "2026-10-28T00:00:00Z",
  can_revoke: true,
  ...extra,
});
function mount() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <MCPClientsTab />
    </QueryClientProvider>
  );
  return { ...render(tree()), tree };
}
beforeEach(() => {
  vi.clearAllMocks();
  context.org = "org-a";
  context.level = 3;
  axios.get.mockResolvedValue({ data: { status: true, result: [row()] } });
  axios.delete.mockResolvedValue({
    data: { status: true, result: { revoked: true } },
  });
});

describe("MCP client grants", () => {
  it("shows own grants to members and revokes only after confirmation", async () => {
    mount();
    expect(await screen.findByText("Synthetic desktop")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Organization clients" }),
    ).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(
      "/agentcc/mcp-oauth/consents/",
      expect.objectContaining({
        params: { scope: "self" },
        headers: { "X-Organization-Id": "org-a" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/15 minutes/)).toBeInTheDocument();
    expect(axios.delete).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    // MUI keeps the page aria-hidden until the dialog's exit transition ends.
    const revoke = await screen.findByRole("button", { name: "Revoke" });
    expect(dialog).not.toBeInTheDocument();
    expect(axios.delete).not.toHaveBeenCalled();
    fireEvent.click(revoke);
    axios.get.mockResolvedValue({ data: { status: true, result: [] } });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Revoke access",
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { hidden: true }),
      ).not.toBeInTheDocument(),
    );
    await screen.findByText(/You have no active MCP client connections/);
    expect(axios.delete).toHaveBeenCalledWith(
      "/agentcc/mcp-oauth/consents/1/",
      { headers: { "X-Organization-Id": "org-a" } },
    );
    expect(
      screen.getByText(/Access revoked. Refresh is blocked/),
    ).toBeInTheDocument();
  });

  it("lets admins inspect organization clients without revoking peers", async () => {
    context.level = 8;
    mount();
    await screen.findByText("Synthetic desktop");
    axios.get.mockResolvedValue({
      data: {
        status: true,
        result: [
          row({ can_revoke: false, user: { id: "peer", name: "Peer" } }),
        ],
      },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Organization clients" }),
    );
    await screen.findByText(/Approved by Peer/);
    expect(axios.get).toHaveBeenLastCalledWith(
      "/agentcc/mcp-oauth/consents/",
      expect.objectContaining({ params: { scope: "organization" } }),
    );
    expect(
      screen.queryByRole("button", { name: "Revoke" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the confirmation open on refusal, without a success message", async () => {
    axios.delete.mockRejectedValue({
      response: { status: 403, data: { result: "Membership required." } },
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Revoke access",
      }),
    );
    await screen.findByText("Membership required.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText(/Access revoked/)).not.toBeInTheDocument();
  });

  it("does not treat a failed list read as an empty list", async () => {
    axios.get.mockRejectedValue(new Error("offline"));
    mount();
    await screen.findByRole("button", { name: "Retry" });
    expect(screen.queryByText(/You have no active/)).not.toBeInTheDocument();
    axios.get.mockResolvedValue({ data: { status: true, result: [] } });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/You have no active/);
  });

  it("clears the old selection and cached rows when the organization changes", async () => {
    const view = mount();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    context.org = "org-b";
    axios.get.mockResolvedValue({ data: { status: true, result: [] } });
    view.rerender(view.tree());
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await screen.findByText(/You have no active/);
    expect(screen.queryByText("Synthetic desktop")).not.toBeInTheDocument();
    expect(axios.delete).not.toHaveBeenCalled();
    expect(axios.get).toHaveBeenLastCalledWith(
      "/agentcc/mcp-oauth/consents/",
      expect.objectContaining({ headers: { "X-Organization-Id": "org-b" } }),
    );
  });
});
