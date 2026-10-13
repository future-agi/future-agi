import { describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import WorkspaceAuditPanel from "./WorkspaceAuditPanel";

const SCOPE = { accountId: "acct-1", orgId: "org-1", workspaceId: "ws-1" };
const SCOPE_B = { accountId: "acct-2", orgId: "org-2", workspaceId: "ws-2" };

function integrity(snapshot, state = "verified", overrides = {}) {
  return {
    schemaVersion: "omega-workspace-audit/v1",
    state,
    ok: ["verified", "empty"].includes(state)
      ? true
      : state === "broken"
        ? false
        : null,
    checkedAt: state === "verified" ? "2026-10-13T12:01:00.000Z" : null,
    length: state === "empty" || state === "not_started" ? 0 : 1,
    headHash: state === "empty" || state === "not_started" ? null : "hash-a-1",
    verification: "server-hmac",
    snapshot,
    ...overrides,
  };
}

function page({
  scope = SCOPE,
  snapshot = "snap-1",
  entries = [],
  hasMore = false,
  nextCursor,
  integrityValue = integrity(snapshot),
} = {}) {
  return {
    available: true,
    schemaVersion: "omega-workspace-audit/v1",
    scope,
    entries,
    snapshot,
    page: { limit: 50, hasMore, ...(nextCursor ? { nextCursor } : {}) },
    coverage: {
      source: "managed-lifecycle",
      complete: false,
      since: null,
      exclusions: [
        "authorization-decisions",
        "tool-invocations",
        "customer-configuration-mutations",
        "legacy-unscoped-events",
      ],
    },
    integrity: integrityValue,
  };
}

function entry(id, sequence = 0) {
  return {
    entryId: id,
    sequence,
    recordedAt: "2026-10-13T12:00:00.000Z",
    sourceEventId: `event-${id}`,
    type: "agent.deployed",
    occurredAt: "2026-10-13T11:59:00.000Z",
    agentId: "agent-1",
    previousHash: sequence === 0 ? null : "hash-prev",
    hash: `hash-${id}`,
    alg: "hmac-sha256",
  };
}

describe("WorkspaceAuditPanel", () => {
  it("loads owner-bound records, labels partial coverage, and verifies the same snapshot", async () => {
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(page({ entries: [entry("a-1")] })),
      getTenantAuditIntegrity: vi
        .fn()
        .mockResolvedValue({
          available: true,
          integrity: integrity("snap-1", "verified", {
            checkedAt: "2026-10-13T12:01:00.000Z",
            length: 1,
            headHash: "hash-a-1",
          }),
        }),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    expect(await screen.findByText(/agent.deployed · a-1/)).toBeInTheDocument();
    expect(
      screen.getByText(/Excluded permanently: authorization-decisions/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Server-verified HMAC/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Verify this snapshot" }),
    );
    await waitFor(() =>
      expect(client.getTenantAuditIntegrity).toHaveBeenCalledWith("snap-1"),
    );
    expect(await screen.findByText("Verified")).toBeInTheDocument();
  });

  it("pages only at the original high-water snapshot and refuses a mismatched page", async () => {
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValueOnce(
          page({
            entries: [entry("a-1")],
            hasMore: true,
            nextCursor: "cursor-1",
          }),
        )
        .mockResolvedValueOnce(
          page({ snapshot: "snap-changed", entries: [entry("a-2", 1)] }),
        ),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    await screen.findByText(/agent.deployed · a-1/);
    await userEvent.click(
      screen.getByRole("button", { name: "Load more records" }),
    );
    expect(
      await screen.findByText(/high-water snapshot changed/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/agent.deployed · a-2/)).not.toBeInTheDocument();
    expect(client.getTenantAudit).toHaveBeenNthCalledWith(2, {
      limit: 50,
      cursor: "cursor-1",
    });
  });

  it("does not render stale records after a scope change or a late response", async () => {
    const pending = [];
    const client = {
      getTenantAudit: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            pending.push(resolve);
          }),
      ),
    };
    const { rerender } = render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    await act(async () => {
      rerender(
        <WorkspaceAuditPanel
          client={client}
          scopeKey="host-scope-b"
          expectedScope={SCOPE_B}
        />,
      );
    });
    await act(async () => pending[0](page({ entries: [entry("stale")] })));
    expect(screen.queryByText(/stale/)).not.toBeInTheDocument();
    expect(screen.queryByText(/agent.deployed/)).not.toBeInTheDocument();
    expect(client.getTenantAudit).toHaveBeenCalledTimes(2);
    await act(async () =>
      pending[1](page({ scope: SCOPE_B, entries: [entry("current")] })),
    );
    expect(
      await screen.findByText(/agent.deployed · current/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/stale/)).not.toBeInTheDocument();
  });

  it("distinguishes empty and not-started integrity without claiming a verified chain", async () => {
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(
          page({
            snapshot: "empty-snapshot",
            integrityValue: integrity("empty-snapshot", "empty"),
          }),
        ),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    expect(await screen.findByText("Empty chain")).toBeInTheDocument();
    expect(
      screen.getByText(/No managed lifecycle records/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("shows not-started separately when no native audit stream has been written", async () => {
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(
          page({
            snapshot: null,
            integrityValue: integrity(null, "not_started"),
          }),
        ),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    expect(await screen.findByText("Not started")).toBeInTheDocument();
    expect(
      screen.getByText(/No managed lifecycle records/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Verify this snapshot" }),
    ).toBeDisabled();
  });

  it("refuses an integrity verdict that changes the high-water head", async () => {
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(page({ entries: [entry("a-1")] })),
      getTenantAuditIntegrity: vi
        .fn()
        .mockResolvedValue({
          available: true,
          integrity: integrity("snap-1", "verified", {
            length: 1,
            headHash: "different-head",
          }),
        }),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    await screen.findByText(/agent.deployed · a-1/);
    await userEvent.click(
      screen.getByRole("button", { name: "Verify this snapshot" }),
    );
    expect(
      await screen.findByText(/changed this audit snapshot/),
    ).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });

  it("refuses malformed or foreign scope responses", async () => {
    const client = {
      getTenantAudit: vi.fn().mockResolvedValue(page({ scope: SCOPE_B })),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-scope-a"
        expectedScope={SCOPE}
      />,
    );
    expect(await screen.findByText(/scope does not match/)).toBeInTheDocument();
    expect(screen.queryByText(/agent.deployed/)).not.toBeInTheDocument();
  });

  it("accepts native scope from the first authenticated response and pages within its frozen snapshot", async () => {
    const highwater = integrity("native-snapshot", "verified", {
      length: 2,
      headHash: "hash-new",
    });
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValueOnce(
          page({
            snapshot: "native-snapshot",
            entries: [entry("new", 1)],
            hasMore: true,
            nextCursor: "older",
            integrityValue: highwater,
          }),
        )
        .mockResolvedValueOnce(
          page({
            snapshot: "native-snapshot",
            entries: [entry("old", 0)],
            integrityValue: highwater,
          }),
        ),
    };
    render(
      <WorkspaceAuditPanel
        client={client}
        scopeKey="host-account/host-org/host-workspace"
      />,
    );
    await screen.findByText(/agent.deployed · new/);
    await userEvent.click(
      screen.getByRole("button", { name: "Load more records" }),
    );
    expect(await screen.findByText(/agent.deployed · old/)).toBeInTheDocument();
    expect(screen.getByText(/agent.deployed · new/)).toBeInTheDocument();
    expect(screen.queryByText(SCOPE.accountId)).not.toBeInTheDocument();
    expect(client.getTenantAudit).toHaveBeenLastCalledWith({
      limit: 50,
      cursor: "older",
    });
  });

  it("rejects another native owner on a later page without displaying that page", async () => {
    const highwater = integrity("native-snapshot", "verified", {
      length: 2,
      headHash: "hash-new",
    });
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValueOnce(
          page({
            snapshot: "native-snapshot",
            entries: [entry("new", 1)],
            hasMore: true,
            nextCursor: "older",
            integrityValue: highwater,
          }),
        )
        .mockResolvedValueOnce(
          page({
            scope: SCOPE_B,
            snapshot: "native-snapshot",
            entries: [entry("foreign", 0)],
            integrityValue: highwater,
          }),
        ),
    };
    render(<WorkspaceAuditPanel client={client} scopeKey="host-scope" />);
    await screen.findByText(/agent.deployed · new/);
    await userEvent.click(
      screen.getByRole("button", { name: "Load more records" }),
    );
    expect(await screen.findByText(/scope does not match/)).toBeInTheDocument();
    expect(
      screen.queryByText(/agent.deployed · foreign/),
    ).not.toBeInTheDocument();
  });

  it("renders corruption as broken and keeps exclusions visible on refusal", async () => {
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(
          page({
            snapshot: null,
            integrityValue: integrity(null, "broken", {
              length: null,
              headHash: null,
              firstBreak: { reason: "hash_mismatch", index: 0 },
            }),
          }),
        ),
    };
    const { rerender } = render(
      <WorkspaceAuditPanel client={client} scopeKey="host-scope" />,
    );
    expect(await screen.findByText("Broken")).toBeInTheDocument();
    expect(screen.getByText("Chain break: hash_mismatch")).toBeInTheDocument();
    const refused = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue({
          available: false,
          reason: "Current membership was revoked.",
        }),
    };
    rerender(<WorkspaceAuditPanel client={refused} scopeKey="host-scope" />);
    expect(
      await screen.findByText("Current membership was revoked."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Excluded permanently/)).toBeInTheDocument();
    expect(screen.queryByText("Broken")).not.toBeInTheDocument();
  });

  it("ignores an old verification result when the client and scope change", async () => {
    let resolveIntegrity;
    const client = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(page({ entries: [entry("first")] })),
      getTenantAuditIntegrity: vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveIntegrity = resolve;
          }),
      ),
    };
    const { rerender } = render(
      <WorkspaceAuditPanel client={client} scopeKey="host-scope-a" />,
    );
    await screen.findByText(/agent.deployed · first/);
    await userEvent.click(
      screen.getByRole("button", { name: "Verify this snapshot" }),
    );
    const next = {
      getTenantAudit: vi
        .fn()
        .mockResolvedValue(
          page({
            scope: SCOPE_B,
            snapshot: "empty",
            integrityValue: integrity("empty", "empty"),
          }),
        ),
    };
    rerender(<WorkspaceAuditPanel client={next} scopeKey="host-scope-b" />);
    expect(await screen.findByText("Empty chain")).toBeInTheDocument();
    await act(async () =>
      resolveIntegrity({
        available: true,
        ...integrity("snap-1", "broken", {
          firstBreak: { reason: "old-corruption" },
        }),
      }),
    );
    expect(screen.queryByText(/old-corruption/)).not.toBeInTheDocument();
    expect(screen.getByText("Empty chain")).toBeInTheDocument();
  });
});
