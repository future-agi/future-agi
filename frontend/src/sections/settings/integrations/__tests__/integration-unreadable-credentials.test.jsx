import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";
import IntegrationDetail from "../IntegrationDetail";
import EditIntegrationDialog from "../EditIntegrationDialog";
import { INTEGRATION_ADMIN_ONLY } from "../access";

// A connection whose saved credentials cannot be read says so on its card,
// once, with a Reconnect that opens that connection's credentials edit (for
// Slack and Linear, its Manage dialog) without leaving the page. That edit
// asks for each credential as required to reconnect and refuses a blank one
// under its field. Where Edit can't take the credentials again (a message
// queue), Reconnect says so and offers to remove the connection. Its
// connection page says the same once. A readable connection's card shows
// none of it.

const SENTENCE =
  "This connection's saved credentials can't be read. Reconnect it to use it again.";
const REQUIRED = "Required to reconnect.";
const MIXPANEL_OPTIONAL =
  "Optional. With the API secret, request logs go to Mixpanel's /import API, which also accepts logs older than 5 days; without it, to /track, which accepts the last 5 days.";
const QUEUE_NOT_HERE =
  "Its credentials can't be entered again in Edit, and this page doesn't offer adding Message Queue connections, so it can't be reconnected here. You can remove it.";
const LINEAR_RECONNECT =
  "To reconnect it, disconnect it, then connect Linear again with an API key.";
const KEEP_CURRENT = "Leave blank to keep the current one";

const h = vi.hoisted(() => ({
  role: "Admin",
  connections: [],
  detail: null,
  navigate: vi.fn(),
  updateConnection: vi.fn(),
  startSlackInstall: vi.fn(),
  deleteDialog: vi.fn(),
}));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: h.connections,
    isLoading: false,
    isError: false,
  }),
  useIntegrationConnection: () => ({
    data: h.detail,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useStartSlackInstall: () => ({
    mutate: h.startSlackInstall,
    isPending: false,
  }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConnection: () => ({
    mutate: h.updateConnection,
    isPending: false,
    error: null,
  }),
  useSyncNow: () => ({
    mutate: vi.fn(),
    isPending: false,
    completionUnconfirmed: false,
    checkStatus: vi.fn(),
  }),
  usePauseConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeConnection: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role }),
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: true,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => h.navigate,
}));

vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));
vi.mock("../webhooks/WebhooksSection", () => ({ default: () => null }));
vi.mock("../IntegrationSyncHistory", () => ({ default: () => null }));
vi.mock("../DeleteIntegrationDialog", () => ({
  default: (props) => {
    h.deleteDialog(props);
    return null;
  },
}));

const UNREADABLE_ID = "c0a80101-0000-4000-8000-000000000021";
const QUEUE_ID = "c0a80101-0000-4000-8000-000000000024";

const unreadableLangfuse = (extra = {}) => ({
  id: UNREADABLE_ID,
  platform: "langfuse",
  display_name: "Self-hosted Langfuse",
  host_url: "https://langfuse.example.com",
  status: "active",
  status_message: SENTENCE,
  credentials_unreadable: true,
  external_project_name: "support",
  total_traces_synced: 0,
  total_spans_synced: 0,
  total_scores_synced: 0,
  sync_interval_seconds: 300,
  last_synced_at: null,
  backfill_completed: true,
  backfill_progress: {},
  ...extra,
});

const readablePosthog = {
  id: "c0a80101-0000-4000-8000-000000000022",
  platform: "posthog",
  display_name: "Product analytics",
  host_url: "https://us.i.posthog.com",
  status: "active",
  status_message: "",
  credentials_unreadable: false,
  external_project_name: "posthog",
  total_traces_synced: 0,
  sync_interval_seconds: 300,
  last_synced_at: null,
  backfill_completed: true,
  backfill_progress: {},
};

const unreadableSlack = {
  id: "c0a80101-0000-4000-8000-000000000023",
  platform: "slack",
  display_name: "Engineering Slack",
  host_url: "https://slack.com",
  status: "active",
  status_message: SENTENCE,
  credentials_unreadable: true,
  external_project_name: "T-SAME",
};

const unreadableLinear = {
  id: "c0a80101-0000-4000-8000-000000000026",
  platform: "linear",
  display_name: "Linear",
  host_url: "https://api.linear.app",
  status: "active",
  status_message: SENTENCE,
  credentials_unreadable: true,
  external_project_name: "Engineering",
};

const unreadableQueue = {
  id: QUEUE_ID,
  platform: "message_queue",
  display_name: "Gateway queue",
  host_url: "https://sqs.eu-west-1.amazonaws.com/123456789012/gateway-logs",
  status: "error",
  status_message: SENTENCE,
  credentials_unreadable: true,
  external_project_name: "message_queue",
  total_traces_synced: 0,
  sync_interval_seconds: 300,
  last_synced_at: null,
  backfill_completed: true,
  backfill_progress: {},
};

const unreadableS3 = {
  id: "c0a80101-0000-4000-8000-000000000025",
  platform: "cloud_storage",
  display_name: "Gateway logs",
  host_url:
    "https://acme-gateway-logs.s3.eu-west-1.amazonaws.com/agentcc/production",
  status: "error",
  status_message: SENTENCE,
  credentials_unreadable: true,
  sync_interval_seconds: 300,
};

const unreadableMixpanel = {
  id: "c0a80101-0000-4000-8000-000000000027",
  platform: "mixpanel",
  display_name: "Gateway events",
  host_url: "https://api.mixpanel.com",
  status: "error",
  status_message: SENTENCE,
  credentials_unreadable: true,
  sync_interval_seconds: 300,
};

const withClient = (ui) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);
const renderList = () => render(withClient(<IntegrationsList />));
const renderDetail = () => render(withClient(<IntegrationDetail />));
const renderEdit = (connection) => {
  const ui = (
    <EditIntegrationDialog open onClose={vi.fn()} connection={connection} />
  );
  return render(withClient(ui));
};

const cardOf = (element) => element.closest('[role="button"]');
const reconnectButtons = () =>
  screen.queryAllByRole("button", { name: "Reconnect" });

beforeEach(() => {
  h.role = "Admin";
  h.connections = [];
  h.detail = null;
  h.navigate.mockClear();
  h.updateConnection.mockClear();
  h.startSlackInstall.mockClear();
  h.deleteDialog.mockClear();
});

describe("A connection whose saved credentials cannot be read", () => {
  it("its card says so once, with Reconnect; a readable card has neither", () => {
    h.connections = [unreadableLangfuse(), readablePosthog];
    renderList();

    const sentences = screen.queryAllByText(SENTENCE);
    expect(sentences).toHaveLength(1);
    expect(cardOf(sentences[0])).toHaveTextContent("Self-hosted Langfuse");
    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    expect(cardOf(reconnect[0])).toHaveTextContent("Self-hosted Langfuse");

    const readable = cardOf(screen.getByText("Product analytics"));
    expect(readable).not.toHaveTextContent(SENTENCE);
    const readableReconnect = within(readable).queryByRole("button", {
      name: "Reconnect",
    });
    expect(readableReconnect).toBeNull();
  });

  it("a Langfuse import in error says it once, not as a second stop reason", () => {
    h.connections = [unreadableLangfuse({ status: "error" })];
    renderList();

    expect(screen.queryAllByText(SENTENCE)).toHaveLength(1);
    expect(screen.queryByTestId("integration-card-stopped-reason")).toBeNull();
    expect(reconnectButtons()).toHaveLength(1);
  });

  it("Reconnect opens that connection's credentials edit and stays on the page", async () => {
    const user = userEvent.setup();
    h.connections = [unreadableLangfuse(), readablePosthog];
    renderList();

    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    await user.click(reconnect[0]);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Edit Integration")).toBeInTheDocument();
    const name = within(dialog).getByLabelText("Display Name");
    expect(name).toHaveValue("Self-hosted Langfuse");
    expect(h.navigate).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText("Public Key"), "pk-new");
    await user.type(within(dialog).getByLabelText("Secret Key"), "sk-new");
    const save = within(dialog).getByRole("button", { name: "Save Changes" });
    await user.click(save);
    await waitFor(() => expect(h.updateConnection).toHaveBeenCalledTimes(1));
    expect(h.updateConnection.mock.calls[0][0]).toEqual({
      id: UNREADABLE_ID,
      data: { public_key: "pk-new", secret_key: "sk-new" },
    });
  });

  it("the edit Reconnect opens asks for each credential as required to reconnect", () => {
    renderEdit(unreadableS3);

    const dialog = screen.getByRole("dialog");
    ["Access Key ID", "Secret Access Key"].forEach((label) => {
      const input = within(dialog).getByLabelText(label);
      expect(input).toHaveValue("");
      expect(input).not.toHaveAttribute("placeholder");
    });
    expect(within(dialog).queryAllByText(REQUIRED)).toHaveLength(2);
    expect(within(dialog).queryByPlaceholderText(KEEP_CURRENT)).toBeNull();
  });

  it("a blank required credential is refused under its field and nothing is sent", async () => {
    const user = userEvent.setup();
    h.connections = [unreadableLangfuse()];
    renderList();

    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    await user.click(reconnect[0]);
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Public Key"), "pk-new");
    const save = within(dialog).getByRole("button", { name: "Save Changes" });
    await user.click(save);

    const refused = "Secret Key is required to reconnect.";
    expect(await within(dialog).findByText(refused)).toBeInTheDocument();
    expect(h.updateConnection).not.toHaveBeenCalled();
  });

  it("Mixpanel's API secret stays optional and says what leaving it blank does", async () => {
    const user = userEvent.setup();
    renderEdit(unreadableMixpanel);

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryAllByText(REQUIRED)).toHaveLength(1);
    expect(within(dialog).queryAllByText(MIXPANEL_OPTIONAL)).toHaveLength(1);

    const token = within(dialog).getByLabelText("Project Token");
    await user.type(token, "tok-new");
    const save = within(dialog).getByRole("button", { name: "Save Changes" });
    await user.click(save);
    await waitFor(() => expect(h.updateConnection).toHaveBeenCalledTimes(1));
    expect(h.updateConnection.mock.calls[0][0].data).toEqual({
      credentials: { project_token: "tok-new" },
    });
  });

  it("a message queue's Reconnect says it can't be reconnected here and offers to remove it", async () => {
    const user = userEvent.setup();
    h.connections = [unreadableQueue];
    renderList();

    expect(screen.queryAllByText(SENTENCE)).toHaveLength(1);
    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    await user.click(reconnect[0]);

    const dialog = await screen.findByRole("dialog");
    const title = within(dialog).getByText("Reconnect Gateway queue");
    expect(title).toBeInTheDocument();
    expect(dialog).toHaveTextContent(QUEUE_NOT_HERE);
    expect(within(dialog).queryByText("Edit Integration")).toBeNull();
    expect(h.navigate).not.toHaveBeenCalled();

    const remove = within(dialog).getByRole("button", {
      name: "Remove connection",
    });
    await user.click(remove);
    const removing = expect.objectContaining({
      open: true,
      connection: expect.objectContaining({ id: QUEUE_ID }),
    });
    await waitFor(() =>
      expect(h.deleteDialog).toHaveBeenLastCalledWith(removing),
    );
  });

  it("a Slack workspace's Reconnect opens its Manage dialog, which reconnects through Slack", async () => {
    const user = userEvent.setup();
    h.connections = [unreadableSlack];
    renderList();

    expect(screen.queryAllByText(SENTENCE)).toHaveLength(1);
    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    await user.click(reconnect[0]);

    const dialog = await screen.findByRole("dialog");
    const title = within(dialog).getByText("Manage Engineering Slack");
    expect(title).toBeInTheDocument();
    const slack = within(dialog).getByRole("button", {
      name: "Reconnect Slack",
    });
    await user.click(slack);
    expect(h.startSlackInstall).toHaveBeenCalledWith(
      unreadableSlack.id,
      expect.any(Object),
    );
  });

  it("Linear's Reconnect opens its Manage dialog, which says how to reconnect it", async () => {
    const user = userEvent.setup();
    h.connections = [unreadableLinear];
    renderList();

    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    await user.click(reconnect[0]);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryAllByText(LINEAR_RECONNECT)).toHaveLength(1);
    const disconnect = within(dialog).getByRole("button", {
      name: "Disconnect",
    });
    expect(disconnect).toBeEnabled();
  });

  it("a role that cannot manage integrations sees it with Reconnect disabled", () => {
    h.role = "Viewer";
    h.connections = [unreadableLangfuse()];
    renderList();

    expect(screen.queryAllByText(SENTENCE)).toHaveLength(1);
    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    expect(reconnect[0]).toBeDisabled();
    const adminOnly = `[aria-label="${INTEGRATION_ADMIN_ONLY}"]`;
    expect(reconnect[0].closest(adminOnly)).not.toBeNull();
  });

  it("its connection page says so once, with Reconnect opening Edit", async () => {
    const user = userEvent.setup();
    h.detail = unreadableLangfuse({
      project_name: "Support",
      public_key_display: "****",
      secret_key_display: "****",
    });
    renderDetail();

    expect(screen.queryAllByText(SENTENCE)).toHaveLength(1);
    const reconnect = reconnectButtons();
    expect(reconnect).toHaveLength(1);
    await user.click(reconnect[0]);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Edit Integration")).toBeInTheDocument();
  });
});
