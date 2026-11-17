import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GatewaySnippetKey from "./GatewaySnippetKey";

const state = vi.hoisted(() => ({
  organization: "org-a",
  result: { secret: "synthetic-new-key", allowedModels: [], allowedProviders: [] },
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { id: "user-a" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: state.organization }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-a" }),
}));
vi.mock("../keys/CreateKeyDialog", () => ({
  default: ({ onCreated, onClose }) => (
    <button onClick={() => { onCreated(state.result); onClose(); }}>Finish creation</button>
  ),
}));

async function createKey(user) {
  await user.click(screen.getByRole("button", { name: "Create a key" }));
  await user.click(screen.getByRole("button", { name: "Finish creation" }));
}

describe("Overview inline key", () => {
  beforeEach(() => {
    state.result = { secret: "synthetic-new-key", allowedModels: [], allowedProviders: [] };
    state.organization = "org-a";
  });

  it.each([
    { allowedModels: ["gpt-4.1"] },
    { allowedProviders: ["groq"] },
    { requestedModels: ["groq/chat"] },
    { requestedProviders: ["groq"] },
    { allowedModels: "malformed" },
  ])("R3-KEYS-ONBOARDING-03 withholds a known-denied request for %j", async (restrictions) => {
    const user = userEvent.setup();
    state.result = { ...state.result, ...restrictions };
    render(<GatewaySnippetKey gatewayId="default" route={{ id: "openai/gpt-4.1", provider: "openai" }}>
      {(secret) => <pre data-testid="request">{secret}</pre>}
    </GatewaySnippetKey>);
    await createKey(user);
    expect(screen.queryByTestId("request")).toBeNull();
    expect(screen.getByText(/does not match this key/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy key" })).toBeEnabled();
  });

  it("R3-KEYS-ONBOARDING-03 rechecks the selected route and still permits model discovery", async () => {
    const user = userEvent.setup();
    state.result = { ...state.result, allowedModels: ["openai/gpt-4.1"], allowedProviders: ["openai"] };
    const view = (route) => <GatewaySnippetKey gatewayId="default" route={route}>
      {(secret) => <pre data-testid="request">{secret}</pre>}
    </GatewaySnippetKey>;
    const { rerender } = render(view({ id: "openai/gpt-4.1", provider: "openai" }));
    await createKey(user);
    expect(screen.getByTestId("request")).toHaveTextContent("synthetic-new-key");
    rerender(view({ id: "groq/chat", provider: "groq" }));
    expect(screen.queryByTestId("request")).toBeNull();
    rerender(view(null));
    expect(screen.getByTestId("request")).toHaveTextContent("synthetic-new-key");
  });
  it("R3-KEYS-ONBOARDING-01 reveals and copies a key even with no request recipe", async () => {
    const user = userEvent.setup();
    render(<GatewaySnippetKey gatewayId="default">{() => null}</GatewaySnippetKey>);
    await createKey(user);
    expect(screen.getByText("synthetic-new-key")).toBeInTheDocument();
    expect(screen.getByText(/only time.*secret/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy key" }));
    expect(await navigator.clipboard.readText()).toBe("synthetic-new-key");
    await user.click(screen.getByRole("button", { name: "Forget key" }));
    expect(screen.queryByText("synthetic-new-key")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy key" })).toBeNull();
  });

  it("R3-KEYS-ONBOARDING-01 forgets the secret when organization scope changes", async () => {
    const user = userEvent.setup();
    const view = <GatewaySnippetKey gatewayId="default">{() => null}</GatewaySnippetKey>;
    const { rerender } = render(view);
    await createKey(user);
    state.organization = "org-b";
    rerender(<GatewaySnippetKey gatewayId="default">{() => null}</GatewaySnippetKey>);
    expect(screen.queryByText("synthetic-new-key")).toBeNull();
    state.organization = "org-a";
  });
});
