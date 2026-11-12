import React from "react";
import Typography from "@mui/material/Typography";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import { faTestTheme } from "../../__tests__/faTheme";
import BudgetsConfigTab from "../../settings/BudgetsConfigTab";
import AdmissionCapEditor from "./AdmissionCapEditor";
import AdmissionPolicySummary from "./AdmissionPolicySummary";

// No organization selected: the panel shows its heading and caption only, so
// nothing is read from the server.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: null }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));

const AUTHOR = "3f2c9a4e-7b1d-4c8e-9a05-6d2e1f0b7c3a";
const cap = (limit) => ({
  limit,
  period: "daily",
  activated_at: "2026-10-27T12:00:00Z",
});

// Agent caps on with no key bound to an agent, a key cap and a per-run
// ceiling: every block of the summary's copy is shown.
const ACTIVE = {
  organization: "org-a",
  version: 4,
  created_by: AUTHOR,
  budgets: {
    enabled: true,
    admission_caps_enabled: true,
    policy_revision: 7,
    agents: { worker: cap(5) },
    key_ids: { key_a1: cap(2) },
  },
  routing: { agents: { max_run_usd: 3 } },
};

const EMPTY = {
  organization: "org-a",
  version: 1,
  budgets: { enabled: true, admission_caps_enabled: false, policy_revision: 1 },
  routing: {},
};

const CODES = /\b(402|429)\b|call_cap_unenforceable|agent_budget_exhausted/;

const theme = faTestTheme("light");

// The text of a node without its code lines.
function proseOf(node) {
  const copy = node.cloneNode(true);
  copy
    .querySelectorAll('[data-testid="admission-code"]')
    .forEach((line) => line.remove());
  return copy.textContent;
}

function fontSizeLike(variant, heading) {
  render(
    <Typography variant={variant} data-testid={`${variant}-reference`}>
      Reference
    </Typography>,
    { theme },
  );
  const size = window.getComputedStyle(heading).fontSize;
  expect(size).not.toBe("18px");
  expect(size).toBe(
    window.getComputedStyle(screen.getByTestId(`${variant}-reference`))
      .fontSize,
  );
}

describe("Admission cap panel copy", () => {
  // The Budgets tab's other section headings are subtitle1 at 600.
  it("titles the panel with the theme's subtitle1 variant, not a fixed 18 px", () => {
    render(<AdmissionCapEditor />, { theme });
    fontSizeLike(
      "subtitle1",
      screen.getByRole("heading", {
        level: 3,
        name: "Admission cap activation",
      }),
    );
  });

  it("titles Budgets & Cost Tracking with the same theme variant", () => {
    render(
      <BudgetsConfigTab
        budgets={{}}
        costTracking={{}}
        onBudgetsChange={vi.fn()}
        onCostTrackingChange={vi.fn()}
      />,
      { theme },
    );
    fontSizeLike(
      "subtitle1",
      screen.getByRole("heading", {
        level: 3,
        name: "Budgets & Cost Tracking",
      }),
    );
  });

  it("says in plain words what the panel shows, pointing only to a note that exists", () => {
    render(<AdmissionCapEditor />, { theme });
    expect(
      screen.getByText(
        "This is the saved policy. When this gateway may not enforce budgets, a note at the top of this tab says so.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        /control-plane|request walk|enforcement notice above/i,
      ),
    ).toBeNull();
  });

  it("keeps the exact codes, each on a code line under its plain sentence", () => {
    render(<AdmissionPolicySummary config={ACTIVE} />, { theme });
    const summary = screen.getByTestId("admission-policy-current");
    expect(proseOf(summary)).not.toMatch(CODES);
    const lines = within(summary)
      .getAllByTestId("admission-code")
      .map((line) => line.textContent);
    expect(lines.join(" | ")).toMatch(/402 call_cap_unenforceable/);
    expect(lines.join(" | ")).toMatch(/\b429\b/);
    expect(lines.join(" | ")).toMatch(/402 agent_budget_exhausted/);
  });

  it("says in plain words that unbound agent caps refuse every execution key, with the code under it", () => {
    render(<AdmissionPolicySummary config={ACTIVE} />, { theme });
    const alert = screen.getByTestId("admission-bindings-missing");
    expect(proseOf(alert)).toContain(
      "Agent caps are on, but no API key is linked to an agent, so the gateway refuses requests from every execution key.",
    );
    expect(within(alert).getByTestId("admission-code")).toHaveTextContent(
      "402 call_cap_unenforceable",
    );
  });

  it("names who last changed the config without a raw user id in the text", () => {
    render(<AdmissionPolicySummary config={ACTIVE} />, { theme });
    expect(screen.getByText(/^Last config change: saved by/)).toHaveTextContent(
      "Last config change: saved by a member of this organization. This is who saved the config, not necessarily who turned the caps on.",
    );
    expect(
      screen.getByTestId("admission-policy-current").textContent,
    ).not.toContain(AUTHOR);
  });

  it("shows the author's user id on hover", async () => {
    const user = userEvent.setup();
    render(<AdmissionPolicySummary config={ACTIVE} />, { theme });
    await user.hover(screen.getByTestId("admission-config-author"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      `User ID ${AUTHOR}`,
    );
  });

  it("shows the author's user id on keyboard focus too", async () => {
    const user = userEvent.setup();
    render(<AdmissionPolicySummary config={ACTIVE} />, { theme });
    const author = screen.getByTestId("admission-config-author");
    expect(author).toHaveAttribute("tabindex", "0");
    for (let i = 0; i < 10 && document.activeElement !== author; i += 1) {
      await user.tab();
    }
    expect(author).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      `User ID ${AUTHOR}`,
    );
  });

  it("shows a compact empty state that says who can add caps and how", () => {
    render(<AdmissionPolicySummary config={EMPTY} />, { theme });
    const summary = screen.getByTestId("admission-policy-current");
    expect(
      within(summary).getByText("No admission caps configured"),
    ).toBeInTheDocument();
    expect(
      within(summary).getByText(
        "An Owner or Admin can create an activation draft to set daily agent or key caps, or a per-run ceiling. The budgets switch alone does not turn admission caps on.",
      ),
    ).toBeInTheDocument();
  });
});
