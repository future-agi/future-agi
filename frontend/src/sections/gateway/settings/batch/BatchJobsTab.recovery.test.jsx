import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
  onlineManager,
} from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import BatchJobsTab from "./BatchJobsTab";

const { get, post, writeText, access } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  writeText: vi.fn(),
  access: { orgLevel: 8 },
}));

// Exercise the real status hook and React Query cache; mock only the existing
// HTTP, organization and notification boundaries used by the batch specs.
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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const route = "/dashboard/gateway/settings";
const completed = (content = "Recovered answer") => ({
  batch_id: "job-1",
  status: "completed",
  total: 1,
  summary: {
    completed: 1,
    failed: 0,
    cancelled: 0,
    total_cost: 0.0001,
    total_input_tokens: 2,
    total_output_tokens: 3,
  },
  results: [{
    index: 0,
    status: "success",
    response: { choices: [{ message: { content } }] },
  }],
});
const answer = (result) => ({ data: { status: true, result } });
const clients = [];

function mount({ search = "?tab=batch-jobs&batch_id=job-1", gatewayId = "gateway-1" } = {}) {
  window.history.replaceState({}, "", `${route}${search}`);
  const client = new QueryClient({
    // Deliberately permit retries globally: the status hook must opt out.
    defaultOptions: { queries: { retry: 2, retryDelay: 10 }, mutations: { retry: false } },
  });
  clients.push(client);
  const view = (id) => (
    <QueryClientProvider client={client}>
      <BatchJobsTab gatewayId={id} config={{ batch: {} }} />
    </QueryClientProvider>
  );
  const rendered = render(view(gatewayId));
  return { ...rendered, client, setGateway: (id) => rendered.rerender(view(id)) };
}

function navigateToJob(batchId) {
  const url = new URL(window.location.href);
  url.searchParams.set("batch_id", batchId);
  window.history.pushState({}, "", url);
  fireEvent.popState(window);
}

const details = () => screen.getByRole("dialog", { name: /Batch Details/ });
const stat = (label) => within(details()).getByText(label, { exact: true }).parentElement;
const advance = async (ms) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  writeText.mockReset().mockResolvedValue(undefined);
  access.orgLevel = 8;
  vi.stubGlobal("navigator", Object.defineProperty(Object.create(navigator), "clipboard", {
    configurable: true,
    value: { writeText },
  }));
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("BP2-06 batch job recovery", () => {
  it("opens a direct job URL in a fresh view and recovers the same results after remount", async () => {
    get.mockResolvedValue(answer(completed()));
    const first = mount();
    expect(await within(details()).findByText("Recovered answer")).toBeInTheDocument();
    expect(stat("Completed")).toHaveTextContent("1");
    expect(get).toHaveBeenCalledWith("/agentcc/gateways/gateway-1/get-batch/", {
      params: { batch_id: "job-1" },
    });
    first.unmount();
    mount();
    expect(await within(details()).findByText("Recovered answer")).toBeInTheDocument();
    fireEvent.click(within(details()).getByRole("button", { name: "Close batch details" }));
    expect(screen.getByText("Jobs submitted in this view")).toBeInTheDocument();
    expect(screen.getByText(/No batch jobs submitted in this view/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it.each(["pending", "processing"])("R3-CONTROLS-06 cancels a recovered %s job only after confirmation", async (status) => {
    get.mockResolvedValue(answer({ batch_id: "job-1", status, total: 1 }));
    post.mockImplementation(() => {
      get.mockResolvedValue(answer({ batch_id: "job-1", status: "cancelled", total: 1 }));
      return Promise.resolve(answer({ status: "cancelled" }));
    });
    mount();
    fireEvent.click(await within(details()).findByRole("button", { name: "Cancel job" }));
    const confirmation = screen.getByRole("dialog");
    expect(confirmation).toHaveTextContent("Requests already sent may still finish and incur charges");
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Keep running" }));
    await waitFor(() => expect(screen.queryByText(/Cancel batch job job-1\?/)).not.toBeInTheDocument());
    fireEvent.click(within(details()).getByRole("button", { name: "Cancel job" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel job" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/agentcc/gateways/gateway-1/cancel-batch/", { batch_id: "job-1" }));
    const reopenedDetails = await screen.findByRole("dialog", { name: /Batch Details/ });
    expect(await within(reopenedDetails).findByText("cancelled", { exact: true })).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("R3-CONTROLS-06 leaves recovered cancellation disabled for a reader", async () => {
    access.orgLevel = 1;
    get.mockResolvedValue(answer({ batch_id: "job-1", status: "processing", total: 1 }));
    mount();
    const cancel = await within(details()).findByRole("button", { name: "Cancel job" });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    expect(post).not.toHaveBeenCalled();
  });

  it.each(["completed", "failed", "cancelled"])("R3-CONTROLS-06 offers no cancellation for a terminal %s job", async (status) => {
    get.mockResolvedValue(answer({ ...completed(), status }));
    mount();
    await within(details()).findByText(status, { exact: true });
    expect(within(details()).queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();
  });

  it("copies an encoded job link with the settings route, unrelated query context and hash intact", async () => {
    get.mockResolvedValue(answer(completed()));
    const batchId = "batch /+?&=雪";
    mount({ search: `?workspace=team%2Fone&tab=batch-jobs&batch_id=${encodeURIComponent(batchId)}#investigation` });
    await within(details()).findByText("Recovered answer");
    fireEvent.click(within(details()).getByRole("button", { name: "Copy job link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = new URL(writeText.mock.calls[0][0]);
    expect(copied.origin).toBe(window.location.origin);
    expect(copied.pathname).toBe(route);
    expect(copied.searchParams.get("tab")).toBe("batch-jobs");
    expect(copied.searchParams.get("batch_id")).toBe(batchId);
    expect(copied.searchParams.get("workspace")).toBe("team/one");
    expect(copied.hash).toBe("#investigation");
    expect(get).toHaveBeenCalledWith(expect.any(String), { params: { batch_id: batchId } });
    expect(await within(details()).findByRole("status")).toHaveTextContent("Job link copied");
    expect(post).not.toHaveBeenCalled();
  });

  it("R3-CONTROLS-07 copies loaded results without another request or submission", async () => {
    get.mockResolvedValue(answer(completed()));
    mount();
    await within(details()).findByText("Recovered answer");
    fireEvent.click(within(details()).getByRole("button", { name: "Copy results" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(JSON.parse(writeText.mock.calls[0][0])).toMatchObject({
      ...completed(), export_scope: "loaded-results-only", snapshot_kind: "terminal",
      // A total without complete-charge evidence is copied as unknown.
      summary: { ...completed().summary, total_cost: null, cost_known: false },
    });
    expect(await within(details()).findByText("Results copied")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
  });

  it("R3-CONTROLS-07 labels a running export as partial and reports clipboard failure", async () => {
    get.mockResolvedValue(answer({ ...completed(), status: "processing" }));
    writeText.mockRejectedValue(new Error("Clipboard denied"));
    mount();
    expect(await within(details()).findByText(/Partial running snapshot/)).toBeInTheDocument();
    fireEvent.click(within(details()).getByRole("button", { name: "Copy results" }));
    expect(await within(details()).findByText(/Could not copy results/)).toBeInTheDocument();
    expect(within(details()).queryByText("Results copied")).not.toBeInTheDocument();
  });

  it("reports clipboard failure instead of claiming the link was copied", async () => {
    get.mockResolvedValue(answer(completed()));
    writeText.mockRejectedValue(new Error("Clipboard denied"));
    mount();
    await within(details()).findByText("Recovered answer");
    fireEvent.click(within(details()).getByRole("button", { name: "Copy job link" }));
    expect(await within(details()).findByRole("alert")).toHaveTextContent(/could not copy/i);
    expect(within(details()).queryByText("Job link copied")).not.toBeInTheDocument();
  });

  it("opens an ID without submitting and restores selection through Close, Forward and Back", async () => {
    get.mockResolvedValue(answer(completed()));
    access.orgLevel = 1;
    mount({ search: "?tab=batch-jobs&workspace=team-one#context" });
    expect(screen.getByRole("button", { name: "Open job" })).toBeDisabled();
    const input = screen.getByRole("textbox", { name: "Batch job ID" });
    fireEvent.change(input, { target: { value: " job-1 " } });
    fireEvent.submit(input.closest("form"));
    await within(details()).findByText("Recovered answer");
    expect(new URLSearchParams(window.location.search).get("batch_id")).toBe("job-1");
    fireEvent.click(within(details()).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(window.location.search).toBe("?tab=batch-jobs&workspace=team-one");
    expect(window.location.hash).toBe("#context");
    act(() => window.history.forward());
    await screen.findByRole("dialog", { name: /Batch Details/ });
    act(() => window.history.back());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(post).not.toHaveBeenCalled();
  });

  it("closing a directly loaded link removes only its job selection", async () => {
    get.mockResolvedValue(answer(completed()));
    mount({ search: "?tab=batch-jobs&batch_id=job-1&workspace=team-one#context" });
    await within(details()).findByText("Recovered answer");
    fireEvent.click(within(details()).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const search = new URLSearchParams(window.location.search);
    expect(search.has("batch_id")).toBe(false);
    expect(search.get("tab")).toBe("batch-jobs");
    expect(search.get("workspace")).toBe("team-one");
    expect(window.location.hash).toBe("#context");
    expect(post).not.toHaveBeenCalled();
  });

  it("shows loading rather than zero completed requests while the status read is pending", async () => {
    let resolve;
    get.mockReturnValue(new Promise((done) => { resolve = done; }));
    mount();
    expect(within(details()).getByRole("status")).toHaveTextContent(/loading batch job/i);
    expect(within(details()).queryByText("Completed", { exact: true })).not.toBeInTheDocument();
    await act(async () => resolve(answer(completed())));
    expect(await within(details()).findByText("Recovered answer")).toBeInTheDocument();
  });

  it.each([
    ["not found", { statusCode: 404, message: "Batch not found" }, /not found or not accessible/i],
    ["denied", { statusCode: 403, message: "Forbidden" }, /do not have access/i],
    ["unauthenticated", { statusCode: 401 }, /sign in/i],
    ["network failure", new Error("Network Error"), /couldn't load this batch job/i],
  ])("distinguishes %s from an empty completed batch and Retry only reads status", async (_, error, message) => {
    get.mockRejectedValueOnce(error).mockResolvedValue(answer(completed()));
    mount();
    expect(await within(details()).findByRole("alert")).toHaveTextContent(message);
    expect(within(details()).queryByText("Completed", { exact: true })).not.toBeInTheDocument();
    expect(within(details()).queryByText("No results available.")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);
    fireEvent.click(within(details()).getByRole("button", { name: "Retry" }));
    expect(await within(details()).findByText("Recovered answer")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
    expect(post).not.toHaveBeenCalled();
  });

  it("preserves a recorded failed job with unknown completion without inventing zero counts", async () => {
    get.mockResolvedValue(answer({
      batch_id: "job-1", status: "failed", total: 4, outcome: "unknown",
      error: "The gateway no longer holds this batch, so its outcome is unknown.",
    }));
    mount();
    expect(await within(details()).findByRole("alert")).toHaveTextContent(/completion unknown/i);
    expect(within(details()).getByText("failed", { exact: true })).toBeInTheDocument();
    expect(stat("Completed")).toHaveTextContent("Not reported");
    expect(stat("Failed")).toHaveTextContent("Not reported");
    expect(stat("Tokens")).toHaveTextContent("Not reported");
    expect(within(details()).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(within(details()).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("shows reported failure separately from an unknown outcome", async () => {
    get.mockResolvedValue(answer({ status: "failed", total: 2, summary: { failed: 2 }, error: "Runner stopped" }));
    mount();
    expect(await within(details()).findByRole("alert")).toHaveTextContent(/batch failed.*Runner stopped/i);
    expect(stat("Failed")).toHaveTextContent("2");
    expect(within(details()).queryByText(/completion unknown/i)).not.toBeInTheDocument();
    expect(within(details()).queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("keeps recorded completion distinct from expired summary and results", async () => {
    get.mockResolvedValue(answer({ status: "completed", total: 3, error: "Its results and summary are gone; this status was recorded when it finished." }));
    mount();
    expect(await within(details()).findByRole("alert")).toHaveTextContent(/results and summary are gone/i);
    expect(within(details()).getByText("completed", { exact: true })).toBeInTheDocument();
    expect(stat("Completed")).toHaveTextContent("Not reported");
    expect(within(details()).queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("clears old results and expansion on job or gateway changes and ignores a late old response", async () => {
    let resolveOld;
    get.mockImplementation((url, { params }) => {
      if (params.batch_id === "job-3" && url.includes("gateway-1")) {
        return new Promise((resolve) => { resolveOld = resolve; });
      }
      const content = url.includes("gateway-2")
        ? "New gateway answer"
        : params.batch_id === "job-2" ? "New job answer" : "Original answer";
      return Promise.resolve(answer(completed(content)));
    });
    const { client, setGateway } = mount();
    fireEvent.click(await within(details()).findByText("Original answer"));
    expect(within(details()).getByText(/"choices"/)).toBeVisible();
    navigateToJob("job-2");
    expect(within(details()).queryByText("Original answer")).not.toBeInTheDocument();
    expect(await within(details()).findByText("New job answer")).toBeInTheDocument();
    expect(within(details()).getByText(/"choices"/)).not.toBeVisible();
    fireEvent.click(within(details()).getByText("New job answer"));
    expect(within(details()).getByText(/"choices"/)).toBeVisible();
    navigateToJob("job-3");
    expect(within(details()).queryByText("New job answer")).not.toBeInTheDocument();
    expect(within(details()).getByRole("status")).toHaveTextContent(/loading batch job/i);
    setGateway("gateway-2");
    expect(await within(details()).findByText("New gateway answer")).toBeInTheDocument();
    expect(within(details()).getByText(/"choices"/)).not.toBeVisible();
    await act(async () => resolveOld(answer(completed("Obsolete answer"))));
    expect(within(details()).queryByText("Obsolete answer")).not.toBeInTheDocument();
    expect(client.getQueryData(["agentcc-batch-status", "gateway-1", "job-1"]).results[0].response.choices[0].message.content).toBe("Original answer");
    expect(client.getQueryData(["agentcc-batch-status", "gateway-2", "job-3"]).results[0].response.choices[0].message.content).toBe("New gateway answer");
    expect(get).toHaveBeenCalledWith("/agentcc/gateways/gateway-2/get-batch/", { params: { batch_id: "job-3" } });
    expect(post).not.toHaveBeenCalled();
  });

  it.each(["completed", "failed", "cancelled"])("does not poll a terminal %s job", async (status) => {
    vi.useFakeTimers();
    get.mockResolvedValue(answer({ ...completed(), status }));
    mount();
    await advance(50);
    expect(within(details()).getByText(status, { exact: true })).toBeInTheDocument();
    await advance(12000);
    expect(get).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
  });

  it.each(["pending", "processing"])("polls a readable %s job but stops as soon as the detail closes", async (status) => {
    vi.useFakeTimers();
    get.mockResolvedValue(answer({ status, total: 2, summary: { completed: 1, failed: 0 } }));
    mount();
    await advance(50);
    expect(within(details()).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
    await advance(3100);
    expect(get).toHaveBeenCalledTimes(2);
    fireEvent.click(within(details()).getByRole("button", { name: "Close" }));
    await advance(50);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await advance(12000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("stops after a polling read fails, and one failed Retry cannot start a read loop", async () => {
    vi.useFakeTimers();
    get.mockResolvedValueOnce(answer({ status: "processing", total: 2 }))
      .mockRejectedValue({ statusCode: 404, message: "Batch not found" });
    mount();
    await advance(3200);
    expect(within(details()).getByRole("alert")).toHaveTextContent(/not found or not accessible/i);
    expect(within(details()).queryByText("Total", { exact: true })).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
    await advance(15000);
    expect(get).toHaveBeenCalledTimes(2);
    fireEvent.click(within(details()).getByRole("button", { name: "Retry" }));
    await advance(50);
    expect(get).toHaveBeenCalledTimes(3);
    act(() => { focusManager.setFocused(false); onlineManager.setOnline(false); });
    act(() => { focusManager.setFocused(true); onlineManager.setOnline(true); });
    await advance(15000);
    expect(get).toHaveBeenCalledTimes(3);
    fireEvent.click(within(details()).getByRole("button", { name: "Close" }));
    const input = screen.getByRole("textbox", { name: "Batch job ID" });
    fireEvent.change(input, { target: { value: "job-1" } });
    fireEvent.submit(input.closest("form"));
    await advance(12000);
    expect(within(details()).getByRole("alert")).toHaveTextContent(/not found or not accessible/i);
    expect(get).toHaveBeenCalledTimes(3);
    get.mockResolvedValue(answer(completed()));
    fireEvent.click(within(details()).getByRole("button", { name: "Retry" }));
    await advance(50);
    expect(within(details()).getByText("Recovered answer")).toBeInTheDocument();
    await advance(12000);
    expect(get).toHaveBeenCalledTimes(4);
    expect(post).not.toHaveBeenCalled();
  });

  it("does not keep polling an unreported status or unknown completion", async () => {
    vi.useFakeTimers();
    get.mockResolvedValue(answer({ total: 2, outcome: "unknown" }));
    mount();
    await advance(50);
    expect(within(details()).getByRole("alert")).toHaveTextContent(/completion unknown/i);
    expect(within(details()).queryByRole("progressbar")).not.toBeInTheDocument();
    await advance(12000);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("does not poll closed tracked rows and clears the view's submitted jobs when gateway changes", async () => {
    vi.useFakeTimers();
    post.mockResolvedValue(answer({ batch_id: "submitted-here" }));
    get.mockResolvedValue(answer({ status: "processing", total: 1 }));
    const { setGateway } = mount({ search: "?tab=batch-jobs" });
    fireEvent.click(screen.getByRole("button", { name: "Submit Batch Job" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    await advance(50);
    // Let the real MUI exit transition release its modal aria-hidden state
    // after React commits the mutation's close update.
    await advance(500);
    expect(screen.getByText("Jobs submitted in this view")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View Details" })).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);
    await advance(12000);
    expect(get).toHaveBeenCalledTimes(1);
    setGateway("gateway-2");
    expect(screen.queryByRole("button", { name: "View Details" })).not.toBeInTheDocument();
    expect(screen.getByText(/No batch jobs submitted in this view/)).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gateway-1/submit-batch/");
  });
});

describe("submitted job rows", () => {
  // Match the page text as Testing Library normalizes it (a locale's time may
  // carry a narrow no-break space before AM/PM).
  const checkedText = (date) => `Checked ${date.toLocaleTimeString()}`.replace(/\s+/g, " ");

  it("says when a row's status was checked and shows a finished job as finished, without Cancel, after Check status", async () => {
    vi.useFakeTimers();
    const submittedAt = new Date(2026, 10, 9, 10, 42, 5, 0);
    vi.setSystemTime(submittedAt);
    post.mockResolvedValue(answer({ batch_id: "submitted-here" }));
    get.mockResolvedValue(answer({ status: "processing", total: 1, summary: { completed: 0, failed: 0 } }));
    mount({ search: "?tab=batch-jobs" });
    fireEvent.click(screen.getByRole("button", { name: "Submit Batch Job" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    await advance(50);
    await advance(500);
    const table = screen.getByRole("table");
    expect(within(table).getByText("processing", { exact: true })).toBeInTheDocument();
    expect(within(table).getByRole("progressbar")).toBeInTheDocument();
    expect(within(table).getByText(checkedText(submittedAt))).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Cancel" })).toBeInTheDocument();

    // The job finishes on the gateway while its row stays closed.
    get.mockResolvedValue(answer({ ...completed(), batch_id: "submitted-here" }));
    await advance(12000);
    expect(get).toHaveBeenCalledTimes(1);

    const checkedAt = new Date(2026, 10, 9, 10, 47, 30, 0);
    vi.setSystemTime(checkedAt);
    fireEvent.click(within(table).getByRole("button", { name: "Check status" }));
    await advance(50);
    expect(within(table).getByText("completed", { exact: true })).toBeInTheDocument();
    expect(within(table).queryByText("processing", { exact: true })).not.toBeInTheDocument();
    expect(within(table).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(within(table).getByText(checkedText(checkedAt))).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
    expect(get).toHaveBeenLastCalledWith("/agentcc/gateways/gateway-1/get-batch/", {
      params: { batch_id: "submitted-here" },
    });
    expect(post).toHaveBeenCalledTimes(1);
  });
});
