import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useQueryClient } from "@tanstack/react-query";
import { screen, userEvent, waitFor, within } from "src/utils/test-utils";
import { renderPage } from "../../guardrails/__tests__/helpers";
import KeyManagementSection from "../KeyManagementSection";
import { ROTATE_REVOCATION_COPY } from "../rotate";
import approved from "./rotation-copy.activation.json";
import { enqueueSnackbar } from "notistack";

const fixture = vi.hoisted(() => ({
  post: vi.fn(),
  patch: vi.fn(),
  reply: null,
  failRevoke: false,
  key: {
    id: "old-row",
    name: "Rotation fixture",
    user: "actor",
    organization: "org-a",
    status: "active",
    allowedModels: [],
    allowedProviders: [],
  },
}));
const SECRET_FIXTURE = "rotation-reveal-fixture-only";
const PENDING_WARNING =
  "This key may still work. Revocation has not been confirmed at the gateway.";

vi.mock("src/utils/axios", () => ({
  default: { post: fixture.post, patch: fixture.patch },
  endpoints: {
    gateway: {
      createApiKey: "/fixture/keys/",
      updateApiKey: (id) => `/fixture/keys/${id}/`,
      revokeApiKey: (id) => `/fixture/keys/${id}/revoke/`,
    },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: 8,
    currentOrganizationId: "org-a",
    isReady: true,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "actor" } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("../../providers/ui/CreditBalanceChip", () => ({ default: () => null }));
vi.mock("../CreateKeyDialog", () => ({ default: () => null }));
vi.mock("../EditKeyDialog", () => ({ default: () => null }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "fixture-gateway" }),
}));
vi.mock("../hooks/useApiKeys", async (importOriginal) => ({
  ...(await importOriginal()),
  // Keep the real rotation hook, composition and React Query mutation cache.
  useApiKeys: () => ({ data: [fixture.key], isLoading: false, isError: false }),
  useApiKeyDetail: () => ({ data: fixture.key, isLoading: false }),
  useRevokeApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  fixture.failRevoke = false;
  fixture.reply = { status: "revoked", gateway_revocation_confirmed: true };
  fixture.post.mockImplementation(async (url) => {
    if (url === "/fixture/keys/") {
      return {
        data: {
          result: {
            id: "new-row", name: fixture.key.name, key: SECRET_FIXTURE, status: "active",
            rotation: {
              expiry_copied: true,
              ...(fixture.failRevoke ? { status: "active" } : fixture.reply),
            },
          },
        },
      };
    }
    throw new Error("Unexpected fixture request");
  });
});

function renderWithCache() {
  let client;
  function Page() {
    client = useQueryClient();
    return <KeyManagementSection />;
  }
  const view = renderPage(<Page />);
  return { ...view, client };
}

async function rotate(user) {
  await user.click(screen.getByRole("button", { name: "Actions for Rotation fixture" }));
  await user.click(screen.getByRole("menuitem", { name: "Rotate" }));
  const confirm = await screen.findByRole("dialog", { name: "Rotate Rotation fixture?" });
  await user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
  return screen.findByRole("dialog", { name: "Copy your new key" });
}

describe("rotation enforcement notes", () => {
  it("separates local revocation from explicit gateway confirmation", async () => {
    const user = userEvent.setup();
    renderWithCache();
    const reveal = await rotate(user);
    expect(within(reveal).getByText(ROTATE_REVOCATION_COPY.local(fixture.key.name))).toBeVisible();
    expect(within(reveal).getByText(ROTATE_REVOCATION_COPY.confirmed)).toBeVisible();
    expect(within(reveal).queryByText(/401/)).toBeNull();
    expect(within(reveal).getByText(ROTATE_REVOCATION_COPY.confirmed).textContent).toBe(approved.rows["KEYS-ROTATE-12"].renderedExact);
    expect(within(reveal).getByText(ROTATE_REVOCATION_COPY.local(fixture.key.name)).textContent).toBe(approved.rows["KEYS-ROTATE-11"].renderedExact.replace("${name}", fixture.key.name));
    expect(within(reveal).queryByText(ROTATE_REVOCATION_COPY.pending)).toBeNull();
  });

  it.each([
    ["pending gateway push", { status: "revoked", gateway_revocation_confirmed: false, warning: PENDING_WARNING }],
    ["unconfirmed without warning", { status: "revoked", gateway_revocation_confirmed: false }],
    ["legacy response without confirmation", { status: "revoked" }],
    ["contradictory warning", { status: "revoked", gateway_revocation_confirmed: true, warning: PENDING_WARNING }],
  ])("does not claim 401s for %s", async (_, reply) => {
    fixture.reply = reply;
    const user = userEvent.setup();
    renderWithCache();
    const reveal = await rotate(user);
    expect(within(reveal).getByText(SECRET_FIXTURE)).toBeVisible();
    expect(within(reveal).getByText(ROTATE_REVOCATION_COPY.local(fixture.key.name))).toBeVisible();
    expect(within(reveal).getByText(ROTATE_REVOCATION_COPY.pending).textContent).toBe(approved.rows["KEYS-ROTATE-13"].renderedExact);
    expect(within(reveal).queryByText(/401/)).toBeNull();
    expect(within(reveal).queryByText(ROTATE_REVOCATION_COPY.confirmed)).toBeNull();
    if (reply.warning) expect(within(reveal).getByText(PENDING_WARNING)).toBeVisible();
  });

  it("keeps the replacement visible without a containment claim when a reply does not confirm local revocation", async () => {
    fixture.failRevoke = true;
    const user = userEvent.setup();
    renderWithCache();
    const reveal = await rotate(user);
    expect(within(reveal).getByText(SECRET_FIXTURE)).toBeVisible();
    expect(within(reveal).getByText(/Revocation of the old key could not be confirmed/i)).toBeVisible();
    expect(within(reveal).queryByText(/401/)).toBeNull();
    expect(within(reveal).queryByText(ROTATE_REVOCATION_COPY.confirmed)).toBeNull();
    expect(within(reveal).queryByText(ROTATE_REVOCATION_COPY.local(fixture.key.name))).toBeNull();
  });
});

describe("show-once rotation mutation cache", () => {
  it.each([false, true])("Done removes both secret copies immediately; pending=%s", async (pending) => {
    if (pending) fixture.reply = {
      status: "revoked",
      gateway_revocation_confirmed: false,
      warning: PENDING_WARNING,
    };
    const user = userEvent.setup();
    const { client } = renderWithCache();
    const cache = client.getMutationCache();
    const unrelated = cache.build(client, { gcTime: Infinity, mutationFn: vi.fn() });
    const reveal = await rotate(user);
    const cached = cache.getAll().find((entry) => entry.state.data?.secret === SECRET_FIXTURE);
    expect(cached).toBeDefined();
    expect(cached.state.data.created.key).toBe(SECRET_FIXTURE);
    await user.click(within(reveal).getByRole("button", { name: "Done" }));
    // Do not wait for GC: dismissal itself removes this exact cache entry.
    expect(cache.getAll()).not.toContain(cached);
    expect(cache.getAll()).toContain(unrelated);
    expect(cache.getAll().some((entry) =>
      entry.state.data?.secret === SECRET_FIXTURE || entry.state.data?.created?.key === SECRET_FIXTURE,
    )).toBe(false);
    await waitFor(() => expect(screen.queryByText(SECRET_FIXTURE)).toBeNull());
    cache.remove(unrelated);
  });

  it("collects an unobserved rotation reply when the page unmounts", async () => {
    const user = userEvent.setup();
    const { client, unmount } = renderWithCache();
    await rotate(user);
    expect(client.getMutationCache().getAll().some((entry) => entry.state.data?.secret)).toBe(true);
    unmount();
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
  });
});

it("remote provisioning failure shows activated failure copy and no secret", async () => {
  fixture.post.mockRejectedValueOnce({ response: { data: { code: "KEY_PROVISIONING_FAILED" } } });
  const user = userEvent.setup();
  const { client } = renderWithCache();
  await user.click(screen.getByRole("button", { name: "Actions for Rotation fixture" }));
  await user.click(screen.getByRole("menuitem", { name: "Rotate" }));
  const confirm = await screen.findByRole("dialog", { name: "Rotate Rotation fixture?" });
  await user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
  await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledWith(
    'Rotation could not be confirmed: the new key could not be retrieved. Check the key list before retrying.',
    expect.objectContaining({ variant: "error" }),
  ));
  expect(screen.queryByRole("dialog", { name: "Copy your new key" })).toBeNull();
  expect(client.getMutationCache().getAll().some((entry) => entry.state.data?.secret)).toBe(false);
});
