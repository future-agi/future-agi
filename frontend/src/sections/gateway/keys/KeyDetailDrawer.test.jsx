import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import KeyDetailDrawer from "./KeyDetailDrawer";
import { expectAdminOnly } from "../testing/adminOnly";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "signed-in-user" } }),
}));
const org = vi.hoisted(() => ({ level: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: org.level,
    isReady: true,
    currentOrganizationId: "org-a",
  }),
}));

const { keyState, analyticsState, overviewCalls, usageCalls } = vi.hoisted(
  () => ({
    keyState: { data: null },
    analyticsState: { isError: false, overview: undefined },
    overviewCalls: [],
    usageCalls: [],
  }),
);

vi.mock("./hooks/useApiKeys", () => ({
  useApiKeyDetail: () => ({ data: keyState.data, isLoading: false }),
  useRevokeApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateApiKey: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: (params, options) => {
    overviewCalls.push({ params, options });
    return { data: analyticsState.overview, isError: analyticsState.isError };
  },
}));

vi.mock("../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: (params, options) => {
    usageCalls.push({ params, options });
    return { data: undefined, isError: analyticsState.isError };
  },
}));

const renderDrawer = () =>
  render(
    <KeyDetailDrawer
      keyId="key-uuid"
      open
      onClose={vi.fn()}
      gatewayId="gw-1"
    />,
  );

describe("KeyDetailDrawer per-key analytics", () => {
  beforeEach(() => {
    overviewCalls.length = 0;
    usageCalls.length = 0;
    analyticsState.isError = false;
    analyticsState.overview = undefined;
    keyState.data = {
      id: "0f1b1f3e-0000-4000-8000-000000000001",
      name: "prod key",
      status: "active",
      // What the gateway stamps on a request log. Not a UUID, and the only
      // value `api_key_id` can be filtered on.
      gatewayKeyId: "gw-key-7f3a",
      allowedModels: [],
      allowedProviders: [],
    };
  });

  it("filters by the gateway key id, not the key's UUID primary key", () => {
    renderDrawer();

    expect(overviewCalls.at(-1).params.apiKeyId).toBe("gw-key-7f3a");
    expect(usageCalls.at(-1).params.apiKeyId).toBe("gw-key-7f3a");
  });

  it("opens Logs filtered to this key, on the parameter Logs reads", () => {
    // Logs (logs/hooks/useFilters.js) and the API read api_key_id; an
    // api_key parameter is refused by the dev contract and ignored by the
    // API, which then lists every request of the organization.
    renderDrawer();
    const link = screen.getByRole("link", { name: /view logs/i });
    expect(link.getAttribute("href")).toBe(
      "/dashboard/gateway/logs?api_key_id=gw-key-7f3a",
    );
  });

  it("keeps these ambient reads off the app-wide error toast", () => {
    // Otherwise a failed refetch lands next to a save the user just made and
    // reads as "the save failed".
    renderDrawer();

    expect(overviewCalls.at(-1).options.meta).toEqual({ errorHandled: true });
    expect(usageCalls.at(-1).options.meta).toEqual({ errorHandled: true });
  });

  it("reports a failed usage read in the panel it belongs to", () => {
    analyticsState.isError = true;

    renderDrawer();

    expect(
      screen.getByText(/Usage for this key could not be loaded/i),
    ).toBeInTheDocument();
  });

  // A key that sent nothing has a true 0 requests, cost
  // and tokens, but no error rate; it read "0.0%" (and "0%" when missing).
  it("reads — for the error rate of a key with no requests", () => {
    analyticsState.overview = {
      total_requests: { value: 0, trend: null },
      total_cost: { value: "0", trend: null },
      total_tokens: { value: 0, trend: null },
      error_rate: { value: 0, trend: null },
    };

    renderDrawer();

    const figure = (label) =>
      screen.getByText(label, { selector: "p, span" }).parentElement
        .textContent;
    expect(figure("Requests")).toBe("Requests0");
    expect(figure("Cost")).toBe("Cost$0.00");
    expect(figure("Tokens")).toBe("Tokens0");
    expect(figure("Error rate")).toBe("Error rate\u2014");
  });

  it("shows a dash, never a zero, for each figure the failed read did not give", () => {
    // Under its own "could not be loaded" line the panel read Requests 0,
    // Cost $0.00, Tokens 0, Error rate 0%.
    analyticsState.isError = true;

    renderDrawer();

    const figure = (label) =>
      screen.getByText(label, { selector: "p, span" }).parentElement
        .textContent;
    expect(figure("Requests")).toBe("Requests\u2014");
    expect(figure("Cost")).toBe("Cost\u2014");
    expect(figure("Tokens")).toBe("Tokens\u2014");
    expect(figure("Error rate")).toBe("Error rate\u2014");
  });

  // A key's Cost over unpriced requests (stored at 0) read
  // "$0.00". It reads unknown when none had a price, a floor when some had.
  it("reads the key's Cost as unknown or a floor when its requests had no price", () => {
    const figure = (label) =>
      screen.getByText(label, { selector: "p, span" }).parentElement;
    analyticsState.overview = {
      total_requests: { value: 4, trend: null },
      total_cost: { value: "0", trend: null, unpriced_requests: 4 },
      total_tokens: { value: 3208, trend: null },
      error_rate: { value: 0, trend: null },
    };
    const { unmount } = renderDrawer();
    expect(figure("Cost").textContent).toBe("Cost\u2014");
    expect(within(figure("Cost")).getByTitle("No price found")).toBeVisible();
    unmount();

    analyticsState.overview = {
      ...analyticsState.overview,
      total_cost: { value: "0.02", trend: null, unpriced_requests: 1 },
    };
    renderDrawer();
    expect(figure("Cost").textContent).toBe("Cost\u2265\u00a0$0.02");
    expect(
      within(figure("Cost")).getByTitle(
        "1 of 4 requests had no price found, so this total counts only the priced ones.",
      ),
    ).toBeVisible();
  });

  it("says nothing when the usage read succeeded", () => {
    renderDrawer();

    expect(
      screen.queryByText(/Usage for this key could not be loaded/i),
    ).not.toBeInTheDocument();
  });
});

describe("KeyDetailDrawer actions", () => {
  beforeEach(() => {
    org.level = 8;
    keyState.data = {
      id: "key-uuid",
      user: "peer-user",
      organization: "org-a",
      name: "prod key",
      status: "active",
      gatewayKeyId: "gw-key-7f3a",
      keyPrefix: "sk-agentcc-4...",
      allowedModels: ["gpt-4o"],
      allowedProviders: [],
    };
  });

  it("hands Edit, Rotate and Revoke of its own live key to the page, for a writer", async () => {
    keyState.data.user = "signed-in-user";
    const onRotate = vi.fn();
    const onRevoke = vi.fn();
    render(
      <KeyDetailDrawer
        keyId="key-uuid"
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        onEdit={vi.fn()}
        onRotate={onRotate}
        onRevoke={onRevoke}
      />,
    );
    screen.getByRole("button", { name: "Rotate" }).click();
    screen.getByRole("button", { name: "Revoke" }).click();
    expect(onRotate).toHaveBeenCalledWith(keyState.data);
    expect(onRevoke).toHaveBeenCalledWith(keyState.data);
    expect(screen.getByText("All providers")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("offers no actions on a revoked key", () => {
    keyState.data = { ...keyState.data, status: "revoked" };
    render(<KeyDetailDrawer keyId="key-uuid" open onClose={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Rotate" })).toBeNull();
    expect(screen.getByText("Revoked locally")).toBeInTheDocument();
    expect(
      screen.getByText(/Gateway enforcement is not confirmed/),
    ).toBeInTheDocument();
  });

  it("hides peer Rotate and shows a reader Edit and Revoke disabled, saying why", () => {
    org.level = 3;
    render(<KeyDetailDrawer keyId="key-uuid" open onClose={vi.fn()} />);
    for (const name of ["Edit", "Revoke"]) {
      expectAdminOnly(screen.getByRole("button", { name }));
    }
    expect(screen.queryByRole("button", { name: "Rotate" })).toBeNull();
    expect(screen.getByRole("link", { name: /View logs/i })).toBeEnabled();
  });
});
