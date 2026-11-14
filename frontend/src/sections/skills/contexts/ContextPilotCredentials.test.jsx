import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  list: { data: { results: [] }, isLoading: false, isError: false },
  issue: { mutate: vi.fn(), isPending: false, isError: false, data: undefined },
  revoke: { mutate: vi.fn(), isPending: false, isError: false },
}));
vi.mock("src/api/skills", () => ({
  useContextCredentials: () => hooks.list,
  useIssueContextCredential: () => hooks.issue,
  useRevokeContextCredential: () => hooks.revoke,
}));

const { default: ContextPilotCredentials, pilotCredentialInput, grantsContext } =
  await import("./ContextPilotCredentials");

const CONTEXT = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const VERSION = "33333333-3333-4333-8333-333333333333";
const snapshot = { version: 1, versionId: VERSION, name: "pilot", digest: `sha256:${"a".repeat(64)}` };
const credential = (id, resourceId, extra = {}) => ({
  id, purpose: "consume", bindingIds: [], createdAt: "2026-11-10T00:00:00Z", lastUsedAt: null,
  expiresAt: "2099-01-01T00:00:00Z", revokedAt: null,
  grants: [{ kind: "agent_context", resourceId, labels: [], versionIds: [VERSION] }], ...extra,
});

afterEach(cleanup);
beforeEach(() => {
  hooks.list = { data: { results: [] }, isLoading: false, isError: false };
  hooks.issue = { mutate: vi.fn(), isPending: false, isError: false, data: undefined };
  hooks.revoke = { mutate: vi.fn(), isPending: false, isError: false };
});

describe("pilot credential input", () => {
  it("binds consume purpose to one exact version without moving labels", () => {
    const input = pilotCredentialInput({ contextId: CONTEXT, versionId: VERSION, days: 7, now: Date.UTC(2026, 10, 10) });
    expect(input).toEqual({
      purpose: "consume", bindingIds: [], expiresAt: "2026-11-17T00:00:00.000Z",
      grants: [{ kind: "agent_context", resourceId: CONTEXT, labels: [], versionIds: [VERSION] }],
    });
  });
  it("rejects an unbounded or unidentified grant", () => {
    expect(() => pilotCredentialInput({ contextId: CONTEXT, versionId: VERSION, days: 365 })).toThrow();
    expect(() => pilotCredentialInput({ contextId: CONTEXT, versionId: "", days: 7 })).toThrow();
  });
  it("lists only consume credentials granting this context", () => {
    expect(grantsContext(credential("a", CONTEXT), CONTEXT)).toBe(true);
    expect(grantsContext(credential("b", OTHER), CONTEXT)).toBe(false);
    expect(grantsContext({ ...credential("c", CONTEXT), purpose: "report" }, CONTEXT)).toBe(false);
  });
});

describe("ContextPilotCredentials", () => {
  it("issues with a stable idempotency key and never renders the secret", () => {
    const { rerender } = render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
    fireEvent.click(screen.getByRole("button", { name: "Create credential" }));
    expect(hooks.issue.mutate).toHaveBeenCalledTimes(1);
    const [input] = hooks.issue.mutate.mock.calls[0];
    expect(input.grants[0]).toEqual({ kind: "agent_context", resourceId: CONTEXT, labels: [], versionIds: [VERSION] });
    expect(input.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
    fireEvent.click(screen.getByRole("button", { name: "Create credential" }));
    expect(hooks.issue.mutate.mock.calls[1][0].idempotencyKey).toBe(input.idempotencyKey);
    hooks.issue = { ...hooks.issue, data: { id: CONTEXT, secret: "secret-value-not-shown", secretAvailable: true } };
    rerender(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
    expect(screen.getByRole("button", { name: "Copy credential secret" })).toBeInTheDocument();
    expect(screen.queryByText(/secret-value-not-shown/)).toBeNull();
  });
  it("explains a replay instead of offering an unavailable secret", () => {
    hooks.issue = { ...hooks.issue, data: { id: CONTEXT, secret: null, secretAvailable: false } };
    render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
    expect(screen.queryByRole("button", { name: "Copy credential secret" })).toBeNull();
    expect(screen.getByText(/secret is no longer available/)).toBeInTheDocument();
  });
  it("revokes only after explicit confirmation of the exact credential", () => {
    hooks.list = { data: { results: [credential(CONTEXT, CONTEXT), credential(OTHER, OTHER)] }, isLoading: false, isError: false };
    render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
    expect(screen.getAllByRole("row")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Actions for credential 11111111" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Revoke" }));
    expect(hooks.revoke.mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(hooks.revoke.mutate).toHaveBeenCalledWith({ credentialId: CONTEXT }, expect.any(Object));
  });
  it("shows revoked credentials as revoked and disables a second revoke", () => {
    hooks.list = { data: { results: [credential(CONTEXT, CONTEXT, { revokedAt: "2026-11-10T01:00:00Z" })] }, isLoading: false, isError: false };
    render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
    expect(screen.getByText("Revoked")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Actions for credential 11111111" }));
    expect(screen.getByRole("menuitem", { name: "Revoke" })).toHaveAttribute("aria-disabled", "true");
  });
  it("does not offer issuance without the admin permission", () => {
    render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue={false} />);
    expect(screen.queryByRole("button", { name: "Create credential" })).toBeNull();
  });
});

it('gives a non-admin plain access guidance without an inert action or claiming issuance', () => {
  render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue={false} />);
  expect(screen.getByText('Ask a workspace admin for access.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Ask a workspace admin' })).not.toBeInTheDocument();
  expect(hooks.issue.mutate).not.toHaveBeenCalled();
});
it('shows relative expiry with the exact UTC instant available on the time element', () => {
  const now = Date.UTC(2026, 10, 14);
  const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
  try {
    hooks.list.data.results = [credential(CONTEXT, CONTEXT, { expiresAt: '2026-11-21T00:00:00.000Z' })];
    render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
    const expiry = screen.getByText('Expires in 7 days');
    expect(expiry).toHaveAttribute('datetime', '2026-11-21T00:00:00.000Z');
    expect(expiry).toHaveAttribute('title', expect.stringContaining('UTC'));
  } finally { clock.mockRestore(); }
});
it('lists only credentials granting the viewed version', () => {
  hooks.list.data.results = [credential(CONTEXT, CONTEXT), credential(OTHER, CONTEXT, { grants: [{ kind: 'agent_context', resourceId: CONTEXT, labels: [], versionIds: [OTHER] }] })];
  render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue />);
  expect(screen.getAllByRole('row')).toHaveLength(2);
  expect(screen.queryByText('22222222')).not.toBeInTheDocument();
});

it('makes key creation primary only when no active key exists for this exact version', () => {
  const available = vi.fn();
  const view = render(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue onAvailabilityChange={available} />);
  expect(screen.getByRole('button', { name: 'Create credential' })).toHaveClass('MuiButton-contained');
  expect(available).toHaveBeenLastCalledWith(false);
  hooks.list.data.results = [credential(CONTEXT, CONTEXT)];
  view.rerender(<ContextPilotCredentials contextId={CONTEXT} snapshot={snapshot} canIssue onAvailabilityChange={available} />);
  expect(screen.getByRole('button', { name: 'Create credential' })).toHaveClass('MuiButton-outlined');
  expect(available).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole('columnheader', { name: 'Key' })).toBeVisible();
  expect(screen.getByRole('columnheader', { name: 'Version' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy full Key' })).toBeVisible();
});
