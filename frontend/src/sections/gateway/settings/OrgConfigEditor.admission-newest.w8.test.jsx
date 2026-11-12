import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";
import {
  ACTIVE_URL,
  CREATE_URL,
  createFakeBackend,
} from "../budgets/admission/testing/serverFake";

// The editor opened on the page's read with inactive caps, and
// while it stays open the page delivers a newer read of the same organization
// with active caps (activated in another tab, then a refreshed Org Config
// query). The guard must follow that newest read, not the draft's base: Save
// sends no budgets.enabled=false, other edits are saved in one update-config,
// and admission deactivation stays available. The editor's own read after an
// admission change outranks the page read it saw, until the page reads again.

const { get, post, context, budgetForm, stub } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  context: { canEdit: true },
  budgetForm: { props: null },
  stub: () => ({ default: () => null }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      orgConfig: {
        active: "/agentcc/org-configs/active/",
        create: "/agentcc/org-configs/",
      },
      apiKeys: "/agentcc/api-keys/",
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("@iconify/react", () => ({ Icon: () => null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: context.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { org_sections_enforced: ["budgets", "routing", "cost_tracking"] },
  }),
  // A not-applied receipt renders Retry apply; it is never clicked here.
  useReloadConfig: () => ({ mutateAsync: vi.fn(), isPending: false }),
  refreshGatewayConfig: vi.fn(async () => {}),
  asRequestError: (error) => error,
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));
vi.mock("./ProviderConfigTab", stub);
vi.mock("./GuardrailConfigTab", stub);
vi.mock("./RoutingConfigTab", stub);
vi.mock("./CacheConfigTab", stub);
vi.mock("./RateLimitingConfigTab", stub);
vi.mock("./SecurityConfigTab", stub);
vi.mock("./AlertingConfigTab", stub);
vi.mock("./ModelDatabaseConfigTab", stub);
vi.mock("./ModelMapConfigTab", stub);
// The real form, with its props captured so a test can make a budgets edit
// while a save is pending.
vi.mock("./BudgetsConfigTab", async (importOriginal) => {
  const { default: BudgetsConfigTab } = await importOriginal();
  return {
    default: (props) => {
      budgetForm.props = props;
      return <BudgetsConfigTab {...props} />;
    },
  };
});

const UPDATE_URL = "/agentcc/gateways/gw-1/update-config/";
// Active spellings in the newer read: canonical when present, else the alias.
const ACTIVE = [
  ["canonical", { admission_caps_enabled: true, policy_revision: 4 }],
  ["alias-only", { admissionCapsEnabled: true, policyRevision: 4 }],
  [
    "canonical active over inactive alias",
    {
      admission_caps_enabled: true,
      admissionCapsEnabled: false,
      policy_revision: 4,
    },
  ],
];
const EXPLANATION = /deactivate daily admission caps.*before turning/i;

let client;
const budgetToggle = () =>
  screen.getByRole("checkbox", { name: "Enable per-org budgets" });
const saveButton = () =>
  screen.getByRole("button", { name: "Save & Activate" });
const synced = () =>
  Promise.resolve({ data: { result: { gateway_synced: true } } });

// Synthetic org configs only.
function readOf(version, policy) {
  return {
    organization: "org-a",
    version,
    budgets: {
      enabled: true,
      ...policy,
      key_ids: {
        "synthetic-key-id": {
          limit: 5,
          period: "daily",
          activated_at: "2026-11-09T00:00:00Z",
        },
      },
    },
    routing: {},
  };
}
// The page's read when the editor opened: caps inactive, revision 3.
const olderRead = () =>
  readOf(4, { admission_caps_enabled: false, policy_revision: 3 });

// Opens the editor on the page's older read while the server already holds
// the newer one (the admission panel reads the server itself). deliver() is
// the page's refreshed read arriving while the editor stays open.
function openOnOlderRead(policy, updateConfig = synced) {
  const backend = createFakeBackend({ config: readOf(5, policy) });
  get.mockImplementation(backend.get);
  post.mockImplementation((url, body, options) =>
    url === UPDATE_URL ? updateConfig(body) : backend.post(url, body, options),
  );
  const onClose = vi.fn();
  const ui = (initialConfig) => (
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={initialConfig}
        defaultTab="budgets"
      />
    </QueryClientProvider>
  );
  const view = render(ui(olderRead()));
  return { onClose, deliver: (config) => view.rerender(ui(config)) };
}
async function ready() {
  await screen.findByTestId("admission-policy-current");
}
async function save(onClose) {
  fireEvent.click(saveButton());
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
}
function expectNoEnforcementOffPost() {
  post.mock.calls.forEach(([, body]) =>
    expect(body?.budgets?.enabled).not.toBe(false),
  );
}
// The user turns budgets off with the real switch while the editor still
// shows the older read's inactive caps: an ordinary edit at that moment.
function turnOffWhileInactive() {
  expect(budgetToggle()).toBeEnabled();
  expect(budgetToggle()).toBeChecked();
  fireEvent.click(budgetToggle());
  expect(budgetToggle()).not.toBeChecked();
}
function turnOnCostTracking() {
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Enable custom cost tracking" }),
  );
}
// The admission panel's own read now reaches the guard, so an off
// made after it is already locked. Opens like openOnOlderRead, but the
// panel's read of the server (the newer, active one) stays pending until
// release(), so the off can be made first, as an ordinary edit.
function openOnOlderReadPanelHeld(policy, updateConfig = synced) {
  const backend = createFakeBackend({ config: readOf(5, policy) });
  let releaseRead = () => {};
  const held = new Promise((resolve) => {
    releaseRead = resolve;
  });
  get.mockImplementation((url, options) =>
    url === ACTIVE_URL
      ? held.then(() => backend.get(url, options))
      : backend.get(url, options),
  );
  post.mockImplementation((url, body, options) =>
    url === UPDATE_URL ? updateConfig(body) : backend.post(url, body, options),
  );
  const onClose = vi.fn();
  const ui = (initialConfig) => (
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={initialConfig}
        defaultTab="budgets"
      />
    </QueryClientProvider>
  );
  const view = render(ui(olderRead()));
  return {
    onClose,
    deliver: (config) => view.rerender(ui(config)),
    release: () =>
      act(async () => {
        releaseRead();
      }),
  };
}
// The off is made while the panel is still loading and the editor shows only
// the older read's inactive caps; then the panel's active read arrives.
async function turnOffBeforePanelRead(release) {
  expect(screen.getByTestId("admission-loading")).toBeInTheDocument();
  turnOffWhileInactive();
  await release();
  await ready();
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  context.canEdit = true;
  budgetForm.props = null;
});
afterEach(() => client.clear());

describe("a newer read with active caps reaches an editor opened on an inactive read", () => {
  it.each(ACTIVE)(
    "%s: the off edit is left out, other edits are saved in one update-config, deactivation stays available",
    async (_name, policy) => {
      const { onClose, deliver, release } = openOnOlderReadPanelHeld(policy);
      await turnOffBeforePanelRead(release);
      turnOnCostTracking();
      deliver(readOf(5, policy));
      await waitFor(() =>
        expect(screen.getByTestId("admission-deactivate")).toBeEnabled(),
      );
      await save(onClose);
      expectNoEnforcementOffPost();
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        UPDATE_URL,
        { cost_tracking: { enabled: true } },
        expect.anything(),
      );
    },
  );

  // Gateway mustKeep 6: a not-dirty save just closes.
  it.each(ACTIVE)(
    "%s: the switch shows on, locked and explained; the ignored off alone sends nothing and closes",
    async (_name, policy) => {
      const { onClose, deliver, release } = openOnOlderReadPanelHeld(policy);
      await turnOffBeforePanelRead(release);
      deliver(readOf(5, policy));
      await waitFor(() => expect(budgetToggle()).toBeDisabled());
      expect(budgetToggle()).toBeChecked();
      expect(screen.getByText(EXPLANATION)).toHaveTextContent(
        /Admission cap activation panel above/i,
      );
      fireEvent.click(budgetToggle());
      expect(budgetToggle()).toBeChecked();
      await save(onClose);
      expect(post).not.toHaveBeenCalled();
    },
  );
});

describe("gateway save-path rules with the newer active read", () => {
  // mustKeep 1 and 6: one update-config with the merge patch; the guarded
  // budgets become saved state; a failed apply keeps the editor open.
  it("a failed apply stays open and the ignored off is stored as saved, never resent", async () => {
    const policy = ACTIVE[0][1];
    const { onClose, deliver, release } = openOnOlderReadPanelHeld(policy, () =>
      Promise.resolve({
        data: { result: { version: 6, gateway_synced: false } },
      }),
    );
    await turnOffBeforePanelRead(release);
    turnOnCostTracking();
    deliver(readOf(5, policy));
    await waitFor(() => expect(budgetToggle()).toBeDisabled());
    fireEvent.click(saveButton());
    await screen.findByText(/Saved · Not applied/);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("All edits stored.")).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    expect(budgetToggle()).toBeChecked();
    expectNoEnforcementOffPost();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      UPDATE_URL,
      { cost_tracking: { enabled: true } },
      expect.anything(),
    );
  });

  // mustKeep 4 and 6: an edit made while Save is pending is kept, the editor
  // stays open for it, and the next Save sends only that edit.
  it("a budgets edit made while Save is pending is kept and sent alone next", async () => {
    const policy = ACTIVE[1][1];
    let finishSave;
    const updateConfig = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishSave = resolve;
          }),
      )
      .mockImplementation(synced);
    const { onClose, deliver, release } = openOnOlderReadPanelHeld(
      policy,
      updateConfig,
    );
    await turnOffBeforePanelRead(release);
    turnOnCostTracking();
    deliver(readOf(5, policy));
    await waitFor(() => expect(budgetToggle()).toBeDisabled());
    fireEvent.click(saveButton());
    await screen.findByRole("button", { name: /Saving/ });
    act(() => {
      budgetForm.props.onBudgetsChange({
        ...budgetForm.props.budgets,
        org_limit: 7,
      });
    });
    await act(async () => {
      finishSave({ data: { result: { version: 6, gateway_synced: true } } });
    });
    await screen.findByText(/Saved · Applied to gateway/);
    expect(
      screen.getByText(/Unsaved changes are not applied/),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenLastCalledWith(
      UPDATE_URL,
      { cost_tracking: { enabled: true } },
      expect.anything(),
    );
    await save(onClose);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenLastCalledWith(
      UPDATE_URL,
      { budgets: { org_limit: 7 } },
      expect.anything(),
    );
    expectNoEnforcementOffPost();
  });
});

describe("the newest read decides", () => {
  it("the editor's own deactivation read outranks the unchanged page read, and a later page read outranks it", async () => {
    const policy = ACTIVE[0][1];
    const { onClose, deliver } = openOnOlderRead(policy);
    await ready();
    deliver(readOf(5, policy));
    await waitFor(() => expect(budgetToggle()).toBeDisabled());
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await screen.findByText("Daily caps inactive");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      CREATE_URL,
      expect.objectContaining({
        budgets: expect.objectContaining({
          enabled: true,
          admission_caps_enabled: false,
          policy_revision: 4,
        }),
      }),
      expect.anything(),
    );
    // The page's read still says active; the editor's read is newer.
    await waitFor(() => expect(budgetToggle()).toBeEnabled());
    turnOffWhileInactive();
    // Caps are activated again elsewhere and the page reads them.
    deliver(readOf(7, { admission_caps_enabled: true, policy_revision: 6 }));
    await waitFor(() => expect(budgetToggle()).toBeDisabled());
    expect(budgetToggle()).toBeChecked();
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();
    await save(onClose);
    expectNoEnforcementOffPost();
    expect(post).toHaveBeenCalledTimes(1);
  });
});
