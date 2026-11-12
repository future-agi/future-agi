import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  renderWithRouter,
  screen,
} from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";
import IntegrationCard from "../IntegrationCard";
import IntegrationDetail from "../IntegrationDetail";

// Integrations belong to the organization: only the roles that see Add
// Integration enabled (Owner, Admin, Workspace Admin) add, reconnect,
// disconnect, edit, pause, resume or remove one. Every other role sees the
// same pages read-only, each write control disabled with "Owner or Admin only".

const h = vi.hoisted(() => ({
  role: "Owner",
  connections: [],
  startSlackInstall: vi.fn(),
  deleteConnection: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  syncNow: vi.fn(),
  detail: null,
  addIntegrationWizard: vi.fn(() => null),
}));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: h.connections,
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({
    mutate: h.startSlackInstall,
    isPending: false,
  }),
  useDeleteConnection: () => ({ mutate: h.deleteConnection, isPending: false }),
  useIntegrationConnection: () => ({
    data: h.detail,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSyncNow: () => ({ mutate: h.syncNow, isPending: false }),
  usePauseConnection: () => ({ mutate: h.pause, isPending: false }),
  useResumeConnection: () => ({ mutate: h.resume, isPending: false }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role }),
}));

vi.mock("../AddIntegrationWizard", () => ({
  default: h.addIntegrationWizard,
}));
// Error Feed allowed, so the page offers Linear too.
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: true,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("../IntegrationSyncHistory", () => ({ default: () => null }));
vi.mock("../EditIntegrationDialog", () => ({ default: () => null }));
vi.mock("../DeleteIntegrationDialog", () => ({ default: () => null }));

const ADMIN_ONLY = "Owner or Admin only";
const READ_ONLY_ROLES = [
  "Member",
  "Viewer",
  "workspace_member",
  "workspace_viewer",
];
const MANAGER_ROLES = ["Owner", "Admin", "workspace_admin"];

const SLACK_CONNECTION = {
  id: "conn-slack",
  platform: "slack",
  display_name: "Acme Slack",
  host_url: "https://slack.com",
  created_at: "2026-10-20T10:00:00Z",
};

const LANGFUSE_CONNECTION = {
  id: "conn-lf",
  platform: "langfuse",
  display_name: "Acme Langfuse",
  host_url: "https://langfuse.example.com",
  status: "active",
  created_at: "2026-10-20T10:00:00Z",
};

const withQuery = (ui) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);

const expectAdminOnly = (element) => {
  expect(element).toBeDisabled();
  expect(element.closest(`[aria-label="${ADMIN_ONLY}"]`)).not.toBeNull();
};

const platformCard = (name) =>
  screen.getByRole("button", { name: new RegExp(`^${name}(?:\\s|$)`, "i") });

const expectPlatformAdminOnly = (element) => {
  expect(element).toHaveAttribute("aria-disabled", "true");
  expect(element).toHaveAttribute("tabindex", "-1");
  expect(element.closest(`[aria-label="${ADMIN_ONLY}"]`)).not.toBeNull();
};

const PLATFORM_ACTIVATIONS = [
  { label: "click", activate: (element) => fireEvent.click(element) },
  {
    label: "Enter",
    activate: (element) => fireEvent.keyDown(element, { key: "Enter" }),
  },
  {
    label: "Space",
    activate: (element) => fireEvent.keyDown(element, { key: " " }),
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  h.role = "Owner";
  h.connections = [];
  h.detail = null;
  window.history.pushState({}, "", "/");
});

describe("Integrations page", () => {
  describe.each(PLATFORM_ACTIVATIONS)(
    "platform $label activation",
    ({ activate }) => {
      it.each(READ_ONLY_ROLES)(
        "%s sees the platform cards read-only",
        (role) => {
          h.role = role;
          render(withQuery(<IntegrationsList />));
          screen
            .getAllByRole("button", { name: /add integration/i })
            .forEach(expectAdminOnly);
          ["PostHog", "Datadog", "Slack", "Linear"].forEach((name) => {
            const card = platformCard(name);
            expectPlatformAdminOnly(card);
            activate(card);
          });
          expect(h.startSlackInstall).not.toHaveBeenCalled();
          expect(h.addIntegrationWizard).toHaveBeenCalled();
          h.addIntegrationWizard.mock.calls.forEach(([props]) => {
            expect(props.open).toBe(false);
          });
        },
      );

      it.each(MANAGER_ROLES)("%s can open every platform card", (role) => {
        h.role = role;
        render(withQuery(<IntegrationsList />));
        [
          { name: "PostHog", id: "posthog" },
          { name: "Datadog", id: "datadog" },
          { name: "Linear", id: "linear" },
        ].forEach(({ name, id }) => {
          const card = platformCard(name);
          expect(card).not.toHaveAttribute("aria-disabled");
          expect(card).toHaveAttribute("tabindex", "0");
          card.focus();
          expect(card).toHaveFocus();
          activate(card);
          expect(h.addIntegrationWizard.mock.lastCall[0]).toMatchObject({
            open: true,
            initialPlatform: id,
          });
        });
        const slack = platformCard("Slack");
        expect(slack).not.toHaveAttribute("aria-disabled");
        expect(slack).toHaveAttribute("tabindex", "0");
        slack.focus();
        expect(slack).toHaveFocus();
        activate(slack);
        expect(h.startSlackInstall).toHaveBeenCalledTimes(1);
      });
    },
  );

  it("does not start a Slack install from a link for a read-only role", () => {
    h.role = "Member";
    renderWithRouter(withQuery(<IntegrationsList />), {
      route: "/dashboard/settings/integrations?platform=slack",
    });
    expect(h.startSlackInstall).not.toHaveBeenCalled();
    expect(
      screen.getByText(/only an owner or admin can connect slack/i),
    ).toBeInTheDocument();
  });
});

describe("Manage dialog of a connected Slack workspace", () => {
  const openManage = () => {
    render(<IntegrationCard connection={SLACK_CONNECTION} />);
    fireEvent.click(screen.getByText("Acme Slack"));
  };

  it.each(READ_ONLY_ROLES)("%s cannot reconnect or disconnect", (role) => {
    h.role = role;
    openManage();
    expectAdminOnly(screen.getByRole("button", { name: "Reconnect Slack" }));
    expectAdminOnly(screen.getByRole("button", { name: "Disconnect" }));
  });

  it.each(MANAGER_ROLES)("%s can reconnect and disconnect", (role) => {
    h.role = role;
    openManage();
    expect(
      screen.getByRole("button", { name: "Reconnect Slack" }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(h.deleteConnection).toHaveBeenCalledWith(
      "conn-slack",
      expect.any(Object),
    );
  });
});

describe("Integration detail page", () => {
  it.each(READ_ONLY_ROLES)(
    "%s cannot edit, remove or pause the connection",
    (role) => {
      h.role = role;
      h.detail = LANGFUSE_CONNECTION;
      render(<IntegrationDetail />);
      expectAdminOnly(
        screen.getByRole("button", { name: /integration options menu/i }),
      );
      expectAdminOnly(screen.getByRole("button", { name: "Pause" }));
    },
  );

  it.each(READ_ONLY_ROLES)("%s cannot resume a paused connection", (role) => {
    h.role = role;
    h.detail = { ...LANGFUSE_CONNECTION, status: "paused" };
    render(<IntegrationDetail />);
    expectAdminOnly(screen.getByRole("button", { name: "Resume" }));
  });

  it.each(MANAGER_ROLES)("%s can edit, remove and pause", (role) => {
    h.role = role;
    h.detail = LANGFUSE_CONNECTION;
    render(<IntegrationDetail />);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(h.pause).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole("button", { name: /integration options menu/i }),
    );
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Delete" }),
    ).toBeInTheDocument();
  });
});
