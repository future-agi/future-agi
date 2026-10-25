// Guardrail feedback in the request drawer: the widget loads the reviewer's
// own saved answer (GET ?mine=true), Edit PATCHes that answer, and reopening
// the drawer shows it.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RequestDetailDrawer from "../logs/RequestDetailDrawer";
import FeedbackWidget from "./FeedbackWidget";
import FeedbackSummaryCard from "./FeedbackSummaryCard";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  saved: [],
  detailLog: { current: null },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post, patch: h.patch },
  endpoints: {
    gateway: {
      requestLogDetail: (id) => `/agentcc/request-logs/${id}/`,
      guardrailFeedback: {
        list: "/agentcc/guardrail-feedback/",
        create: "/agentcc/guardrail-feedback/",
        detail: (id) => `/agentcc/guardrail-feedback/${id}/`,
        summary: "/agentcc/guardrail-feedback/summary/",
      },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("../logs/hooks/useRequestDetail", () => ({
  default: (logId) => ({
    data: logId ? { result: h.detailLog.current } : null,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

const LOG = {
  id: "log-1",
  request_id: "req-1",
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 403,
  latency_ms: 12,
  input_tokens: 10,
  output_tokens: 0,
  cost: "0.000000",
  started_at: "2026-10-19T10:00:00Z",
  is_stream: false,
  cache_hit: false,
  fallback_used: false,
  is_error: true,
  request_body: { messages: [{ role: "user", content: "hi" }] },
  response_body: { error: { code: "content_blocked" } },
  request_headers: {},
  response_headers: {},
  guardrail_triggered: true,
  guardrail_results: {
    action: "block",
    checks: [
      { name: "pii_detection", action: "block", score: 0.97, threshold: 0.8 },
    ],
  },
};

const feedbackGets = () =>
  h.get.mock.calls.filter(([url]) => url === "/agentcc/guardrail-feedback/");

let client;
function renderDrawer(props) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (p) => (
    <QueryClientProvider client={client}>
      <RequestDetailDrawer onClose={() => {}} {...p} />
    </QueryClientProvider>
  );
  const utils = render(ui(props));
  return { ...utils, rerenderWith: (p) => utils.rerender(ui(p)) };
}

beforeEach(() => {
  h.get.mockReset();
  h.post.mockReset();
  h.patch.mockReset();
  h.saved = [];
  h.detailLog.current = LOG;
  // The server: GET ?mine=true lists this reviewer's answers; POST creates;
  // PATCH changes one.
  h.get.mockImplementation(async (url, config) => {
    if (url === "/agentcc/guardrail-feedback/") {
      const p = config?.params || {};
      return {
        data: {
          status: true,
          result: h.saved.filter(
            (r) =>
              r.request_log === p.request_log_id &&
              r.check_name === p.check_name,
          ),
        },
      };
    }
    if (url === "/agentcc/guardrail-feedback/summary/") {
      return { data: { status: true, result: [] } };
    }
    throw new Error(`unexpected GET ${url}`);
  });
  h.post.mockImplementation(async (url, body) => {
    const row = {
      id: `fb-${h.saved.length + 1}`,
      updated_at: "2026-10-21T10:00:00Z",
      ...body,
    };
    h.saved.push(row);
    return { data: { status: true, result: row } };
  });
  h.patch.mockImplementation(async (url, body) => {
    const row = h.saved.find((r) => url.endsWith(`/${r.id}/`));
    Object.assign(row, body, { updated_at: "2026-10-21T10:05:00Z" });
    return { data: { status: true, result: { ...row } } };
  });
});

describe("Edit changes the saved answer", () => {
  it("False Positive, Edit, Correct: 1 POST and 1 PATCH; reopening shows the answer and posts nothing", async () => {
    const user = userEvent.setup();
    const { rerenderWith } = renderDrawer({ logId: "log-1", open: true });

    await user.click(await screen.findByRole("tab", { name: "Guardrails" }));
    await waitFor(() => expect(feedbackGets()).toHaveLength(1));
    expect(feedbackGets()[0][1].params).toEqual({
      request_log_id: "log-1",
      check_name: "pii_detection",
      mine: "true",
    });

    await user.click(await screen.findByText("False Positive"));
    await user.click(screen.getByRole("button", { name: "Submit Feedback" }));
    await screen.findByText(/Feedback:\s*False Positive/);

    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByText("Correct"));
    await user.click(screen.getByRole("button", { name: "Submit Feedback" }));
    await screen.findByText(/Feedback:\s*Correct/);

    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post.mock.calls[0][1]).toMatchObject({
      request_log: "log-1",
      check_name: "pii_detection",
      feedback: "false_positive",
    });
    expect(h.patch).toHaveBeenCalledTimes(1);
    expect(h.patch.mock.calls[0][0]).toBe("/agentcc/guardrail-feedback/fb-1/");
    expect(h.patch.mock.calls[0][1]).toEqual({
      feedback: "correct",
      comment: "",
    });

    // Close the drawer the way RequestExplorerSection does, then reopen.
    rerenderWith({ logId: null, open: false });
    await waitFor(() =>
      expect(screen.queryByText(/Feedback:/)).not.toBeInTheDocument(),
    );
    rerenderWith({ logId: "log-1", open: true });
    await user.click(await screen.findByRole("tab", { name: "Guardrails" }));
    expect(await screen.findByText(/Feedback:\s*Correct/)).toBeInTheDocument();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.patch).toHaveBeenCalledTimes(1);
  });
});

describe("the widget loads the saved answer", () => {
  function renderWidget() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <FeedbackWidget requestLogId="log-1" checkName="pii_detection" />
      </QueryClientProvider>,
    );
  }

  it("shows a saved answer and its comment, and Edit starts from them", async () => {
    h.saved = [
      {
        id: "fb-9",
        request_log: "log-1",
        check_name: "pii_detection",
        feedback: "unsure",
        comment: "looks like a test number",
        updated_at: "2026-10-20T09:00:00Z",
      },
    ];
    const user = userEvent.setup();
    renderWidget();
    expect(await screen.findByText(/Feedback:\s*Unsure/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText(/Comment/)).toHaveValue(
      "looks like a test number",
    );
    await user.click(screen.getByText("False Negative"));
    await user.click(screen.getByRole("button", { name: "Submit Feedback" }));
    await screen.findByText(/Feedback:\s*False Negative/);
    expect(h.post).not.toHaveBeenCalled();
    expect(h.patch.mock.calls[0]).toEqual([
      "/agentcc/guardrail-feedback/fb-9/",
      { feedback: "false_negative", comment: "looks like a test number" },
    ]);
  });

  it("of two saved rows for one check, edits the latest", async () => {
    h.saved = [
      {
        id: "fb-old",
        request_log: "log-1",
        check_name: "pii_detection",
        feedback: "false_positive",
        comment: "",
        updated_at: "2026-10-19T09:00:00Z",
      },
      {
        id: "fb-new",
        request_log: "log-1",
        check_name: "pii_detection",
        feedback: "correct",
        comment: "",
        updated_at: "2026-10-20T09:00:00Z",
      },
    ];
    const user = userEvent.setup();
    renderWidget();
    expect(await screen.findByText(/Feedback:\s*Correct/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByText("Unsure"));
    await user.click(screen.getByRole("button", { name: "Submit Feedback" }));
    await screen.findByText(/Feedback:\s*Unsure/);
    expect(h.patch.mock.calls[0][0]).toBe(
      "/agentcc/guardrail-feedback/fb-new/",
    );
  });

  it("with no saved answer, offers the choices and creates one", async () => {
    const user = userEvent.setup();
    renderWidget();
    await waitFor(() => expect(feedbackGets()).toHaveLength(1));
    expect(screen.getByText("Guardrail Feedback:")).toBeInTheDocument();
    await user.click(screen.getByText("Correct"));
    await user.click(screen.getByRole("button", { name: "Submit Feedback" }));
    await screen.findByText(/Feedback:\s*Correct/);
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.patch).not.toHaveBeenCalled();
  });
});

describe("the summary card adds up", () => {
  it("shows Unsure, so Total equals the four answer counts", async () => {
    h.get.mockImplementation(async () => ({
      data: {
        status: true,
        result: [
          {
            check_name: "pii_detection",
            total_feedback: 4,
            correct_count: 1,
            false_positive_count: 1,
            false_negative_count: 0,
            unsure_count: 2,
          },
        ],
      },
    }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <FeedbackSummaryCard />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole("columnheader", { name: "Unsure" }),
    ).toBeInTheDocument();
    const row = screen.getByText("pii_detection").closest("tr");
    expect(row).toHaveTextContent("4");
    expect(row).toHaveTextContent("2");
  });
});
