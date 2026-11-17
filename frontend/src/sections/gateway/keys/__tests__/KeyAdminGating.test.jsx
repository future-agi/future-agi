import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HelmetProvider } from "react-helmet-async";
import { render, screen } from "src/utils/test-utils";

import KeyManagementSection from "../KeyManagementSection";
import KeyDetailDrawer from "../KeyDetailDrawer";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../../testing/adminOnly";

// Admin controls stay gated; active members may create personal keys.
// A peer key never offers Rotate, regardless of the actor's role.
// Readers keep Edit/Revoke disabled and View logs open.

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "signed-in-user" } }),
}));
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: mockOrgLevel,
    currentOrganizationId: "org-a",
    isReady: true,
  }),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
}));

vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);

const mutation = () => ({
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
});

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

const KEY = {
  id: "key-1",
  user: "peer-user",
  organization: "org-a",
  name: "prod key",
  status: "active",
  keyPrefix: "sk-agcc-ab",
  gatewayKeyId: "gw-key-1",
  allowedModels: [],
  allowedProviders: [],
};
let mockKeys = [KEY];

vi.mock("../hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: mockKeys, isLoading: false, error: null }),
  useSyncApiKeys: () => mutation(),
  useApiKeyDetail: () => ({ data: KEY, isLoading: false }),
  useRevokeApiKey: () => mutation(),
  useRotateApiKey: () => mutation(),
  useUpdateApiKey: () => mutation(),
  useCreateApiKey: () => mutation(),
}));
vi.mock("../CreateKeyDialog", () => ({ default: () => null }));
vi.mock("../EditKeyDialog", () => ({ default: () => null }));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ credits: null, creditsAvailable: false }),
}));

const renderPage = () =>
  render(
    <HelmetProvider>
      <KeyManagementSection />
    </HelmetProvider>,
  );

const renderDrawer = () =>
  render(
    <KeyDetailDrawer keyId="key-1" open onClose={vi.fn()} gatewayId="gw" />,
  );

beforeEach(() => {
  mockOrgLevel = 8;
  mockKeys = [KEY];
});

describe("API keys", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the keys with Sync, Create key and the row actions disabled",
    (_who, level) => {
      mockOrgLevel = level;
      renderPage();
      expect(screen.getByText("prod key")).toBeInTheDocument();
      expectAdminOnly(
        screen.getByRole("button", { name: "Sync from gateway" }),
      );
      expect(screen.getByRole("button", { name: "Create key" })).toHaveProperty(
        "disabled",
        !(typeof level === "number" && level >= 1),
      );
      expectAdminOnly(
        screen.getByRole("button", { name: "Actions for prod key" }),
      );
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Create key disabled on an empty list",
    (_who, level) => {
      mockOrgLevel = level;
      mockKeys = [];
      renderPage();
      screen
        .getAllByRole("button", { name: "Create key" })
        .forEach((button) => {
          expect(button).toHaveProperty(
            "disabled",
            !(typeof level === "number" && level >= 1),
          );
        });
    },
  );

  it.each(ADMIN_LEVELS)("%s can sync and create keys", (_who, level) => {
    mockOrgLevel = level;
    renderPage();
    expectEditable(screen.getByRole("button", { name: "Sync from gateway" }));
    expectEditable(screen.getByRole("button", { name: "Create key" }));
    expectEditable(
      screen.getByRole("button", { name: "Actions for prod key" }),
    );
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads a peer key with Rotate hidden, Edit/Revoke disabled and View logs open",
    (_who, level) => {
      mockOrgLevel = level;
      renderDrawer();
      expect(screen.getAllByText("prod key").length).toBeGreaterThan(0);
      expectAdminOnly(screen.getByRole("button", { name: "Edit" }));
      expect(screen.queryByRole("button", { name: "Rotate" })).toBeNull();
      expectAdminOnly(screen.getByRole("button", { name: "Revoke" }));
      expect(screen.getByRole("link", { name: /View logs/ })).toBeEnabled();
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can edit and revoke another person's key, but cannot rotate it",
    (_who, level) => {
      mockOrgLevel = level;
      renderDrawer();
      expectEditable(screen.getByRole("button", { name: "Edit" }));
      expect(screen.queryByRole("button", { name: "Rotate" })).toBeNull();
      expectEditable(screen.getByRole("button", { name: "Revoke" }));
    },
  );
});
