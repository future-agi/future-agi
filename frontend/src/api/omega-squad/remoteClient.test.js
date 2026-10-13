import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createOmegaSquadClient,
  normalizeRemoteBaseUrl,
  parseFleetHealth,
  readOmegaSquadConfig,
} from "./remoteClient";
import {
  hashCanonicalJson,
  OMEGA_PROOF_PACK_PROTOCOL,
  OMEGA_PROOF_PACK_RUN_EVIDENCE_PROTOCOL,
  OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL,
} from "./proofPack";

describe("Omega Squad remote client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.__OMEGA_SQUAD_CONFIG__ = {};
    window.__OMEGA_SQUAD_SESSION__ = undefined;
    if (typeof sessionStorage !== "undefined") sessionStorage.clear();
    if (typeof localStorage !== "undefined") localStorage.clear();
  });

  it("requires a configured non-loopback URL", async () => {
    expect(normalizeRemoteBaseUrl("http://localhost:8317")).toBe("");
    expect(normalizeRemoteBaseUrl("http://omega.example.com")).toBe("");
    expect(normalizeRemoteBaseUrl("https://omega.example.com/")).toBe(
      "https://omega.example.com",
    );

    const config = readOmegaSquadConfig();
    expect(config.baseUrl).toBe("");
    await expect(
      createOmegaSquadClient(config).listWorkforce(),
    ).resolves.toMatchObject({
      available: false,
      code: "NOT_CONFIGURED",
    });
  });

  it("reads the customer model catalog with the selected trusted scope", async () => {
    const response = {
      schemaVersion: "omega-workforce/v1",
      defaultModelId: "model-a",
      models: [{ modelId: "model-a", selectable: true }],
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(response), {
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
        organizationId: "org-a",
        workspaceId: "ws-a",
      },
      { fetchImpl },
    );
    await expect(client.listWorkforceModels()).resolves.toMatchObject({
      available: true,
      ...response,
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://omega.example.com/workspace/workforce/models",
      expect.objectContaining({
        credentials: "omit",
        headers: expect.objectContaining({
          "X-Organization-Id": "org-a",
          "X-Workspace-Id": "ws-a",
          Authorization: "Bearer session",
        }),
      }),
    );
  });

  it("uses scoped skill routes with immutable revision and mutation fences", async () => {
    const fetchImpl = vi
      .fn()
      .mockImplementation(
        async () =>
          new Response(
            JSON.stringify({ schemaVersion: "omega-workspace-skills/v1" }),
            { headers: { "content-type": "application/json" } },
          ),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
        organizationId: "org-a",
        workspaceId: "ws-a",
      },
      { fetchImpl },
    );
    await client.listWorkspaceSkills();
    await client.getWorkspaceSkill("skill-a");
    await client.listWorkspaceSkillRevisions("skill-a");
    await client.getWorkspaceSkillRevision("skill-a", 2);
    const input = {
      name: "Research",
      description: "Evidence",
      instructions: "Cite sources",
    };
    await client.createWorkspaceSkill(input, "create-once");
    await client.updateWorkspaceSkill("skill-a", {
      ...input,
      expectedRevision: 2,
    });
    await client.archiveWorkspaceSkill("skill-a", { expectedRevision: 3 });
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/workspace/skills",
      "https://omega.example.com/workspace/skills/skill-a",
      "https://omega.example.com/workspace/skills/skill-a/revisions",
      "https://omega.example.com/workspace/skills/skill-a/revisions/2",
      "https://omega.example.com/workspace/skills",
      "https://omega.example.com/workspace/skills/skill-a",
      "https://omega.example.com/workspace/skills/skill-a/archive",
    ]);
    expect(fetchImpl.mock.calls[4][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify(input),
      headers: {
        "Idempotency-Key": "create-once",
        "X-Organization-Id": "org-a",
        "X-Workspace-Id": "ws-a",
      },
    });
    expect(fetchImpl.mock.calls[5][1]).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ ...input, expectedRevision: 2 }),
    });
    expect(fetchImpl.mock.calls[6][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ expectedRevision: 3 }),
    });
    await expect(client.createWorkspaceSkill(input, "")).resolves.toMatchObject(
      { available: false, code: "INVALID_INPUT" },
    );
    expect(fetchImpl).toHaveBeenCalledTimes(7);
  });

  it("reads sharing candidates through the scoped read-only customer endpoint", async () => {
    const response = {
      schemaVersion: "omega-workforce/v1",
      truncated: false,
      candidates: [{ accountId: "member-a", displayName: "Alex" }],
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(response), {
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
        organizationId: "org-a",
        workspaceId: "ws-a",
      },
      { fetchImpl },
    );
    await expect(
      client.listWorkforceSharingCandidates(),
    ).resolves.toMatchObject({ available: true, ...response });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://omega.example.com/workspace/workforce/sharing-candidates",
      expect.objectContaining({
        credentials: "omit",
        headers: expect.objectContaining({
          "X-Organization-Id": "org-a",
          "X-Workspace-Id": "ws-a",
        }),
      }),
    );
  });

  it("generates a review-only agent proposal through the scoped remote endpoint without retries", async () => {
    const input = {
      message: "Prepare a weekly evidence report",
      currentProposal: {
        name: "Analyst",
        role: "Researcher",
        purpose: "Review facts",
        responsibilities: ["Cite sources"],
      },
    };
    const response = {
      schemaVersion: "omega-workforce-agent-draft/v1",
      reviewRequired: true,
      proposal: {
        name: "Analyst",
        role: "Researcher",
        purpose: "Review facts",
        responsibilities: ["Cite sources"],
        workMode: "on_demand",
        connectorBindingIds: [],
      },
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(response), {
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
        organizationId: "org-a",
        workspaceId: "ws-a",
      },
      { fetchImpl },
    );
    await expect(
      client.draftWorkforceAgent(input, "draft-1"),
    ).resolves.toMatchObject({ available: true, ...response });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenLastCalledWith(
      "https://omega.example.com/workspace/workforce/agent-drafts",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(input),
        credentials: "omit",
        headers: expect.objectContaining({
          "Idempotency-Key": "draft-1",
          "X-Organization-Id": "org-a",
          "X-Workspace-Id": "ws-a",
          Authorization: "Bearer session",
        }),
      }),
    );
    fetchImpl.mockRejectedValueOnce(new Error("lost response"));
    await expect(
      client.draftWorkforceAgent(input, "draft-1"),
    ).resolves.toMatchObject({ available: false, code: "NETWORK_ERROR" });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    await expect(client.draftWorkforceAgent(input, "")).resolves.toMatchObject({
      available: false,
      code: "INVALID_INPUT",
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("reads the tenant-scoped agent tool policy projection and preserves typed errors", async () => {
    const response = {
      schemaVersion: "omega-workforce/v1",
      agentId: "agent-1",
      currentVersion: 4,
      deployedVersion: 3,
      toolPolicy: {
        protocol: "omega-workforce-tool-policy/v1",
        defaultMode: "ask",
        tools: {},
      },
      deployedToolPolicy: {
        protocol: "omega-workforce-tool-policy/v1",
        tools: {},
      },
      tools: [
        {
          toolId: "orders.read",
          mode: "ask",
          effectiveMode: "ask",
          configured: false,
          available: true,
        },
      ],
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(response), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
        organizationId: "org-a",
        workspaceId: "ws-a",
      },
      { fetchImpl },
    );
    await expect(
      client.getWorkforceAgentTools("agent/1"),
    ).resolves.toMatchObject({ available: true, ...response });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://omega.example.com/workspace/workforce/agents/agent%2F1/tools",
      expect.objectContaining({
        credentials: "omit",
        headers: expect.objectContaining({
          Authorization: "Bearer session",
          "X-Organization-Id": "org-a",
          "X-Workspace-Id": "ws-a",
        }),
      }),
    );

    fetchImpl.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schemaVersion: "omega-workspace/v1",
          error: {
            code: "tools_unavailable",
            message: "Tool policy catalog is unavailable on this deployment.",
          },
        }),
        { status: 503, headers: { "content-type": "application/json" } },
      ),
    );
    await expect(
      client.getWorkforceAgentTools("agent-1"),
    ).resolves.toMatchObject({
      available: false,
      code: "tools_unavailable",
      status: 503,
    });
    await expect(client.getWorkforceAgentTools("")).resolves.toMatchObject({
      available: false,
      code: "INVALID_INPUT",
    });
  });

  it("preserves typed workspace errors nested under the EE error envelope", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            schemaVersion: "omega-workspace/v1",
            error: {
              code: "draft_model_unavailable",
              message: "A bounded authoring model is not configured.",
            },
          }),
          { status: 503, headers: { "content-type": "application/json" } },
        ),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session",
        authMode: "bearer",
        organizationId: "org-a",
        workspaceId: "ws-a",
      },
      { fetchImpl },
    );
    await expect(
      client.draftWorkforceAgent(
        { message: "Draft an analyst" },
        "draft-typed-error",
      ),
    ).resolves.toMatchObject({
      available: false,
      code: "draft_model_unavailable",
      reason: "A bounded authoring model is not configured.",
      status: 503,
    });
  });

  it("uses injected host auth and workspace scope without persisting credentials", async () => {
    window.__OMEGA_SQUAD_CONFIG__ = {
      VITE_OMEGA_SQUAD_API_URL: "https://omega.example.com/",
      organizationId: "org-1",
      workspaceId: "ws-1",
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ agents: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const config = readOmegaSquadConfig({ getToken: () => "session-token" });
    const result = await createOmegaSquadClient(config, {
      fetchImpl,
    }).listWorkforce();

    expect(result).toMatchObject({ available: true, agents: [] });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://omega.example.com/workspace/workforce",
      expect.objectContaining({
        credentials: "omit",
        headers: expect.objectContaining({
          Authorization: "Bearer session-token",
          "X-Organization-Id": "org-1",
          "X-Workspace-Id": "ws-1",
        }),
      }),
    );
    if (typeof localStorage !== "undefined")
      expect(localStorage.getItem("accessToken")).toBeNull();
  });

  it("does not use mutable host organization storage as an Omega authorization scope", async () => {
    window.__OMEGA_SQUAD_CONFIG__ = {
      VITE_OMEGA_SQUAD_API_URL: "https://omega.example.com/",
    };
    sessionStorage.setItem("organizationId", "spoofed-org");
    sessionStorage.setItem("workspaceId", "spoofed-workspace");
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ agents: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    await createOmegaSquadClient(readOmegaSquadConfig(), {
      fetchImpl,
    }).listWorkforce();

    expect(
      fetchImpl.mock.calls[0][1].headers["X-Organization-Id"],
    ).toBeUndefined();
    expect(
      fetchImpl.mock.calls[0][1].headers["X-Workspace-Id"],
    ).toBeUndefined();
  });

  it("does not forward the FutureAGI JWT to another origin without an explicit adapter", async () => {
    window.__OMEGA_SQUAD_CONFIG__ = {
      VITE_OMEGA_SQUAD_API_URL: "https://omega.example.com/",
    };
    if (typeof window !== "undefined" && window.localStorage)
      window.localStorage.setItem("accessToken", "futureagi-jwt");
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ agents: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    await createOmegaSquadClient(readOmegaSquadConfig(), {
      fetchImpl,
    }).listWorkforce();

    expect(fetchImpl.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("forwards the host JWT only when the runtime explicitly enables the gateway exchange", async () => {
    window.__OMEGA_SQUAD_CONFIG__ = {
      VITE_OMEGA_SQUAD_API_URL: "https://omega.example.com/",
      allowHostToken: "true",
    };
    window.__OMEGA_SQUAD_SESSION__ = { accessToken: "futureagi-jwt" };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ agents: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    await createOmegaSquadClient(
      readOmegaSquadConfig({
        organizationId: "selected-org",
        workspaceId: "selected-workspace",
        scopeReady: true,
      }),
      { fetchImpl },
    ).listWorkforce();

    expect(fetchImpl.mock.calls[0][1].headers).toMatchObject({
      Authorization: "Bearer futureagi-jwt",
      "X-Organization-Id": "selected-org",
      "X-Workspace-Id": "selected-workspace",
    });
  });

  it.each([
    { organizationId: "org", workspaceId: "ws", scopeReady: false },
    { organizationId: null, workspaceId: "ws", scopeReady: true },
    { organizationId: "org", workspaceId: null, scopeReady: true },
    { organizationId: "org", workspaceId: " ", scopeReady: true },
  ])(
    "does not read or send a host credential without a ready selection: %j",
    async (scope) => {
      const getToken = vi.fn(() => "host-secret");
      const fetchImpl = vi.fn();
      window.__OMEGA_SQUAD_CONFIG__ = {
        baseUrl: "https://omega.example.com",
        allowHostToken: true,
        organizationId: "runtime-org",
        workspaceId: "runtime-ws",
      };
      sessionStorage.setItem("organizationId", "stored-org");
      sessionStorage.setItem("workspaceId", "stored-ws");
      const config = readOmegaSquadConfig({ ...scope, getToken });
      await expect(
        createOmegaSquadClient(config, { fetchImpl }).listWorkforce(),
      ).resolves.toMatchObject({
        available: false,
        code: "SCOPE_UNAVAILABLE",
      });
      expect(getToken).not.toHaveBeenCalled();
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );

  it("ignores runtime and stored host selections even when a credential is available", async () => {
    window.__OMEGA_SQUAD_CONFIG__ = {
      baseUrl: "https://omega.example.com",
      allowHostToken: true,
      organizationId: "runtime-org",
      workspaceId: "runtime-ws",
      scopeReady: true,
    };
    window.__OMEGA_SQUAD_SESSION__ = { token: "host-secret" };
    sessionStorage.setItem("organizationId", "stored-org");
    sessionStorage.setItem("workspaceId", "stored-ws");
    const fetchImpl = vi.fn();
    const config = readOmegaSquadConfig();
    expect(config).toMatchObject({
      organizationId: null,
      workspaceId: null,
      scopeReady: false,
    });
    await expect(
      createOmegaSquadClient(config, { fetchImpl }).listWorkforce(),
    ).resolves.toMatchObject({ code: "SCOPE_UNAVAILABLE" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("drops a host request whose scope changed while obtaining its credential", async () => {
    let resolveToken;
    let current = true;
    const fetchImpl = vi.fn();
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        allowHostToken: true,
        scopeReady: true,
        organizationId: "org",
        workspaceId: "ws",
        isScopeCurrent: () => current,
        getToken: () =>
          new Promise((resolve) => {
            resolveToken = resolve;
          }),
      },
      { fetchImpl },
    );
    const result = client.listWorkforce();
    current = false;
    resolveToken("old-host-secret");
    await expect(result).resolves.toMatchObject({
      available: false,
      code: "SCOPE_UNAVAILABLE",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("discards a prior tenant response after the scope changes", async () => {
    let resolveResponse;
    let current = true;
    const fetchImpl = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        allowHostToken: true,
        scopeReady: true,
        organizationId: "org",
        workspaceId: "ws",
        isScopeCurrent: () => current,
        getToken: () => "host-secret",
      },
      { fetchImpl },
    );
    const result = client.listWorkforce();
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    current = false;
    resolveResponse(
      new Response(JSON.stringify({ agents: [{ id: "prior-tenant-agent" }] })),
    );
    await expect(result).resolves.toMatchObject({
      available: false,
      code: "SCOPE_UNAVAILABLE",
    });
  });

  it("posts a customer conversation to the remote workspace", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ conversation: { id: "run-1" } }), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => null,
        authMode: "cookie",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );
    const result = await client.createConversation("summarize the queue", {
      mode: "agent",
      agentId: "report writer",
      idempotencyKey: "conversation-create-1",
    });

    expect(result).toMatchObject({
      available: true,
      conversation: { id: "run-1" },
    });
    expect(fetchImpl.mock.calls[0][0]).toBe(
      "https://omega.example.com/workspace/conversations",
    );
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({
        input: "summarize the queue",
        mode: "agent",
        agentId: "report writer",
      }),
      headers: expect.objectContaining({
        "Idempotency-Key": "conversation-create-1",
      }),
    });
  });

  it("reads tenant-scoped workforce health and derives status counts", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schemaVersion: "omega-workforce/v1",
          capabilities: { canCreateAgent: true },
          agents: [
            {
              id: "agent-1",
              name: "Research",
              status: "working",
              currentWork: "Review queue",
            },
            {
              id: "agent-2",
              name: "Support",
              status: "idle",
              currentWork: null,
            },
            {
              id: "agent-3",
              name: "Billing",
              status: "needs_attention",
              reason: "last_task_failed",
            },
          ],
          teams: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    const result = await client.getFleetHealth();

    expect(result).toMatchObject({
      available: true,
      health: {
        total: 3,
        healthy: 2,
        active: 1,
        ready: 1,
        attention: 1,
        paused: 0,
        offline: 0,
      },
    });
    expect(result.agents[2]).toMatchObject({
      name: "Billing",
      status: "needs_attention",
      reason: "last_task_failed",
    });
    expect(fetchImpl.mock.calls[0][0]).toBe(
      "https://omega.example.com/workspace/workforce",
    );
    expect(fetchImpl.mock.calls[0][1].headers).toMatchObject({
      "X-Organization-Id": "org-1",
      "X-Workspace-Id": "ws-1",
    });
  });

  it("fails closed when the workforce envelope contains an unknown health state", () => {
    expect(
      parseFleetHealth({
        schemaVersion: "omega-workforce/v1",
        agents: [{ name: "Research", status: "mystery" }],
      }),
    ).toMatchObject({
      available: false,
      code: "MALFORMED",
    });
  });

  it("does not label a local or unverified sandbox as cloud", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ items: [{ id: "sbx-1", providerKind: "local" }] }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(client.listSandboxes()).resolves.toMatchObject({
      available: false,
      code: "ISOLATION_UNVERIFIED",
    });
  });

  it("verifies list-only sandboxes through remote detail before labeling them cloud", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: "sbx-1" }] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sandbox: { id: "sbx-1", providerKind: "container" },
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(client.listSandboxes()).resolves.toMatchObject({
      available: true,
      items: [{ id: "sbx-1", providerKind: "container" }],
    });
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/os/sandboxes",
      "https://omega.example.com/os/sandboxes/sbx-1",
    ]);
  });

  it("uses the remote preview-token route and sends connector agent binding", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ connected: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: "preview-token" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.connectConnector("conn-1", { agent: "agent-1" }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.mintSandboxPreview("sbx-1", "svc-1"),
    ).resolves.toMatchObject({ available: true });
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ agent: "agent-1" }),
    });
    expect(fetchImpl.mock.calls[1][0]).toBe(
      "https://omega.example.com/os/sandboxes/sbx-1/services/svc-1/preview-token",
    );
  });

  it("verifies the remote proof pack against the addressed conversation", async () => {
    const proofBody = {
      protocol: OMEGA_PROOF_PACK_PROTOCOL,
      generatedAt: "2026-10-12T00:00:00.000Z",
      task: { id: "run-1" },
      answer: "done",
      spend: { costUsd: 0, tokens: 0 },
      receipts: { groundTruth: [], promotionRefs: [] },
      artifacts: [],
      embedMaxBytes: 0,
    };
    const proofPack = {
      ...proofBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(proofBody),
      },
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(proofPack), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.getConversationProofPack("run-1"),
    ).resolves.toMatchObject({
      available: true,
      verification: { available: true, ok: true, violations: [] },
      proofPack: { task: { id: "run-1" } },
    });

    const tampered = { ...proofPack, answer: "tampered" };
    fetchImpl.mockResolvedValueOnce(
      new Response(JSON.stringify(tampered), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(
      client.getConversationProofPack("run-1"),
    ).resolves.toMatchObject({
      available: false,
      code: "PROOF_UNVERIFIED",
      verification: { available: true, ok: false },
    });
  });

  it("exposes only sealed, observed per-task isolation receipts from a proof pack", async () => {
    const receipt = {
      schemaVersion: OMEGA_RUN_EVIDENCE_ISOLATION_PROTOCOL,
      runId: "run-iso",
      planId: "plan-1",
      provider: "docker",
      isolationClass: "oci_container",
      instanceId: "container-run-iso",
      imageDigest: `sha256:${"a".repeat(64)}`,
      resourceLimitsSha256: "b".repeat(64),
      bootEvidenceStatus: "observed",
      bootAttempted: true,
      bootSucceeded: true,
      cleanupStatus: "destroyed",
      createdAt: "2026-10-12T00:00:00.000Z",
      startedAt: "2026-10-12T00:00:01.000Z",
      stoppedAt: "2026-10-12T00:00:02.000Z",
      cleanupCompletedAt: "2026-10-12T00:00:03.000Z",
    };
    const taskTreeBody = {
      schemaVersion: "omega-run-task-tree-evidence/v1",
      snapshot: { runId: "run-iso", agentId: "agent-1", trees: [] },
      childTerminals: [],
      completeness: {
        status: "complete",
        missingTerminalCount: 0,
        missingEvidenceCount: 0,
        withheldTerminalEventCount: 0,
      },
    };
    const taskTree = {
      ...taskTreeBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(taskTreeBody),
      },
    };
    const manifestBody = {
      schemaVersion: "omega-run-evidence/v1",
      runId: "run-iso",
      agentId: "agent-1",
      accountId: "account-1",
      status: "done",
      verified: true,
      tasks: [],
      isolationReceipt: receipt,
      taskTree,
      reasons: [],
    };
    const manifest = {
      ...manifestBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(manifestBody),
      },
    };
    const linkBody = {
      schemaVersion: OMEGA_PROOF_PACK_RUN_EVIDENCE_PROTOCOL,
      manifestHash: manifest.integrity.hash,
      taskTreeHash: taskTree.integrity.hash,
      manifest,
    };
    const runEvidence = {
      ...linkBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(linkBody),
      },
    };
    const proofBody = {
      protocol: OMEGA_PROOF_PACK_PROTOCOL,
      generatedAt: "2026-10-12T00:00:00.000Z",
      task: { id: "run-iso" },
      answer: "done",
      spend: { costUsd: 0, tokens: 0 },
      receipts: { groundTruth: [], promotionRefs: [] },
      artifacts: [],
      embedMaxBytes: 0,
      runEvidence,
    };
    const proofPack = {
      ...proofBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(proofBody),
      },
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(proofPack), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.getConversationIsolationReceipt("run-iso"),
    ).resolves.toMatchObject({
      available: true,
      receipt,
      verification: { status: "verified", violations: [] },
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://omega.example.com/workspace/conversations/run-iso/proof-pack",
      expect.any(Object),
    );
  });

  it("keeps missing per-task isolation evidence explicitly unavailable", async () => {
    const proofBody = {
      protocol: OMEGA_PROOF_PACK_PROTOCOL,
      generatedAt: "2026-10-12T00:00:00.000Z",
      task: { id: "run-unknown" },
      answer: "done",
      spend: { costUsd: 0, tokens: 0 },
      receipts: { groundTruth: [], promotionRefs: [] },
      artifacts: [],
      embedMaxBytes: 0,
    };
    const proofPack = {
      ...proofBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(proofBody),
      },
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(proofPack), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "session-token",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.getConversationIsolationReceipt("run-unknown"),
    ).resolves.toMatchObject({
      available: false,
      code: "ISOLATION_UNVERIFIED",
    });
  });

  it("uses customer-scoped workforce routes and keeps the server-owned scope headers", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schemaVersion: "omega-workforce/v1",
            capabilities: {
              canCreateAgent: true,
              role: "owner",
              permittedActions: ["view", "run", "create"],
            },
            connectorBindings: [],
            agents: [{ id: "agent-1", kind: "agent", name: "Maya" }],
            teams: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schemaVersion: "omega-workforce/v1",
            agent: { id: "agent-1", kind: "agent", name: "Maya" },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schemaVersion: "omega-workforce/v1",
            agent: { id: "agent-2", kind: "agent", name: "Ada" },
          }),
          { status: 201, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schemaVersion: "omega-workforce/v1",
            agent: { id: "agent-2", kind: "agent", name: "Ada" },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schemaVersion: "omega-workforce/v1",
            agent: {
              id: "agent-2",
              kind: "agent",
              name: "Ada",
              status: "working",
            },
            action: "start",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-server-selected",
        workspaceId: "workspace-server-selected",
      },
      { fetchImpl },
    );

    await expect(client.listWorkforce()).resolves.toMatchObject({
      available: true,
      agents: [{ id: "agent-1" }],
    });
    await expect(client.getWorkforceAgent("agent/1")).resolves.toMatchObject({
      available: true,
      agent: { id: "agent-1" },
    });
    await expect(
      client.createWorkforceAgent({ name: "Ada" }, "agent-create-1"),
    ).resolves.toMatchObject({ available: true, agent: { id: "agent-2" } });
    await expect(
      client.updateWorkforceAgent(
        "agent-2",
        { purpose: "Review" },
        "agent-update-1",
      ),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.controlWorkforceAgent("agent-2", "start", "agent-control-1"),
    ).resolves.toMatchObject({ available: true, action: "start" });

    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/workspace/workforce",
      "https://omega.example.com/workspace/workforce/agents/agent%2F1",
      "https://omega.example.com/workspace/workforce/agents",
      "https://omega.example.com/workspace/workforce/agents/agent-2",
      "https://omega.example.com/workspace/workforce/agents/agent-2/control",
    ]);
    expect(
      fetchImpl.mock.calls.every(
        ([, init]) =>
          init.credentials === "omit" &&
          init.headers.Authorization === "Bearer omega-session" &&
          init.headers["X-Organization-Id"] === "org-server-selected" &&
          init.headers["X-Workspace-Id"] === "workspace-server-selected",
      ),
    ).toBe(true);
    expect(fetchImpl.mock.calls[2][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ name: "Ada" }),
    });
    expect(fetchImpl.mock.calls[3][1]).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ purpose: "Review" }),
    });
    expect(fetchImpl.mock.calls[4][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ action: "start" }),
    });
    expect(
      fetchImpl.mock.calls
        .slice(2)
        .map(([, init]) => init.headers["Idempotency-Key"]),
    ).toEqual(["agent-create-1", "agent-update-1", "agent-control-1"]);
  });

  it("lists and creates customer conversations without falling back to operator routes", async () => {
    const proofBody = {
      protocol: OMEGA_PROOF_PACK_PROTOCOL,
      generatedAt: "2026-10-12T00:00:00.000Z",
      task: { id: "run/2" },
      answer: "done",
      receipts: { groundTruth: [], promotionRefs: [] },
      artifacts: [],
      embedMaxBytes: 0,
    };
    const proofPack = {
      ...proofBody,
      integrity: {
        algorithm: "sha256",
        hash: await hashCanonicalJson(proofBody),
      },
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            conversations: [{ id: "run-1" }],
            nextCursor: "next-1",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ conversation: { id: "run-2" } }), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ conversation: { id: "run-2", status: "done" } }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: { id: "msg-1" } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ evidence: { status: "complete" } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(proofPack), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.listConversations({
        limit: 25,
        agentId: "agent-1",
        status: "active",
      }),
    ).resolves.toMatchObject({
      available: true,
      conversations: [{ id: "run-1" }],
      nextCursor: "next-1",
    });
    await expect(
      client.createConversation("summarize queue", {
        mode: "agent",
        agentId: "agent-1",
        title: "Queue",
      }),
    ).resolves.toMatchObject({
      available: true,
      conversation: { id: "run-2" },
    });
    await expect(client.getConversation("run/2")).resolves.toMatchObject({
      available: true,
      conversation: { id: "run-2" },
    });
    await expect(
      client.sendConversationMessage("run/2", "continue", {
        mode: "chat",
        idempotencyKey: "follow-up-1",
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getConversationEvidence("run/2"),
    ).resolves.toMatchObject({
      available: true,
      evidence: { status: "complete" },
    });
    await expect(
      client.getConversationProofPack("run/2"),
    ).resolves.toMatchObject({
      available: true,
      proofPack: { protocol: OMEGA_PROOF_PACK_PROTOCOL, task: { id: "run/2" } },
      verification: { available: true, ok: true, violations: [] },
    });

    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/workspace/conversations?limit=25&agentId=agent-1&status=active",
      "https://omega.example.com/workspace/conversations",
      "https://omega.example.com/workspace/conversations/run%2F2",
      "https://omega.example.com/workspace/conversations/run%2F2/messages",
      "https://omega.example.com/workspace/conversations/run%2F2/evidence",
      "https://omega.example.com/workspace/conversations/run%2F2/proof-pack",
    ]);
    expect(fetchImpl.mock.calls[1][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        input: "summarize queue",
        mode: "agent",
        agentId: "agent-1",
        title: "Queue",
      }),
    });
    expect(fetchImpl.mock.calls[3][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ input: "continue", mode: "chat" }),
      headers: expect.objectContaining({ "Idempotency-Key": "follow-up-1" }),
    });
  });

  it("opens an owned conversation event stream without consuming its response body", async () => {
    const stream = new Response("event: conversation\ndata: {}\n\n", {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
    const fetchImpl = vi.fn().mockResolvedValue(stream);
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    const result = await client.getConversationEvents("run-1", {
      lastEventId: 9,
    });
    expect(result).toMatchObject({ available: true, response: stream });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://omega.example.com/workspace/conversations/run-1/events",
      expect.objectContaining({
        credentials: "omit",
        headers: expect.objectContaining({
          Accept: "text/event-stream",
          "Last-Event-ID": "9",
        }),
      }),
    );
    expect(stream.bodyUsed).toBe(false);
  });

  it("supports Omega task-scoped interrupt and continue controls", async () => {
    const json = (value) =>
      new Response(JSON.stringify(value), {
        status: 202,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-workspace/v1",
          ok: true,
          id: "proc-1",
          verb: "interrupt",
          status: "paused",
          eventCount: 4,
          receipt: {
            auditEntryId: "audit-1",
            hash: "hash-1",
            recordedAt: "2026-10-12T00:00:00.000Z",
          },
        }),
      )
      .mockResolvedValueOnce(
        json({ conversation: { id: "run-1", status: "running" } }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.controlConversation("run-1", "interrupt", {}, "control-1"),
    ).resolves.toMatchObject({
      available: true,
      ok: true,
      receipt: { auditEntryId: "audit-1", hash: "hash-1" },
    });
    await expect(
      client.controlConversation("run-1", "continue"),
    ).resolves.toMatchObject({ available: true });
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/workspace/conversations/run-1/interrupt",
      "https://omega.example.com/workspace/conversations/run-1/continue",
    ]);
    expect(fetchImpl.mock.calls[0][1].headers).toMatchObject({
      "Idempotency-Key": "control-1",
    });
  });

  it("preserves remote mutation transport receipts for reconciliation", async () => {
    const response = new Response(
      JSON.stringify({ ok: true, status: "accepted" }),
      {
        status: 202,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req-1",
          "x-omega-operation-id": "op-1",
          "x-omega-idempotency-key": "control-1",
          etag: '"receipt-1"',
        },
      },
    );
    const fetchImpl = vi.fn().mockResolvedValue(response);
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.controlConversation("run-1", "stop", {}, "control-1"),
    ).resolves.toMatchObject({
      available: true,
      transport: {
        status: 202,
        headers: {
          "x-request-id": "req-1",
          "x-omega-operation-id": "op-1",
          "x-omega-idempotency-key": "control-1",
          etag: '"receipt-1"',
        },
      },
    });
  });

  it("keeps managed-agent create, edit, and lifecycle controls on tenant workforce routes", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({ agent: { id: "agent-1", name: "Maya" } }, 201),
      )
      .mockResolvedValueOnce(
        json({ agent: { id: "agent-1", name: "Maya", role: "Researcher" } }),
      )
      .mockResolvedValueOnce(
        json({ action: "pause", agent: { id: "agent-1", status: "paused" } }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(
      client.createWorkforceAgent({
        name: "Maya",
        role: "Researcher",
        purpose: "Review reports",
        responsibilities: ["Summarize findings"],
        workMode: "on_demand",
        connectorBindingIds: [],
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.updateWorkforceAgent("agent-1", {
        purpose: "Review weekly reports",
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.controlWorkforceAgent("agent-1", "pause"),
    ).resolves.toMatchObject({ available: true, action: "pause" });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [url, init.method, init.body]),
    ).toEqual([
      [
        "https://omega.example.com/workspace/workforce/agents",
        "POST",
        JSON.stringify({
          name: "Maya",
          role: "Researcher",
          purpose: "Review reports",
          responsibilities: ["Summarize findings"],
          workMode: "on_demand",
          connectorBindingIds: [],
        }),
      ],
      [
        "https://omega.example.com/workspace/workforce/agents/agent-1",
        "PATCH",
        JSON.stringify({ purpose: "Review weekly reports" }),
      ],
      [
        "https://omega.example.com/workspace/workforce/agents/agent-1/control",
        "POST",
        JSON.stringify({ action: "pause" }),
      ],
    ]);
    expect(
      fetchImpl.mock.calls.every(
        ([, init]) => init.headers.Authorization === "Bearer omega-session",
      ),
    ).toBe(true);
  });

  it("reads tenant agent lineage and sends fenced, idempotent rollback requests", async () => {
    const json = (value) =>
      new Response(JSON.stringify(value), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          currentVersion: 3,
          revisions: [{ version: 2, current: false }],
        }),
      )
      .mockResolvedValueOnce(
        json({
          deployment: { version: 3, processId: "proc-3" },
          deployments: [{ version: 2, active: false }],
        }),
      )
      .mockResolvedValueOnce(json({ agent: { id: "agent-1", version: 4 } }))
      .mockResolvedValueOnce(
        json({ deployments: [{ version: 2, active: true }] }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(
      client.listWorkforceAgentRevisions("agent-1"),
    ).resolves.toMatchObject({ available: true, currentVersion: 3 });
    await expect(
      client.listWorkforceAgentDeployments("agent-1"),
    ).resolves.toMatchObject({
      available: true,
      deployment: { processId: "proc-3" },
    });
    await expect(
      client.rollbackWorkforceAgentRevision(
        "agent-1",
        2,
        { expectedVersion: 3 },
        "revision-key",
      ),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.rollbackWorkforceAgentDeployment(
        "agent-1",
        2,
        {
          expectedDeploymentVersion: 3,
          expectedProcessId: "proc-3",
          reason: "customer requested",
        },
        "deployment-key",
      ),
    ).resolves.toMatchObject({ available: true });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [
        url,
        init.method,
        init.headers["Idempotency-Key"],
        init.body,
      ]),
    ).toEqual([
      [
        "https://omega.example.com/workspace/workforce/agents/agent-1/revisions",
        "GET",
        undefined,
        undefined,
      ],
      [
        "https://omega.example.com/workspace/workforce/agents/agent-1/deployments",
        "GET",
        undefined,
        undefined,
      ],
      [
        "https://omega.example.com/workspace/workforce/agents/agent-1/revisions/2/rollback",
        "POST",
        "revision-key",
        JSON.stringify({ expectedVersion: 3 }),
      ],
      [
        "https://omega.example.com/workspace/workforce/agents/agent-1/deployments/2/rollback",
        "POST",
        "deployment-key",
        JSON.stringify({
          expectedDeploymentVersion: 3,
          expectedProcessId: "proc-3",
          reason: "customer requested",
        }),
      ],
    ]);
  });

  it("fails closed for the operator-global connector build route", async () => {
    const fetchImpl = vi.fn();
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.buildConnector({ name: "private" }),
    ).resolves.toMatchObject({
      available: false,
      code: "OPERATOR_ONLY",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("uses only owner-bound workspace audit projections and never operator-global routes", async () => {
    const page = {
      schemaVersion: "omega-workspace-audit/v1",
      scope: { accountId: "acct-1", orgId: "org-1", workspaceId: "ws-1" },
      entries: [],
      snapshot: "snap-1",
      page: { limit: 25, hasMore: false },
      coverage: {
        source: "managed-lifecycle",
        complete: false,
        since: null,
        exclusions: [],
      },
      integrity: {
        schemaVersion: "omega-workspace-audit/v1",
        state: "empty",
        ok: null,
        checkedAt: null,
        length: 0,
        headHash: null,
        verification: "server-hmac",
        snapshot: "snap-1",
      },
    };
    const integrity = {
      schemaVersion: "omega-workspace-audit/v1",
      state: "empty",
      ok: null,
      checkedAt: null,
      length: 0,
      headHash: null,
      verification: "server-hmac",
      snapshot: "snap-1",
    };
    const json = (value) =>
      new Response(JSON.stringify(value), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json(page))
      .mockResolvedValueOnce(json(integrity));
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(
      client.getTenantAudit({
        limit: 25,
        cursor: "next-1",
        organizationId: "foreign",
      }),
    ).resolves.toMatchObject({ available: true, ...page });
    await expect(
      client.getTenantAuditIntegrity("snap-1"),
    ).resolves.toMatchObject({ available: true, ...integrity });
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/workspace/audit?limit=25&cursor=next-1",
      "https://omega.example.com/workspace/audit/integrity?snapshot=snap-1",
    ]);
    expect(
      fetchImpl.mock.calls.every(([url]) => !url.includes("/os/audit")),
    ).toBe(true);
  });

  it("keeps lifecycle, governance, connector, billing, and sandbox operations on remote routes", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json({ control: "paused" }))
      .mockResolvedValueOnce(json({ tree: [] }))
      .mockResolvedValueOnce(json({ approvals: [] }))
      .mockResolvedValueOnce(json({ schedule: { id: "sched-1" } }, 201))
      .mockResolvedValueOnce(json({ connectors: [] }))
      .mockResolvedValueOnce(json({ oauth: { status: "disconnected" } }))
      .mockResolvedValueOnce(json({ scopes: [] }))
      .mockResolvedValueOnce(json({ events: [] }))
      .mockResolvedValueOnce(json({ event: { id: "evt-1" } }))
      .mockResolvedValueOnce(json({ lineage: [] }))
      .mockResolvedValueOnce(json({ recall: { items: [] } }))
      .mockResolvedValueOnce(json({ graph: { nodes: [], edges: [] } }))
      .mockResolvedValueOnce(json({ timeline: [] }))
      .mockResolvedValueOnce(json({ consolidation: { pending: [] } }))
      .mockResolvedValueOnce(
        json({ schemaVersion: "omega-sandbox/v1", id: "sbx-1", files: [] }),
      )
      .mockResolvedValueOnce(
        json({ schemaVersion: "omega-sandbox/v1", id: "sbx-1", ok: true }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.getConversationControlStatus("run-1"),
    ).resolves.toMatchObject({ available: true });
    await expect(client.getConversationTree("run-1")).resolves.toMatchObject({
      available: true,
    });
    await expect(client.listApprovals()).resolves.toMatchObject({
      available: true,
      approvals: [],
    });
    await expect(
      client.createSchedule({ name: "nightly" }, "schedule-key"),
    ).resolves.toMatchObject({ available: true });
    await expect(client.listConnectors()).resolves.toMatchObject({
      available: true,
      connectors: [],
    });
    await expect(client.getConnectorOAuth("conn/1")).resolves.toMatchObject({
      available: true,
    });
    await expect(client.listMemoryScopes()).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.getMemoryEvents({ scope: "org:org-1" }),
    ).resolves.toMatchObject({ available: true });
    await expect(client.getMemoryEvent("evt-1")).resolves.toMatchObject({
      available: true,
    });
    await expect(client.getMemoryLineage("evt-1")).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.getMemoryRecall({ principal: "acct-1", scope: "org:org-1" }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getMemoryGraph({ root: "org:org-1" }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getMemoryTimeline({ scope: "org:org-1" }),
    ).resolves.toMatchObject({ available: true });
    await expect(client.getMemoryConsolidation()).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.listSandboxFiles("sbx/1", { path: "src" }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.controlSandbox("sbx-1", "hibernate", { reason: "idle" }),
    ).resolves.toMatchObject({ available: true });

    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://omega.example.com/workspace/conversations/run-1/control-status",
      "https://omega.example.com/workspace/conversations/run-1/tree",
      "https://omega.example.com/workspace/approvals",
      "https://omega.example.com/workspace/schedules",
      "https://omega.example.com/os/connectors",
      "https://omega.example.com/os/connectors/conn%2F1/oauth",
      "https://omega.example.com/os/memory/scopes",
      "https://omega.example.com/os/memory/events?scope=org%3Aorg-1",
      "https://omega.example.com/os/memory/events/evt-1",
      "https://omega.example.com/os/memory/events/evt-1/lineage",
      "https://omega.example.com/os/memory/recall?principal=acct-1&scope=org%3Aorg-1",
      "https://omega.example.com/os/memory/graph?root=org%3Aorg-1",
      "https://omega.example.com/os/memory/timeline?scope=org%3Aorg-1",
      "https://omega.example.com/os/memory/consolidation",
      "https://omega.example.com/os/sandboxes/sbx%2F1/files?path=src",
      "https://omega.example.com/os/sandboxes/sbx-1/hibernate",
    ]);
    expect(fetchImpl.mock.calls[3][1]).toMatchObject({
      method: "POST",
      headers: expect.objectContaining({ "Idempotency-Key": "schedule-key" }),
    });
  });

  it("supports schedule detail, edit, and deletion with scoped idempotency headers", async () => {
    const json = (value) =>
      new Response(JSON.stringify(value), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json({ schedule: { id: "sched-1" }, runs: [] }))
      .mockResolvedValueOnce(
        json({ schedule: { id: "sched-1", title: "Updated" } }),
      )
      .mockResolvedValueOnce(json({ deleted: true, id: "sched-1" }));
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(client.getSchedule("sched-1")).resolves.toMatchObject({
      available: true,
      runs: [],
    });
    await expect(
      client.updateSchedule("sched-1", { title: "Updated" }, "edit-key"),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.deleteSchedule("sched-1", "delete-key"),
    ).resolves.toMatchObject({ available: true, deleted: true });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [
        url,
        init.method,
        init.headers["Idempotency-Key"],
      ]),
    ).toEqual([
      [
        "https://omega.example.com/workspace/schedules/sched-1",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/workspace/schedules/sched-1",
        "PATCH",
        "edit-key",
      ],
      [
        "https://omega.example.com/workspace/schedules/sched-1",
        "DELETE",
        "delete-key",
      ],
    ]);
    expect(fetchImpl.mock.calls[1][1].body).toBe(
      JSON.stringify({ title: "Updated" }),
    );
    expect(fetchImpl.mock.calls[0][1].headers).toMatchObject({
      Authorization: "Bearer omega-session",
      "X-Organization-Id": "org-1",
      "X-Workspace-Id": "ws-1",
    });
  });

  it("reads the customer-safe billing and usage contract without exposing mutations", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json({ version: "rate-card/v1", rows: [] }))
      .mockResolvedValueOnce(json({ configured: false, meters: [] }))
      .mockResolvedValueOnce(json({ items: [{ id: "acct-1", name: "Acme" }] }))
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", status: "active", planId: "team" }),
      )
      .mockResolvedValueOnce(json({ accountId: "acct-1", seats: 1 }))
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", seats: 2, totalCents: 1000 }),
      )
      .mockResolvedValueOnce(json({ accountId: "acct-1", totals: {} }))
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", dimension: "model", rows: [], totals: {} }),
      )
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", records: [], total: 0 }),
      )
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", configured: false, issued: [] }),
      )
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", matches: true, kinds: [] }),
      )
      .mockResolvedValueOnce(json({ id: "invoice-1", totalCents: 0 }))
      .mockResolvedValueOnce(
        json({ accountId: "acct-1", balanceCents: 0, entries: [] }),
      )
      .mockResolvedValueOnce(json({ usage: true, subscription: true }));
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(client.getBillingRateCard()).resolves.toMatchObject({
      available: true,
    });
    await expect(client.getBillingMeter()).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.listBillingAccounts({ limit: 10 }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getBillingSubscription("acct-1"),
    ).resolves.toMatchObject({ available: true });
    await expect(client.getBillingSeats("acct-1")).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.getBillingSeatQuote("acct-1", { seats: 2 }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getBillingUsage("acct-1", {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-11-01T00:00:00.000Z",
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getBillingUsageAttribution("acct-1", {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-11-01T00:00:00.000Z",
        by: "model",
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getBillingUsageRecords("acct-1", {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-11-01T00:00:00.000Z",
        limit: 50,
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(client.getBillingInvoices("acct-1")).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.getBillingMeterReconciliation("acct-1", {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-11-01T00:00:00.000Z",
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getBillingInvoice("acct-1", {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-11-01T00:00:00.000Z",
      }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.getBillingCredits("acct-1", { limit: 20 }),
    ).resolves.toMatchObject({ available: true });
    await expect(client.getBillingIntegrity("acct-1")).resolves.toMatchObject({
      available: true,
    });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [url, init.method]),
    ).toEqual([
      ["https://omega.example.com/billing/rate-card", "GET"],
      ["https://omega.example.com/billing/meter", "GET"],
      ["https://omega.example.com/billing/accounts?limit=10", "GET"],
      ["https://omega.example.com/billing/accounts/acct-1/subscription", "GET"],
      ["https://omega.example.com/billing/accounts/acct-1/seats", "GET"],
      [
        "https://omega.example.com/billing/accounts/acct-1/seats/quote?seats=2",
        "GET",
      ],
      [
        "https://omega.example.com/billing/accounts/acct-1/usage?start=2026-10-01T00%3A00%3A00.000Z&end=2026-11-01T00%3A00%3A00.000Z",
        "GET",
      ],
      [
        "https://omega.example.com/billing/accounts/acct-1/usage/attribution?start=2026-10-01T00%3A00%3A00.000Z&end=2026-11-01T00%3A00%3A00.000Z&by=model",
        "GET",
      ],
      [
        "https://omega.example.com/billing/accounts/acct-1/usage/records?start=2026-10-01T00%3A00%3A00.000Z&end=2026-11-01T00%3A00%3A00.000Z&limit=50",
        "GET",
      ],
      ["https://omega.example.com/billing/accounts/acct-1/invoices", "GET"],
      [
        "https://omega.example.com/billing/accounts/acct-1/meter?start=2026-10-01T00%3A00%3A00.000Z&end=2026-11-01T00%3A00%3A00.000Z",
        "GET",
      ],
      [
        "https://omega.example.com/billing/accounts/acct-1/invoice?start=2026-10-01T00%3A00%3A00.000Z&end=2026-11-01T00%3A00%3A00.000Z",
        "GET",
      ],
      [
        "https://omega.example.com/billing/accounts/acct-1/credits?limit=20",
        "GET",
      ],
      ["https://omega.example.com/billing/accounts/acct-1/integrity", "GET"],
    ]);
    expect(
      fetchImpl.mock.calls.every(
        ([, init]) =>
          init.method === "GET" &&
          init.headers.Authorization === "Bearer omega-session",
      ),
    ).toBe(true);
  });

  it("keeps the webhook console on tenant channel routes and preserves mutation receipts", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          "content-type": "application/json",
          "x-omega-receipt-id": "receipt-1",
        },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({ channels: [{ channelId: "managed-runs", events: 2 }] }),
      )
      .mockResolvedValueOnce(
        json({ subscriptions: [{ id: "sub-1", status: "active" }] }),
      )
      .mockResolvedValueOnce(
        json({ items: [{ eventId: "evt-1", eventKey: "run.completed" }] }),
      )
      .mockResolvedValueOnce(
        json({ deliveries: [{ id: "delivery-1", status: "dead_letter" }] }),
      )
      .mockResolvedValueOnce(
        json({ channelId: "managed-runs", summary: { timelyDeliveryRate: 1 } }),
      )
      .mockResolvedValueOnce(json({ subscription: { id: "sub-2" } }, 201))
      .mockResolvedValueOnce(json({ tested: true }))
      .mockResolvedValueOnce(json({ rotated: true }))
      .mockResolvedValueOnce(json({ deleted: true }))
      .mockResolvedValueOnce(json({ redriven: true }));
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(client.listChannels()).resolves.toMatchObject({
      available: true,
      channels: [{ channelId: "managed-runs" }],
    });
    await expect(
      client.listChannelSubscriptions("managed-runs"),
    ).resolves.toMatchObject({
      available: true,
      subscriptions: [{ id: "sub-1" }],
    });
    await expect(
      client.listChannelEvents("managed-runs", { limit: 20 }),
    ).resolves.toMatchObject({
      available: true,
      items: [{ eventId: "evt-1" }],
    });
    await expect(
      client.listChannelEventDeliveries("managed-runs", "run.completed"),
    ).resolves.toMatchObject({
      available: true,
      deliveries: [{ id: "delivery-1" }],
    });
    await expect(
      client.getChannelDeliverySlo("managed-runs", { window: "24h" }),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.createChannelSubscription(
        "managed-runs",
        { url: "https://hooks.example.com/omega" },
        "create-1",
      ),
    ).resolves.toMatchObject({
      available: true,
      transport: { headers: { "x-omega-receipt-id": "receipt-1" } },
    });
    await expect(
      client.testChannelSubscription("managed-runs", "sub-1"),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.rotateChannelSubscriptionSecret(
        "managed-runs",
        "sub-1",
        { signingSecret: "new-secret" },
        "rotate-1",
      ),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.deleteChannelSubscription("managed-runs", "sub-1", "delete-1"),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.redriveChannelDelivery(
        "managed-runs",
        "run.completed",
        "delivery-1",
        "redrive-1",
      ),
    ).resolves.toMatchObject({ available: true });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [
        url,
        init.method,
        init.headers["Idempotency-Key"],
      ]),
    ).toEqual([
      ["https://omega.example.com/os/channels", "GET", undefined],
      [
        "https://omega.example.com/os/channels/managed-runs/subscriptions",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/events?limit=20",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/events/run.completed/deliveries",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/delivery-slo?window=24h",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/subscriptions",
        "POST",
        "create-1",
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/subscriptions/sub-1/test",
        "POST",
        undefined,
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/subscriptions/sub-1/signing-secret",
        "POST",
        "rotate-1",
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/subscriptions/sub-1",
        "DELETE",
        "delete-1",
      ],
      [
        "https://omega.example.com/os/channels/managed-runs/events/run.completed/deliveries/delivery-1/redrive",
        "POST",
        "redrive-1",
      ],
    ]);
  });

  it("keeps agent-team CRUD tenant-scoped and sends restart-safe idempotency keys", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    const team = {
      id: "agtm_team-1",
      name: "Research",
      purpose: "Investigate incidents",
      agentIds: ["agent-1"],
      useMinRole: "member",
      editMinRole: "admin",
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-agent-teams/v1",
          teams: [team],
          canCreate: true,
        }),
      )
      .mockResolvedValueOnce(
        json({ schemaVersion: "omega-agent-teams/v1", team }, 201),
      )
      .mockResolvedValueOnce(
        json({ schemaVersion: "omega-agent-teams/v1", team }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-agent-teams/v1",
          teams: [],
          canCreate: true,
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(client.listAgentTeams()).resolves.toMatchObject({
      available: true,
      teams: [{ id: team.id }],
      canCreate: true,
    });
    await expect(
      client.createAgentTeam(
        { name: team.name, purpose: team.purpose, agentIds: team.agentIds },
        "team-create-1",
      ),
    ).resolves.toMatchObject({ available: true, team: { id: team.id } });
    await expect(
      client.updateAgentTeam(
        team.id,
        { purpose: "Investigate production incidents" },
        "team-update-1",
      ),
    ).resolves.toMatchObject({ available: true, team: { id: team.id } });
    await expect(
      client.deleteAgentTeam(team.id, "team-delete-1"),
    ).resolves.toMatchObject({ available: true, teams: [] });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [
        url,
        init.method,
        init.headers["Idempotency-Key"],
      ]),
    ).toEqual([
      ["https://omega.example.com/workspace/agent-teams", "GET", undefined],
      [
        "https://omega.example.com/workspace/agent-teams",
        "POST",
        "team-create-1",
      ],
      [
        "https://omega.example.com/workspace/agent-teams/agtm_team-1",
        "PATCH",
        "team-update-1",
      ],
      [
        "https://omega.example.com/workspace/agent-teams/agtm_team-1",
        "DELETE",
        "team-delete-1",
      ],
    ]);
  });

  it("reads tenant-safe connector vault metadata and sync projections without sending secrets", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-workspace-credentials/v1",
          credentials: [
            {
              credentialId: "cred-1",
              name: "GitHub",
              kind: "oauth_token",
              status: "active",
              bindingCount: 1,
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-workspace-credentials/v1",
          credential: {
            credentialId: "cred-1",
            name: "GitHub",
            kind: "oauth_token",
            status: "active",
            bindingCount: 1,
          },
        }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-workspace-credentials/v1",
          bindings: [
            {
              bindingId: "bind-1",
              credentialId: "cred-1",
              targetKind: "connector",
              targetId: "github",
              surface: "mcp",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-os-connector-data/v1",
          ok: true,
          data: {
            connectorId: "github",
            syncs: { queued: 0, running: 1, succeeded: 4, failed: 0 },
          },
        }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-os-connector-data/v1",
          ok: true,
          sources: [{ id: "source-1", name: "issues" }],
          page: { total: 1 },
        }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-os-connector-data/v1",
          ok: true,
          syncs: [{ id: "sync-1", status: "running" }],
          page: { total: 1 },
        }),
      )
      .mockResolvedValueOnce(
        json({
          schemaVersion: "omega-os-connector-data/v1",
          ok: true,
          schedules: [{ id: "schedule-1", enabled: true }],
          page: { total: 1 },
        }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );

    await expect(client.listCredentialMetadata()).resolves.toMatchObject({
      available: true,
      credentials: [{ credentialId: "cred-1" }],
    });
    await expect(client.getCredentialMetadata("cred-1")).resolves.toMatchObject(
      { available: true, credential: { credentialId: "cred-1" } },
    );
    await expect(
      client.listCredentialBindings("cred-1"),
    ).resolves.toMatchObject({
      available: true,
      bindings: [{ bindingId: "bind-1" }],
    });
    await expect(
      client.getConnectorDataOverview("github", { window: "24h" }),
    ).resolves.toMatchObject({
      available: true,
      data: { connectorId: "github" },
    });
    await expect(
      client.listConnectorSources("github", { limit: 20 }),
    ).resolves.toMatchObject({
      available: true,
      sources: [{ id: "source-1" }],
    });
    await expect(
      client.listConnectorSyncs("github", { limit: 20 }),
    ).resolves.toMatchObject({ available: true, syncs: [{ id: "sync-1" }] });
    await expect(
      client.listConnectorSyncSchedules("github"),
    ).resolves.toMatchObject({
      available: true,
      schedules: [{ id: "schedule-1" }],
    });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [url, init.method, init.body]),
    ).toEqual([
      ["https://omega.example.com/workspace/credentials", "GET", undefined],
      [
        "https://omega.example.com/workspace/credentials/cred-1",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/workspace/credentials/cred-1/bindings",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/connectors/github/data?window=24h",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/connectors/github/sources?limit=20",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/connectors/github/syncs?limit=20",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/connectors/github/sync-schedules",
        "GET",
        undefined,
      ],
    ]);
    expect(
      fetchImpl.mock.calls.every(
        ([, init]) =>
          init.credentials === "omit" &&
          init.headers.Authorization === "Bearer omega-session",
      ),
    ).toBe(true);
  });

  it("keeps custom MCP registration and unavailable runtime capability explicit", async () => {
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          connectors: [
            { id: "mcp-1", kind: "remote_mcp", runtime: { available: false } },
          ],
        }),
      )
      .mockResolvedValueOnce(
        json({ connector: { id: "mcp-1", kind: "remote_mcp" } }),
      )
      .mockResolvedValueOnce(
        json({
          connector: { id: "mcp-1", kind: "remote_mcp" },
          review: { runtime: { available: false } },
        }),
      )
      .mockResolvedValueOnce(
        json({
          connector: { id: "mcp-1", kind: "remote_mcp" },
          review: { runtime: { available: false } },
        }),
      )
      .mockResolvedValueOnce(
        json({ error: { code: "mcp_runtime_unavailable" } }, 503),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: "org-1",
        workspaceId: "ws-1",
      },
      { fetchImpl },
    );
    await expect(client.listCustomConnectors()).resolves.toMatchObject({
      available: true,
      connectors: [{ id: "mcp-1" }],
    });
    await expect(client.getCustomConnector("mcp-1")).resolves.toMatchObject({
      available: true,
    });
    await expect(
      client.getCustomConnectorReview("mcp-1"),
    ).resolves.toMatchObject({
      available: true,
      review: { runtime: { available: false } },
    });
    await expect(
      client.getCustomConnectorRunCapability("mcp-1"),
    ).resolves.toMatchObject({
      available: true,
      review: { runtime: { available: false } },
    });
    await expect(
      client.connectCustomConnector("mcp-1", {}, "mcp-connect-1"),
    ).resolves.toMatchObject({
      available: false,
      code: "mcp_runtime_unavailable",
    });
    expect(
      fetchImpl.mock.calls.map(([url, init]) => [
        url,
        init.method,
        init.headers["Idempotency-Key"],
      ]),
    ).toEqual([
      ["https://omega.example.com/os/custom-connectors", "GET", undefined],
      [
        "https://omega.example.com/os/custom-connectors/mcp-1",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/custom-connectors/mcp-1/review",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/custom-connectors/mcp-1/run-capability",
        "GET",
        undefined,
      ],
      [
        "https://omega.example.com/os/custom-connectors/mcp-1/connect",
        "POST",
        "mcp-connect-1",
      ],
    ]);
  });

  it("keeps organization governance mutations tenant-scoped and idempotent", async () => {
    const json = (value) =>
      new Response(JSON.stringify(value), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        json({ token: "one-time-token", invite: { id: "invite-1" } }),
      )
      .mockResolvedValueOnce(
        json({ ok: true, invite: { id: "invite-1", status: "revoked" } }),
      )
      .mockResolvedValueOnce(json({ accountId: "acct-2", role: "admin" }))
      .mockResolvedValueOnce(
        json({ ok: true, removed: { accountId: "acct-2" } }),
      );
    const client = createOmegaSquadClient(
      {
        baseUrl: "https://omega.example.com",
        getToken: () => "omega-session",
        authMode: "bearer",
        organizationId: null,
        workspaceId: null,
      },
      { fetchImpl },
    );

    await expect(
      client.createOrganizationInvite(
        "org-1",
        { email: "member@example.com", role: "member", manualDelivery: true },
        "invite-key",
      ),
    ).resolves.toMatchObject({ available: true, token: "one-time-token" });
    await expect(
      client.revokeOrganizationInvite("org-1", "invite-1", "revoke-key"),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.updateOrganizationMemberRole(
        "org-1",
        "acct-2",
        "admin",
        "role-key",
      ),
    ).resolves.toMatchObject({ available: true });
    await expect(
      client.removeOrganizationMember("org-1", "acct-2", "remove-key"),
    ).resolves.toMatchObject({ available: true });

    expect(
      fetchImpl.mock.calls.map(([url, init]) => [
        url,
        init.method,
        init.headers["Idempotency-Key"],
        init.body,
      ]),
    ).toEqual([
      [
        "https://omega.example.com/auth/orgs/org-1/invites",
        "POST",
        "invite-key",
        JSON.stringify({
          email: "member@example.com",
          role: "member",
          manualDelivery: true,
        }),
      ],
      [
        "https://omega.example.com/auth/orgs/org-1/invites/invite-1",
        "DELETE",
        "revoke-key",
        undefined,
      ],
      [
        "https://omega.example.com/auth/orgs/org-1/members/acct-2",
        "PATCH",
        "role-key",
        JSON.stringify({ role: "admin" }),
      ],
      [
        "https://omega.example.com/auth/orgs/org-1/members/acct-2",
        "DELETE",
        "remove-key",
        undefined,
      ],
    ]);
  });
});
