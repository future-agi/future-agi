import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import BatchJobsTab from "./BatchJobsTab";

const { get, post, writeText, snack, access } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  writeText: vi.fn(),
  snack: vi.fn(),
  access: { orgLevel: 8, currentOrganizationId: "org-1" },
}));

// The real submit and status hooks; only the HTTP, organization and
// notification boundaries are mocked, as in the other batch specs.
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      batch: {
        get: (id) => `/agentcc/gateways/${id}/get-batch/`,
        submit: (id) => `/agentcc/gateways/${id}/submit-batch/`,
        cancel: (id) => `/agentcc/gateways/${id}/cancel-batch/`,
      },
    },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => access,
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => snack(...args),
}));

const BATCH_ID = "batch_3f9c1e7a5b2d4c6e8f0a1b3c5d7e9f21";
const NOTE =
  "This list keeps jobs submitted in this browser tab. Copy a job's ID or link to reopen it later.";
const answer = (result) => ({ data: { status: true, result } });
const clients = [];

// Gateway settings mounts Batch jobs only while ?tab=batch-jobs
// (GatewaySettingsSection.jsx): any other tab unmounts it.
const settings = (client, view) => (
  <QueryClientProvider client={client}>
    {view.tab === "batch-jobs" ? (
      <BatchJobsTab gatewayId={view.gatewayId} config={{ batch: {} }} />
    ) : (
      <p>Org config</p>
    )}
  </QueryClientProvider>
);

function mount() {
  window.history.replaceState({}, "", "/dashboard/gateway/settings?tab=batch-jobs");
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const view = { tab: "batch-jobs", gatewayId: "gateway-1" };
  const rendered = render(settings(client, view));
  const show = (next) => {
    Object.assign(view, next);
    rendered.rerender(settings(client, view));
  };
  return { ...rendered, show };
}

async function submitJob() {
  fireEvent.click(screen.getByRole("button", { name: "Submit Batch Job" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  // The rest of the page stays aria-hidden until the dialog has exited.
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  return screen.findByRole("table");
}

beforeEach(() => {
  get.mockReset().mockResolvedValue(answer({ status: "processing", total: 1 }));
  post.mockReset().mockResolvedValue(answer({ batch_id: BATCH_ID }));
  writeText.mockReset().mockResolvedValue(undefined);
  snack.mockReset();
  access.orgLevel = 8;
  access.currentOrganizationId = "org-1";
  window.sessionStorage.clear();
  vi.stubGlobal(
    "navigator",
    Object.defineProperty(Object.create(navigator), "clipboard", {
      configurable: true,
      value: { writeText },
    }),
  );
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("submitted batch jobs stay findable", () => {
  it("shows the submitted job's full ID in its row and copies it", async () => {
    mount();
    const table = await submitJob();
    expect(within(table).getByText(BATCH_ID, { exact: true })).toBeInTheDocument();
    expect(within(table).queryByText(/\.\.\.$/)).not.toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: "Copy batch ID" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(BATCH_ID));
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it("keeps the submitted job after switching to another settings tab and back", async () => {
    const { show } = mount();
    await submitJob();
    show({ tab: "org-config" });
    expect(screen.getByText("Org config")).toBeInTheDocument();
    expect(screen.queryByText(BATCH_ID)).not.toBeInTheDocument();
    show({ tab: "batch-jobs" });
    const table = screen.getByRole("table");
    expect(within(table).getByText(BATCH_ID, { exact: true })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "View Details" })).toBeInTheDocument();
    expect(screen.queryByText(/No batch jobs submitted in this view/)).not.toBeInTheDocument();
    // Coming back reads status again; it never submits the job again.
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("names the submitted job in the success message", async () => {
    mount();
    await submitJob();
    expect(snack).toHaveBeenCalledWith(`Batch job ${BATCH_ID} submitted`, {
      variant: "success",
    });
  });

  it("keeps each organization's and gateway's submitted jobs apart", async () => {
    const { show } = mount();
    await submitJob();
    access.currentOrganizationId = "org-2";
    show({});
    expect(screen.queryByText(BATCH_ID)).not.toBeInTheDocument();
    expect(screen.getByText(/No batch jobs submitted in this view/)).toBeInTheDocument();
    access.currentOrganizationId = "org-1";
    show({ gatewayId: "gateway-2" });
    expect(screen.queryByText(BATCH_ID)).not.toBeInTheDocument();
    show({ gatewayId: "gateway-1" });
    expect(
      within(screen.getByRole("table")).getByText(BATCH_ID, { exact: true }),
    ).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
});
