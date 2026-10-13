import { describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import ConnectorPanel from "./ConnectorPanel";
import { safeAuthorizeUrl } from "./connectorSecurity";
import { createOmegaSquadClient } from "src/api/omega-squad/remoteClient";

const agent = {
  id: "agent-1",
  name: "Researcher",
  capabilities: { canEdit: true },
  connectorBindings: [{ id: "existing", name: "Existing docs" }],
};
const workforce = {
  available: true,
  capabilities: { role: "owner", permittedActions: ["view", "update"] },
  agents: [agent],
  connectorBindings: [
    { id: "existing", name: "Existing docs" },
    { id: "org-github", name: "GitHub installation", provider: "GitHub" },
  ],
};
const catalog = {
  available: true,
  connectors: [
    { id: "github", name: "GitHub", provider: "GitHub", auth: "oauth" },
  ],
};
const orgBinding = {
  bindingId: "org-github",
  connectorId: "github",
  target: { kind: "organization", orgId: "org-1" },
  state: "installed",
  grantedScopes: ["repo"],
  refresh: { status: "idle" },
};
const oauthStatus = (bindings = [], overrides = {}) => ({
  available: true,
  schemaVersion: "omega-os-connector-oauth/v1",
  connectorId: "github",
  auth: "oauth",
  configured: true,
  bindings,
  ...overrides,
});
const authorization = (overrides = {}) => ({
  available: true,
  schemaVersion: "omega-os-connector-oauth/v1",
  connectorId: "github",
  authorizeUrl:
    "https://github.com/login/oauth/authorize?state=server-issued&code_challenge=pkce",
  bindingId: "org-github",
  target: { kind: "organization", orgId: "org-1" },
  ...overrides,
});
function client(overrides = {}) {
  return {
    getConnector: vi.fn((id) =>
      Promise.resolve({
        available: true,
        connector: {
          id,
          name: id,
          auth: "oauth",
          provider: "GitHub",
          credentials: { accessToken: "must-not-render" },
        },
      }),
    ),
    listConnectorSources: vi
      .fn()
      .mockResolvedValue({ available: true, sources: [{ id: "issues" }] }),
    listConnectorSyncs: vi
      .fn()
      .mockResolvedValue({
        available: true,
        syncs: [{ id: "sync-1", status: "running" }],
      }),
    listConnectorSyncSchedules: vi
      .fn()
      .mockResolvedValue({
        available: true,
        schedules: [{ id: "schedule-1" }],
      }),
    getConnectorOAuth: vi.fn().mockResolvedValue(oauthStatus()),
    startConnectorOAuth: vi.fn().mockResolvedValue(authorization()),
    listWorkforce: vi.fn().mockResolvedValue(workforce),
    getWorkforceAgent: vi.fn().mockResolvedValue({ available: true, agent }),
    updateWorkforceAgent: vi
      .fn()
      .mockResolvedValue({
        available: true,
        agent: { ...agent, connectorBindings: workforce.connectorBindings },
      }),
    ...overrides,
  };
}
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
async function choose(field, option) {
  await userEvent.click(screen.getByRole("combobox", { name: field }));
  await userEvent.click(screen.getByRole("option", { name: option }));
}
async function chooseBinding() {
  await choose("Agent for workspace connection", "Researcher");
  await choose("Workspace connection", /GitHub installation/);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Attach connection" }),
    ).toBeEnabled(),
  );
}

describe("Omega Squad connector authorization", () => {
  it("reads metadata without rendering tokens or operator actions", async () => {
    const api = client();
    render(<ConnectorPanel result={catalog} client={api} />);
    expect(await screen.findByText("Connector metadata")).toBeInTheDocument();
    expect(
      screen.getByText(/credential values remain server-owned/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("1 sources · 1 sync records · 1 schedules"),
    ).toBeInTheDocument();
    expect(screen.queryByText("must-not-render")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /token|secret|key/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Test|^Build|^Connect$/ }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(api.getConnectorOAuth).toHaveBeenCalledWith("github"),
    );
  });

  it("uses the real EE request/response through the remote client and refreshes after the provider callback", async () => {
    let installed = false;
    const fetchImpl = vi.fn(async (url) => {
      const path = new URL(url).pathname;
      let body;
      if (path.endsWith("/oauth/start")) body = authorization();
      else if (path.endsWith("/oauth"))
        body = oauthStatus(installed ? [orgBinding] : []);
      else if (path === "/workspace/workforce") body = workforce;
      else if (path === "/os/connectors/github")
        body = { connector: { id: "github", auth: "oauth" } };
      else if (path.endsWith("/sources")) body = { sources: [] };
      else if (path.endsWith("/syncs")) body = { syncs: [] };
      else body = { schedules: [] };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const remote = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
      },
      { fetchImpl },
    );
    render(<ConnectorPanel result={catalog} client={remote} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Connect with OAuth" }),
    );
    expect(
      await screen.findByRole("link", { name: "Continue authorization" }),
    ).toHaveAttribute("href", authorization().authorizeUrl);
    const start = fetchImpl.mock.calls.find(([url]) =>
      url.endsWith("/oauth/start"),
    );
    expect(JSON.parse(start[1].body)).toEqual({
      target: { kind: "organization" },
    });
    expect(
      screen.getByText("No server-confirmed installation yet."),
    ).toBeInTheDocument();
    installed = true;
    await userEvent.click(
      screen.getByRole("button", { name: "Refresh authorization" }),
    );
    expect(
      await screen.findByText("1 server-confirmed installation"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/org-github · installed · refresh: idle/),
    ).toBeInTheDocument();
    expect(
      fetchImpl.mock.calls.filter(([url]) => url.endsWith("/oauth")).length,
    ).toBe(2);
  });

  it("authorizes the selected owned agent and retries an existing binding by its server id", async () => {
    const api = client({
      getConnectorOAuth: vi.fn().mockResolvedValue(oauthStatus([orgBinding])),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    await screen.findByRole("button", { name: "Connect with OAuth" });
    await choose("Authorization target", "An existing agent");
    expect(
      screen.getByRole("button", { name: "Connect with OAuth" }),
    ).toBeDisabled();
    await choose("Agent to authorize", "Researcher");
    api.startConnectorOAuth.mockResolvedValueOnce(
      authorization({
        target: { kind: "agent", agentId: "agent-1" },
        bindingId: "agent-binding",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Connect with OAuth" }),
    );
    expect(api.startConnectorOAuth).toHaveBeenLastCalledWith(
      "github",
      { agentId: "agent-1" },
      expect.any(String),
    );
    expect(
      await screen.findByRole("link", { name: "Continue authorization" }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Authorize again" }),
    );
    expect(api.startConnectorOAuth).toHaveBeenLastCalledWith(
      "github",
      { bindingId: "org-github" },
      expect.any(String),
    );
  });

  it("uses the server target list and defaults a delegated workspace to agent authorization", async () => {
    const api = client({
      getConnectorOAuth: vi
        .fn()
        .mockResolvedValue(
          oauthStatus([], { authorizationTargets: ["agent"] }),
        ),
      startConnectorOAuth: vi
        .fn()
        .mockResolvedValue(
          authorization({
            target: { kind: "agent", agentId: "agent-1" },
            bindingId: "agent-binding",
          }),
        ),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    const target = await screen.findByRole("combobox", {
      name: "Authorization target",
    });
    expect(target).toHaveTextContent("An existing agent");
    expect(
      screen.queryByText("Organization installation"),
    ).not.toBeInTheDocument();
    await choose("Agent to authorize", "Researcher");
    await userEvent.click(
      screen.getByRole("button", { name: "Connect with OAuth" }),
    );
    expect(api.startConnectorOAuth).toHaveBeenLastCalledWith(
      "github",
      { agentId: "agent-1" },
      expect.any(String),
    );
  });

  it("switches away from a stale organization target when refresh narrows server targets", async () => {
    const api = client({
      getConnectorOAuth: vi
        .fn()
        .mockResolvedValueOnce(
          oauthStatus([], { authorizationTargets: ["organization", "agent"] }),
        )
        .mockResolvedValueOnce(
          oauthStatus([], { authorizationTargets: ["agent"] }),
        ),
      startConnectorOAuth: vi
        .fn()
        .mockResolvedValue(
          authorization({
            target: { kind: "agent", agentId: "agent-1" },
            bindingId: "agent-binding",
          }),
        ),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    await screen.findByRole("combobox", { name: "Authorization target" });
    await userEvent.click(
      screen.getByRole("button", { name: "Refresh authorization" }),
    );
    const target = await screen.findByRole("combobox", {
      name: "Authorization target",
    });
    expect(target).toHaveTextContent("An existing agent");
    expect(
      screen.queryByText("Organization installation"),
    ).not.toBeInTheDocument();
    await choose("Agent to authorize", "Researcher");
    await userEvent.click(
      screen.getByRole("button", { name: "Connect with OAuth" }),
    );
    expect(api.startConnectorOAuth).toHaveBeenLastCalledWith(
      "github",
      { agentId: "agent-1" },
      expect.any(String),
    );
  });

  it("does not reauthorize a binding whose target disappeared from the authoritative target list", async () => {
    const api = client({
      getConnectorOAuth: vi
        .fn()
        .mockResolvedValue(
          oauthStatus([orgBinding], { authorizationTargets: ["agent"] }),
        ),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    const button = await screen.findByRole("button", {
      name: "Authorize again",
    });
    expect(button).toBeDisabled();
    expect(api.startConnectorOAuth).not.toHaveBeenCalled();
  });

  it.each([
    authorization({
      authorizeUrl: undefined,
      authorizationUrl: "https://github.com/oauth",
    }),
    authorization({ authorizeUrl: "java" + "script:alert(1)" }),
    authorization({ authorizeUrl: "https://127.0.0.1/oauth" }),
    authorization({ authorizeUrl: "https://service.local/oauth" }),
    authorization({ authorizeUrl: "https://user:secret@github.com/oauth" }),
    authorization({ connectorId: "wrong" }),
    authorization({ target: { kind: "agent", agentId: "other" } }),
  ])(
    "refuses an unsafe or mismatched authorization response %#",
    async (response) => {
      const api = client({
        startConnectorOAuth: vi.fn().mockResolvedValue(response),
      });
      render(<ConnectorPanel result={catalog} client={api} />);
      await userEvent.click(
        await screen.findByRole("button", { name: "Connect with OAuth" }),
      );
      expect(
        await screen.findByText(
          /did not return a safe HTTPS authorization URL/,
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: "Continue authorization" }),
      ).not.toBeInTheDocument();
    },
  );

  it("does not publish a delayed authorization link after the target changes", async () => {
    const pending = deferred();
    const api = client({
      startConnectorOAuth: vi.fn().mockReturnValueOnce(pending.promise),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Connect with OAuth" }),
    );
    await choose("Authorization target", "An existing agent");
    await act(async () => pending.resolve(authorization()));
    expect(
      screen.queryByRole("link", { name: "Continue authorization" }),
    ).not.toBeInTheDocument();
  });

  it("ignores an old connector's refresh after selection changes", async () => {
    const pending = deferred();
    const api = client({
      getConnectorOAuth: vi
        .fn()
        .mockResolvedValueOnce(oauthStatus())
        .mockReturnValueOnce(pending.promise)
        .mockResolvedValueOnce({ ...oauthStatus(), connectorId: "slack" }),
    });
    render(
      <ConnectorPanel
        result={{
          ...catalog,
          connectors: [
            ...catalog.connectors,
            { id: "slack", name: "Slack", auth: "oauth" },
          ],
        }}
        client={api}
      />,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Refresh authorization" }),
    );
    await userEvent.click(screen.getByText("Slack"));
    await waitFor(() =>
      expect(api.getConnectorOAuth).toHaveBeenCalledWith("slack"),
    );
    await act(async () => pending.resolve(oauthStatus([orgBinding])));
    expect(
      screen.queryByText(/org-github · installed/),
    ).not.toBeInTheDocument();
  });

  it("makes configuration and request failures explicit", async () => {
    const api = client({
      getConnectorOAuth: vi
        .fn()
        .mockResolvedValue({ ...oauthStatus(), configured: false }),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    expect(
      await screen.findByText(/OAuth is not configured/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Connect with OAuth" }),
    ).not.toBeInTheDocument();
    api.getConnectorOAuth.mockRejectedValueOnce(
      new Error("do-not-leak-internal-secret"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Refresh authorization" }),
    );
    expect(
      await screen.findByText("The remote request failed. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/do-not-leak/)).not.toBeInTheDocument();
  });

  it("keeps a rejected OAuth request explicit without claiming success", async () => {
    const api = client({
      startConnectorOAuth: vi
        .fn()
        .mockResolvedValue({
          available: false,
          reason: "This installation was removed.",
        }),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Connect with OAuth" }),
    );
    expect(
      await screen.findByText("This installation was removed."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Continue authorization" }),
    ).not.toBeInTheDocument();
  });
});

describe("Omega Squad workspace connector binding", () => {
  it("uses a server-known id and preserves freshly read agent bindings", async () => {
    const api = client();
    render(<ConnectorPanel result={catalog} client={api} />);
    await screen.findByRole("button", { name: "Connect with OAuth" });
    await chooseBinding();
    const concurrentBinding = {
      id: "concurrent",
      name: "Another installation",
    };
    api.getWorkforceAgent.mockResolvedValueOnce({
      available: true,
      agent: {
        ...agent,
        connectorBindings: [...agent.connectorBindings, concurrentBinding],
      },
    });
    api.updateWorkforceAgent.mockResolvedValueOnce({
      available: true,
      agent: {
        ...agent,
        connectorBindings: [
          ...agent.connectorBindings,
          concurrentBinding,
          workforce.connectorBindings[1],
        ],
      },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Attach connection" }),
    );
    await waitFor(() =>
      expect(api.updateWorkforceAgent).toHaveBeenCalledWith(
        "agent-1",
        { connectorBindingIds: ["existing", "concurrent", "org-github"] },
        expect.any(String),
      ),
    );
    expect(
      await screen.findByText(/Connection attached to the agent/),
    ).toBeInTheDocument();
  });

  it("refuses a stale binding or revoked authority before updating", async () => {
    const api = client();
    render(<ConnectorPanel result={catalog} client={api} />);
    await screen.findByRole("button", { name: "Connect with OAuth" });
    await chooseBinding();
    api.listWorkforce.mockResolvedValueOnce({
      ...workforce,
      connectorBindings: [],
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Attach connection" }),
    );
    expect(
      await screen.findByText(
        /Agent permissions or available installations changed/,
      ),
    ).toBeInTheDocument();
    expect(api.updateWorkforceAgent).not.toHaveBeenCalled();
  });

  it("does not write a binding after the user changes selection during revalidation", async () => {
    const pending = deferred();
    const api = client();
    render(<ConnectorPanel result={catalog} client={api} />);
    await screen.findByRole("button", { name: "Connect with OAuth" });
    await chooseBinding();
    api.listWorkforce.mockReturnValueOnce(pending.promise);
    await userEvent.click(
      screen.getByRole("button", { name: "Attach connection" }),
    );
    await choose("Workspace connection", /Existing docs/);
    await act(async () => pending.resolve(workforce));
    expect(api.updateWorkforceAgent).not.toHaveBeenCalled();
  });

  it("does not offer mutation actions to a member", async () => {
    const api = client({
      listWorkforce: vi
        .fn()
        .mockResolvedValue({
          ...workforce,
          capabilities: { role: "member", permittedActions: ["view", "run"] },
          agents: [{ ...agent, capabilities: { canEdit: false } }],
        }),
    });
    render(<ConnectorPanel result={catalog} client={api} />);
    expect(
      await screen.findByText(
        "An owner or administrator must authorize this connector.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Attach connection" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Connect with OAuth" }),
    ).not.toBeInTheDocument();
  });
});

describe("OAuth URL boundary", () => {
  it.each([
    "http://github.com/oauth",
    "https://localhost/oauth",
    "https://2130706433/oauth",
    "https://[::1]/oauth",
    "https://metadata.google.internal/oauth",
    "https://accounts/oauth",
    "https://accounts.example.com:8443/oauth",
    "https://user@accounts.example.com/oauth",
  ])("refuses %s", (url) => expect(safeAuthorizeUrl(url)).toBeNull());
});
