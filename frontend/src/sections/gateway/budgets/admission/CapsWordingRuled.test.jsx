import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AdmissionCapEditor from "./AdmissionCapEditor";
import BudgetEnforcementCard from "../BudgetEnforcementCard";
import { createFakeBackend } from "./testing/serverFake";

// What daily caps do in this release, word for word: the caps-on sentences,
// the unbound-key line where an agent has a daily cap, and a Deactivate
// confirm that says caps can't be turned back on.
const { get, post, context } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  context: { canEdit: true },
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
    },
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: context.canEdit }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

const CAPS_ON =
  "Daily caps are on. A key under a cap can make only non-streaming chat requests, and its requests over the day's cap are refused. Streaming and Gemini requests from capped keys are refused, and native message and response requests are refused for every key while daily caps are on.";
const UNBOUND =
  "While any agent has a daily cap, a key not bound to an agent is refused.";
const TURN_OFF_TITLE = "Turn off daily caps?";
const TURN_OFF = "You can't turn them back on in this release.";

const cap = (limit) => ({
  limit,
  period: "daily",
  activated_at: "2026-11-01T00:00:00Z",
});
function config({ enabled = true, agents = true } = {}) {
  return {
    organization: "org-a",
    version: 3,
    budgets: {
      enabled: true,
      admission_caps_enabled: enabled,
      policy_revision: 5,
      fail_mode: "closed",
      no_max_tokens: "clamp",
      ...(agents ? { agents: { worker: cap(5) } } : {}),
      key_ids: { stable: cap(2) },
    },
    routing: {
      agents: {
        key_bindings: agents ? { stable: "worker" } : {},
        unpriced_under_cap: "reject",
      },
    },
  };
}

let queryClient;
let backend;
function panel(cfg) {
  backend = createFakeBackend({ config: cfg, ledger: "absent" });
  get.mockImplementation(backend.get);
  post.mockImplementation(backend.post);
  return render(
    <QueryClientProvider client={queryClient}>
      <AdmissionCapEditor />
    </QueryClientProvider>,
  );
}
const loaded = () => screen.findByTestId("admission-policy-current");

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  get.mockReset();
  post.mockReset();
  context.canEdit = true;
});

describe("caps on: the three caps-on sentences", () => {
  it("the admission panel shows them exactly, once", async () => {
    panel(config());
    await loaded();
    expect(screen.getByTestId("admission-caps-on-notice")).toHaveTextContent(
      CAPS_ON,
    );
    expect(screen.getAllByText(CAPS_ON)).toHaveLength(1);
  });

  it("the Budgets page shows the same words", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={config().budgets}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(screen.getByTestId("budget-caps-on-notice")).toHaveTextContent(
      CAPS_ON,
    );
  });
});

describe("the unbound-key line, only where an agent has a daily cap", () => {
  it("shows under the panel's caps list when an agent has a cap", async () => {
    panel(config());
    await loaded();
    expect(screen.getByTestId("admission-unbound-key-note")).toHaveTextContent(
      UNBOUND,
    );
  });

  it("is absent with key caps only", async () => {
    panel(config({ agents: false }));
    await loaded();
    expect(screen.queryByText(UNBOUND)).toBeNull();
  });

  it("is absent while caps are off", async () => {
    panel(config({ enabled: false }));
    await loaded();
    expect(screen.queryByText(UNBOUND)).toBeNull();
  });

  it("shows under the Budgets page's caps list when an agent has a cap", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={config().budgets}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(screen.getByTestId("budget-unbound-key-note")).toHaveTextContent(
      UNBOUND,
    );
  });

  it("is absent on the Budgets page with key caps only", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={config({ agents: false }).budgets}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(screen.queryByText(UNBOUND)).toBeNull();
  });
});

describe("Deactivate says caps can't be turned back on", () => {
  it("asks before sending anything, then turns caps off", async () => {
    panel(config());
    await loaded();
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    expect(await screen.findByText(TURN_OFF_TITLE)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`^${TURN_OFF}`))).toBeInTheDocument();
    expect(backend.state.posts).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Deactivate caps" }));
    await waitFor(() => expect(backend.state.posts).toHaveLength(1));
    expect(backend.state.posts[0].body.budgets).toMatchObject({
      admission_caps_enabled: false,
      policy_revision: 5,
    });
  });

  it("Cancel sends nothing", async () => {
    panel(config());
    await loaded();
    fireEvent.click(screen.getByTestId("admission-deactivate"));
    await screen.findByText(TURN_OFF_TITLE);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(backend.state.posts).toHaveLength(0);
  });
});
