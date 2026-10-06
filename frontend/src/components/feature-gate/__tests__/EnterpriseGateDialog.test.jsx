import { describe, it, expect, vi, beforeEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({ push: vi.fn(), authenticated: true }));

vi.mock("src/routes/hooks", () => ({ useRouter: () => ({ push: h.push }) }));
vi.mock("src/routes/paths", () => ({
  paths: {
    dashboard: { settings: { eeLicenses: "/dashboard/settings/ee-licenses" } },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ authenticated: h.authenticated }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const { default: EnterpriseGateDialog } =
  await import("../EnterpriseGateDialog");
const { default: EnterpriseGateHost } = await import("../EnterpriseGateHost");
const gateModule = await import("../enterprise-gate");

const memberGate = {
  feature: "members",
  edition: "community",
  limit: 3,
  current: 3,
  requested: 1,
  license_state: "missing",
  contact: "sales@futureagi.com",
  activation_route: "/dashboard/settings/ee-licenses",
};

beforeEach(() => {
  h.push.mockReset();
  h.authenticated = true;
});

describe("EnterpriseGateDialog (TH-8084 AC-08, AC-20)", () => {
  it("presents the Community allowance as an Enterprise feature", () => {
    render(<EnterpriseGateDialog open gate={memberGate} onClose={() => {}} />);
    expect(
      screen.getByText("Add more members with Enterprise"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Community includes up to 3 organization members. More members are an Enterprise feature.",
      ),
    ).toBeInTheDocument();
    const text = document.body.textContent.toLowerCase();
    for (const banned of [
      "usage limit",
      "quota",
      "rate limit",
      "upgrade your plan",
      "pay-as-you-go",
    ]) {
      expect(text).not.toContain(banned);
    }
  });

  it("Contact sales is a mailto to sales@futureagi.com", () => {
    render(<EnterpriseGateDialog open gate={memberGate} onClose={() => {}} />);
    const contact = screen.getByRole("link", { name: /contact sales/i });
    expect(contact).toHaveAttribute("href", "mailto:sales@futureagi.com");
  });

  it("Activate license closes the dialog and opens Plan & License", () => {
    const onClose = vi.fn();
    render(<EnterpriseGateDialog open gate={memberGate} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /activate license/i }));
    expect(onClose).toHaveBeenCalled();
    expect(h.push).toHaveBeenCalledWith("/dashboard/settings/ee-licenses");
  });

  it.each([
    [
      "organizations",
      "Create more organizations with Enterprise",
      "Community includes one organization.",
    ],
    [
      "workspaces",
      "Create more workspaces with Enterprise",
      "Community includes one workspace.",
    ],
  ])("names the %s allowance", (feature, title, allowance) => {
    render(
      <EnterpriseGateDialog
        open
        gate={{ ...memberGate, feature, limit: 1 }}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(allowance))).toBeInTheDocument();
  });
});

describe("EnterpriseGateHost", () => {
  it("opens the dialog when axios dispatches an edition gate", () => {
    render(<EnterpriseGateHost />);
    expect(
      screen.queryByText("Add more members with Enterprise"),
    ).not.toBeInTheDocument();
    act(() => gateModule.dispatchEnterpriseGate(memberGate));
    expect(
      screen.getByText("Add more members with Enterprise"),
    ).toBeInTheDocument();
  });

  it("tells someone signing up to ask for an invite", () => {
    h.authenticated = false;
    render(<EnterpriseGateHost />);
    act(() =>
      gateModule.dispatchEnterpriseGate({
        ...memberGate,
        feature: "organizations",
        limit: 1,
      }),
    );
    expect(screen.getByText(/ask an admin to invite you/i)).toBeInTheDocument();
  });
});

describe("enterprise-gate helpers", () => {
  it("treats only members, organizations and workspaces as edition gates", () => {
    expect(gateModule.isEditionGate(memberGate)).toBe(true);
    expect(gateModule.isEditionGate({ feature: "workspaces" })).toBe(true);
    expect(gateModule.isEditionGate({ feature: "falcon_ai" })).toBe(false);
    expect(gateModule.isEditionGate(undefined)).toBe(false);
  });
});

describe("copy names only sales@futureagi.com (AC-20)", () => {
  const files = [
    "src/components/feature-gate/enterprise-gate.js",
    "src/components/feature-gate/EnterpriseGateDialog.jsx",
    "src/components/feature-gate/EnterpriseGateHost.jsx",
    "src/sections/settings/License/LicensePage.jsx",
    "src/components/oss-upgrade-gate/constants.js",
  ];

  it.each(files)("%s mentions no other email address", (file) => {
    const source = fs.readFileSync(path.resolve(process.cwd(), file), "utf8");
    const emails =
      source.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || [];
    expect(emails.filter((email) => email !== "sales@futureagi.com")).toEqual(
      [],
    );
  });

  it("the Enterprise gate contact is the sales mailto (review C5)", async () => {
    const { CONTACT_URL } =
      await import("src/components/oss-upgrade-gate/constants");
    expect(CONTACT_URL).toBe("mailto:sales@futureagi.com");
  });
});
