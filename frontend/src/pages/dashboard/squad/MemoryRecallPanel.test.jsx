import { describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import MemoryRecallPanel from "./MemoryRecallPanel";

const SCOPE_A = "org/native-a/workspaces/ws-a/memory";
const SCOPE_B = "org/native-a/workspaces/ws-b/memory";
const scopes = {
  available: true,
  schemaVersion: "omega-os-memory/v1",
  items: [
    { path: SCOPE_A, kind: "workspace", memoryCount: 4, grantCount: 1 },
    { path: SCOPE_B, kind: "workspace", memoryCount: 1, grantCount: 1 },
  ],
};
function pack(overrides = {}) {
  return {
    available: true,
    schemaVersion: "omega-os-memory/v1",
    recall: {
      principal: "native-account-a",
      scope: SCOPE_A,
      access: { granted: true },
      protocol: "omega-memory-pack/v1",
      packedAt: "2026-10-13T12:00:00.000Z",
      tokenBudget: 2000,
      tokensUsed: 12,
      items: [
        {
          rank: 0,
          contentPreview: "Always cite the supplied evidence.",
          ageMs: 1000,
          citation: {
            eventId: "memory-1",
            tier: "verified",
            producedBy: "analyst",
            at: "2026-10-13T11:59:59.000Z",
            sourceRefs: ["source:run"],
          },
        },
      ],
      dropped: [
        {
          eventId: "memory-old",
          reason: "stale",
          contentPreview: "Older context",
        },
      ],
      droppedTotal: 1,
      ...overrides,
    },
  };
}
const preview = () =>
  userEvent.click(
    screen.getByRole("button", { name: "Preview context", exact: true }),
  );

// The examples intentionally use server/native paths different from FutureAGI tenant IDs. No principal
// or host-to-native identity construction belongs in this UI request.
describe("memory context preview", () => {
  it("sends only a server-returned scope, budget and dropped limit, then shows provenance and packing results", async () => {
    const client = { getMemoryRecall: vi.fn().mockResolvedValue(pack()) };
    render(<MemoryRecallPanel client={client} scopesResult={scopes} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /ws-a.*4 memories/ }),
    ).toHaveValue(SCOPE_A);
    expect(client.getMemoryRecall).not.toHaveBeenCalled();
    await preview();
    expect(client.getMemoryRecall).toHaveBeenCalledWith({
      scope: SCOPE_A,
      tokenBudget: 2000,
      limit: 50,
    });
    expect(
      await screen.findByText("Always cite the supplied evidence."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Access granted/)).toBeInTheDocument();
    expect(screen.getByText(/12 \/ 2000 tokens/)).toBeInTheDocument();
    expect(
      screen.getByText(/Tier: verified · By: analyst · At:/),
    ).toHaveTextContent("2026-10-13T11:59:59.000Z");
    expect(screen.getByText("Citation: memory-1")).toBeInTheDocument();
    expect(screen.getByText("stale: 1")).toBeInTheDocument();
    expect(
      screen.getByText(
        /not semantic search or a guarantee of exact model context/,
      ),
    ).toBeInTheDocument();
  });

  it("shows an ACL denial without rendering any returned memory or dropped content", async () => {
    const client = {
      getMemoryRecall: vi.fn().mockResolvedValue(
        pack({
          access: { granted: false, reason: "Read grant missing" },
          tokensUsed: 0,
        }),
      ),
    };
    render(<MemoryRecallPanel client={client} scopesResult={scopes} />);
    await preview();
    expect(
      await screen.findByText("Access denied: Read grant missing"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Always cite the supplied evidence."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Older context")).not.toBeInTheDocument();
  });

  it("withholds redacted and tombstoned content even if a faulty response includes it, and labels truncation", async () => {
    const original = pack().recall.items[0];
    const client = {
      getMemoryRecall: vi.fn().mockResolvedValue(
        pack({
          items: [
            {
              ...original,
              contentPreview: "secret-redacted-packed",
              citation: { ...original.citation, redacted: true },
            },
          ],
          dropped: [
            {
              eventId: "redacted",
              reason: "redacted",
              contentPreview: "secret-redacted-drop",
            },
            {
              eventId: "tombstone",
              reason: "tombstoned",
              contentPreview: "secret-tombstone-drop",
            },
            {
              eventId: "budget",
              reason: "budget",
              contentPreview: "Budget-excluded context",
            },
          ],
          droppedTotal: 8,
        }),
      ),
    };
    render(<MemoryRecallPanel client={client} scopesResult={scopes} />);
    await preview();
    expect(
      await screen.findByText(/Showing 3 of 8 dropped candidates/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/secret-/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Content withheld.")).toHaveLength(3);
    expect(screen.getByText("Budget-excluded context")).toBeInTheDocument();
    expect(screen.getByText("redacted: 1")).toBeInTheDocument();
    expect(screen.getByText("tombstoned: 1")).toBeInTheDocument();
    expect(screen.getByText("budget: 1")).toBeInTheDocument();
  });

  it("clears a loaded pack before changing scope or budget and drops obsolete responses", async () => {
    let resolve;
    const client = {
      getMemoryRecall: vi
        .fn()
        .mockResolvedValueOnce(pack())
        .mockImplementationOnce(
          () =>
            new Promise((done) => {
              resolve = done;
            }),
        )
        .mockResolvedValue(
          pack({
            scope: SCOPE_B,
            tokenBudget: 900,
            tokensUsed: 0,
            items: [],
            dropped: [],
            droppedTotal: 0,
          }),
        ),
    };
    render(<MemoryRecallPanel client={client} scopesResult={scopes} />);
    await preview();
    await screen.findByText("Always cite the supplied evidence.");
    await preview();
    expect(
      screen.queryByText("Always cite the supplied evidence."),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Memory scope" }),
      SCOPE_B,
    );
    await userEvent.clear(
      screen.getByRole("spinbutton", { name: "Token budget" }),
    );
    await userEvent.type(
      screen.getByRole("spinbutton", { name: "Token budget" }),
      "900",
    );
    await act(async () => resolve(pack()));
    expect(
      screen.queryByText("Always cite the supplied evidence."),
    ).not.toBeInTheDocument();
    await preview();
    expect(
      await screen.findByText("No memories fit this context."),
    ).toBeInTheDocument();
    expect(client.getMemoryRecall).toHaveBeenLastCalledWith({
      scope: SCOPE_B,
      tokenBudget: 900,
      limit: 50,
    });
  });

  it("removes old tenant results on a client change, scope failure and missing scope list", async () => {
    let resolve;
    const client = {
      getMemoryRecall: vi
        .fn()
        .mockResolvedValueOnce(pack())
        .mockImplementationOnce(
          () =>
            new Promise((done) => {
              resolve = done;
            }),
        ),
    };
    const view = render(
      <MemoryRecallPanel client={client} scopesResult={scopes} />,
    );
    await preview();
    await screen.findByText("Always cite the supplied evidence.");
    await preview();
    const other = {
      getMemoryRecall: vi.fn().mockResolvedValue({
        available: false,
        reason: "Tenant access denied",
        status: 403,
      }),
    };
    view.rerender(<MemoryRecallPanel client={other} scopesResult={scopes} />);
    await act(async () => resolve(pack()));
    expect(
      screen.queryByText("Always cite the supplied evidence."),
    ).not.toBeInTheDocument();
    await preview();
    expect(await screen.findByText("Tenant access denied")).toBeInTheDocument();
    view.rerender(
      <MemoryRecallPanel
        client={other}
        scopesResult={{ available: false, reason: "Scopes denied" }}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Preview context" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("region", { name: "Memory context result" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Scopes denied")).toBeInTheDocument();
  });

  it.each(["0", "8193", "1.5", ""])(
    "bounds the integer token budget %s without a request",
    async (value) => {
      const client = { getMemoryRecall: vi.fn() };
      render(<MemoryRecallPanel client={client} scopesResult={scopes} />);
      const budget = screen.getByRole("spinbutton", { name: "Token budget" });
      await userEvent.clear(budget);
      if (value) await userEvent.type(budget, value);
      expect(
        screen.getByRole("button", { name: "Preview context" }),
      ).toBeDisabled();
      expect(client.getMemoryRecall).not.toHaveBeenCalled();
    },
  );

  it("honors a lower configured server budget cap and never invents a scope from labels or IDs", async () => {
    const client = {
      getMemoryRecall: vi
        .fn()
        .mockResolvedValue(
          pack({ tokenBudget: 400, tokensUsed: 0, items: [] }),
        ),
    };
    render(
      <MemoryRecallPanel
        client={client}
        maxTokenBudget={400}
        scopesResult={{
          ...scopes,
          items: [
            { id: "client-made-id", name: "Wrong" },
            scopes.items[0],
            scopes.items[0],
          ],
        }}
      />,
    );
    expect(
      screen.getByRole("spinbutton", { name: "Token budget" }),
    ).toHaveValue(400);
    expect(
      screen.queryByRole("option", { name: "Wrong" }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(1);
    await preview();
    await waitFor(() =>
      expect(client.getMemoryRecall).toHaveBeenCalledWith({
        scope: SCOPE_A,
        tokenBudget: 400,
        limit: 50,
      }),
    );
  });

  it.each([
    { scope: "foreign-scope" },
    { tokenBudget: 2001 },
    { tokensUsed: 2001 },
    { access: {} },
    { items: [null] },
    { droppedTotal: 0 },
  ])(
    "fails closed for malformed or mismatched recall projection %#",
    async (override) => {
      const client = {
        getMemoryRecall: vi.fn().mockResolvedValue(pack(override)),
      };
      render(<MemoryRecallPanel client={client} scopesResult={scopes} />);
      await preview();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "does not match this request",
      );
      expect(
        screen.queryByText("Always cite the supplied evidence."),
      ).not.toBeInTheDocument();
    },
  );
});
