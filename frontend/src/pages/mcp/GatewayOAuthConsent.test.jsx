import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import GatewayOAuthConsent from "./GatewayOAuthConsent";
import axios from "src/utils/axios";
import { returnToClient } from "src/sections/gateway/mcp/clients/consentApi";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", email: "member@example.test" },
  }),
}));
vi.mock("src/sections/gateway/mcp/clients/consentApi", async (original) => ({
  ...(await original()),
  returnToClient: vi.fn(),
}));
const REQUEST = "pending-request-for-synthetic-client";
const PATH = `/agentcc/mcp-oauth/consent/${REQUEST}/`;
const ORG = "00000000-0000-4000-8000-000000000001";
const detail = () => ({
  client: {
    client_id: "synthetic-client",
    name: "Synthetic desktop",
    verified: false,
    first_party: false,
    kind: "dcr",
  },
  redirect_uri: "http://127.0.0.1:1234/callback",
  redirect_host: "127.0.0.1",
  resource: "https://gateway.example.test/mcp",
  virtual_server: null,
  organizations: [{ id: ORG, name: "Organization A" }],
  scopes: [{ name: "mcp:tools", description: "List and call tools" }],
  pre_approved: false,
});
function mount(query = `request=${REQUEST}`) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[`/gateway/mcp/authorize?${query}`]}>
        <GatewayOAuthConsent />
      </MemoryRouter>
    </HelmetProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockResolvedValue({ data: { status: true, result: detail() } });
  axios.post.mockResolvedValue({
    data: {
      status: true,
      result: {
        redirect_to: "http://127.0.0.1:1234/callback?code=opaque&state=kept",
      },
    },
  });
});

describe("Gateway MCP consent", () => {
  it("loads the opaque request, shows the authoritative identity and scopes, and approves only self", async () => {
    mount(
      `request=${REQUEST}&client_id=spoof&scope=spoof&redirect_uri=https://spoof.invalid&state=spoof&code_challenge=spoof`,
    );
    expect(await screen.findByText("Synthetic desktop")).toBeInTheDocument();
    expect(screen.queryByText("spoof")).not.toBeInTheDocument();
    expect(screen.getByText("List and call tools")).toBeInTheDocument();
    expect(screen.getByText(/gateway.example.test\/mcp/)).toBeInTheDocument();
    expect(screen.getByText(/member@example.test/)).toBeInTheDocument();
    expect(screen.getByText(/not verified/)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(
      PATH,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await waitFor(() =>
      expect(returnToClient).toHaveBeenCalledWith(
        "http://127.0.0.1:1234/callback?code=opaque&state=kept",
        detail().redirect_uri,
      ),
    );
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(PATH, {
      decision: "approve",
      organization_id: ORG,
    });
  });

  it("denies without adding a subject, scope, callback or organization", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(PATH, { decision: "deny" }),
    );
  });

  it("requires an explicit organization when more than one is offered", async () => {
    axios.get.mockResolvedValue({
      data: {
        status: true,
        result: {
          ...detail(),
          organizations: [
            { id: ORG, name: "Organization A" },
            {
              id: "00000000-0000-4000-8000-000000000002",
              name: "Organization B",
            },
          ],
        },
      },
    });
    mount();
    expect(
      await screen.findByRole("button", { name: "Approve" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Organization" }));
    fireEvent.click(screen.getByRole("option", { name: "Organization B" }));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(PATH, {
        decision: "approve",
        organization_id: "00000000-0000-4000-8000-000000000002",
      }),
    );
  });

  it.each([
    "",
    "request=short",
    `request=${REQUEST}&request=${REQUEST}`,
    "client_id=legacy&redirect_uri=http://localhost",
  ])("refuses an incomplete or ambiguous link (%s)", (query) => {
    mount(query);
    expect(screen.getByRole("alert")).toHaveTextContent("incomplete");
    expect(axios.get).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
  });

  it("shows an expired request without actions or redirecting", async () => {
    axios.get.mockRejectedValue({ response: { status: 404 } });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("expired");
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(returnToClient).not.toHaveBeenCalled();
  });

  it("surfaces a denied decision and never redirects on failure", async () => {
    axios.post.mockRejectedValue({
      response: {
        status: 403,
        data: { result: "Membership is no longer active." },
      },
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await screen.findByText("Membership is no longer active.");
    expect(returnToClient).not.toHaveBeenCalled();
  });

  it("does not redirect after unmount while a decision is pending", async () => {
    let finish;
    axios.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const view = mount();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    view.unmount();
    finish({
      data: {
        status: true,
        result: { redirect_to: "http://127.0.0.1:1234/callback?code=opaque" },
      },
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(returnToClient).not.toHaveBeenCalled();
  });
});
