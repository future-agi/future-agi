import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const h = vi.hoisted(() => ({ edition: null }));

vi.mock("src/hooks/useEdition", () => ({
  EDITION_QUERY_KEY: ["edition"],
  useEdition: () => ({ data: h.edition, isLoading: false, isError: false }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITIES_QUERY_KEY: ["capabilities"],
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const { default: LicensePage } = await import("../LicensePage");

const COMMUNITY = {
  edition: "community",
  deployment: "self_hosted",
  limits: {
    organizations: { limit: 1, current: 1 },
    workspaces: { limit: 1, current: 1 },
    members: { limit: 3, current: 2 },
  },
  over_limit: false,
  enterprise_features: [
    "falcon_ai",
    "turing_models",
    "protect",
    "error_feed",
    "members",
    "organizations",
    "workspaces",
  ],
  contact: "sales@futureagi.com",
  activation: { method: "env_restart" },
  license: {
    state: "missing",
    license_type: null,
    issued_to: null,
    expires_at: null,
    grace_ends_at: null,
    license_id_masked: null,
    key_fingerprint: null,
  },
};

const ENTERPRISE = {
  ...COMMUNITY,
  edition: "enterprise",
  limits: {
    organizations: { limit: null, current: 2 },
    workspaces: { limit: null, current: 4 },
    members: { limit: null, current: 9 },
  },
  license: {
    state: "active",
    license_type: "production",
    issued_to: "Acme Corp",
    expires_at: "2027-10-06T00:00:00+00:00",
    grace_ends_at: null,
    license_id_masked: "lic_****9f2a",
    key_fingerprint: "3e1b07c4",
  },
};

function renderPage() {
  const queryClient = new QueryClient();
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return render(<LicensePage />, { wrapper: Wrapper });
}

beforeEach(() => {
  h.edition = null;
});

describe("Plan & License (TH-8084 AC-01, AC-02)", () => {
  it("shows Community · Self-hosted with its allowance and usage", () => {
    h.edition = COMMUNITY;
    renderPage();
    expect(screen.getByText("Plan & License")).toBeInTheDocument();
    expect(screen.getByText("Community · Self-hosted")).toBeInTheDocument();
    expect(
      screen.getByText("1 organization · 1 workspace · up to 3 members"),
    ).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    expect(
      screen.getByText("All other products, with no usage caps."),
    ).toBeInTheDocument();
    for (const addition of [
      "Falcon AI",
      "Turing Models",
      "Protect",
      "Error Feed",
    ]) {
      expect(screen.getByText(addition)).toBeInTheDocument();
    }
    expect(screen.queryByText("No license found")).not.toBeInTheDocument();
  });

  it("offers Contact sales (mailto) and the activation steps", () => {
    h.edition = COMMUNITY;
    renderPage();
    expect(
      screen.getByRole("link", { name: /contact sales/i }),
    ).toHaveAttribute("href", "mailto:sales@futureagi.com");
    fireEvent.click(screen.getByRole("button", { name: /activate license/i }));
    expect(
      screen.getByText(/Set EE_LICENSE_KEY on every backend/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Finish restarting every service/),
    ).toBeInTheDocument();
  });

  it("flags an over-limit install without hiding anything (AC-11)", () => {
    h.edition = {
      ...COMMUNITY,
      over_limit: true,
      limits: { ...COMMUNITY.limits, workspaces: { limit: 1, current: 2 } },
    };
    renderPage();
    expect(
      screen.getByText(
        /This install has more workspaces than Community includes\. Everything you have stays/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("2 / 1")).toBeInTheDocument();
  });

  it("shows an expired licence as Community with its state (AC-17)", () => {
    h.edition = {
      ...COMMUNITY,
      license: { ...COMMUNITY.license, state: "expired" },
    };
    renderPage();
    expect(screen.getByText("Community · Self-hosted")).toBeInTheDocument();
    expect(screen.getByText("expired")).toBeInTheDocument();
    expect(screen.getByText(/Your license has expired/)).toBeInTheDocument();
  });

  it("shows Enterprise · Self-hosted with masked licence details (AC-16)", () => {
    h.edition = ENTERPRISE;
    renderPage();
    expect(screen.getByText("Enterprise · Self-hosted")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("lic_****9f2a")).toBeInTheDocument();
    expect(screen.getByText("3e1b07c4")).toBeInTheDocument();
    expect(
      screen.queryByText("1 organization · 1 workspace · up to 3 members"),
    ).not.toBeInTheDocument();
  });

  it("shows the grace period end date", () => {
    h.edition = {
      ...ENTERPRISE,
      license: {
        ...ENTERPRISE.license,
        state: "grace",
        grace_ends_at: "2027-12-01T00:00:00+00:00",
      },
    };
    renderPage();
    expect(screen.getByText(/grace period until/)).toBeInTheDocument();
  });

  it("never shows Cloud Free/PAYG cards or quota bars (AC-02)", () => {
    h.edition = COMMUNITY;
    renderPage();
    const text = document.body.textContent;
    for (const cloudCopy of [
      "Pay-as-you-go",
      "Free plan",
      "Upgrade",
      "quota",
      "usage limit",
    ]) {
      expect(text).not.toContain(cloudCopy);
    }
  });
});
