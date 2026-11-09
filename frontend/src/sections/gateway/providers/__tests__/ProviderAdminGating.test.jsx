import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HelmetProvider } from "react-helmet-async";
import { render, screen } from "src/utils/test-utils";

import ProviderManagementSection from "../ProviderManagementSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../../testing/adminOnly";

// The gateway admin role check on the Providers page: only
// an organization Owner or Admin may add, edit or remove a provider or
// reload the gateway's config, so each of those controls shows disabled with
// "Owner or Admin only" for a Member, a Viewer or a user whose level is not
// loaded, as on Gateway settings. Every read stays open. Edit and Remove are
// checked where they are: on each row of the list and in the provider's
// detail drawer.

let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({}),
  useNavigate: () => vi.fn(),
}));

vi.mock("ag-grid-react", async () => await import("./grid-mock"));

const mutation = () => ({
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
});

const PROVIDERS = {
  openai: { base_url: "https://api.openai.com/v1", models: ["gpt-4o"] },
};

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("../hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { providers: PROVIDERS },
    isLoading: false,
  }),
  useProviderHealth: () => ({ data: { providers: [] }, isLoading: false }),
  useReloadConfig: () => mutation(),
  useRemoveProvider: () => mutation(),
  useUpdateConfig: () => mutation(),
  isSavedButNotApplied: () => false,
  refreshGatewayConfig: vi.fn(),
}));

vi.mock("../hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null, isLoading: false }),
  useCreateOrgConfig: () => mutation(),
}));

vi.mock("../AddProviderDialog", () => ({ default: () => null }));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ credits: null, creditsAvailable: false }),
}));

// The page frame sets the document title.
const renderPage = () =>
  render(
    <HelmetProvider>
      <ProviderManagementSection />
    </HelmetProvider>,
  );

beforeEach(() => {
  mockOrgLevel = 8;
});

describe("Providers", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Add provider and Reload config disabled, with the role they need",
    (_who, level) => {
      mockOrgLevel = level;
      renderPage();
      expect(screen.getByText("api.openai.com/v1")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Add provider" }));
      expectAdminOnly(screen.getByRole("button", { name: "Reload config" }));
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can add a provider and reload the config",
    (_who, level) => {
      mockOrgLevel = level;
      renderPage();
      expectEditable(screen.getByRole("button", { name: "Add provider" }));
      expectEditable(screen.getByRole("button", { name: "Reload config" }));
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads a provider with Edit and Remove disabled",
    (_who, level) => {
      mockOrgLevel = level;
      renderPage();
      expect(screen.getByText("api.openai.com/v1")).toBeInTheDocument();
      expectAdminOnly(
        screen.getByRole("button", { name: "Edit provider openai" }),
      );
      expectAdminOnly(
        screen.getByRole("button", { name: "Remove provider openai" }),
      );
    },
  );

  it.each(ADMIN_LEVELS)("%s can edit and remove a provider", (_who, level) => {
    mockOrgLevel = level;
    renderPage();
    expectEditable(
      screen.getByRole("button", { name: "Edit provider openai" }),
    );
    expectEditable(
      screen.getByRole("button", { name: "Remove provider openai" }),
    );
  });
});
