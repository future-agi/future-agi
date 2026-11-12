import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { endpoints } from "src/utils/axios";
import { ACCESS } from "../catalogRows";
import ModelTryIt from "./ModelTryIt";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post },
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: () => ({ data: false }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));

const row = {
  id: "openai/model-a",
  type: "chat",
  access: ACCESS.MANAGED,
  managedOn: true,
  ownRoutes: [],
  model: {},
};
const TITLE = "Try it is not available in this release";
const NEXT = "Call the model with your API key instead.";
const BILLED = /Stopped before the answer was complete|may still be billed/;
const SERVER_MESSAGE =
  "Unavailable in this release: Playground, Try-it and Guardrail test will return when delegated calls are available.";
// delegated_availability.unavailable_response: Django's 409 envelope, sent
// before any gateway call.
const ENVELOPE_733 = {
  status: false,
  type: "conflict",
  code: "feature_unavailable_733",
  detail: SERVER_MESSAGE,
  message: SERVER_MESSAGE,
  error: SERVER_MESSAGE,
  result: SERVER_MESSAGE,
  known_issue: "KI-733-PG",
};
// The shared axios interceptor rejects an HTTP error with the body spread
// and the status as statusCode.
const rejected = (status, body) => ({
  ...body,
  statusCode: status,
  transportCode: "ERR_BAD_REQUEST",
  responseHeaders: {},
});
const sse = (event, data) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const PARTIAL =
  sse("meta", { status_code: 200 }) +
  sse("delta", { content: "Partial answer" });
const FULL =
  PARTIAL +
  sse("done", {
    status_code: 200,
    usage: { prompt_tokens: 4, completion_tokens: 2 },
  });

// The browser reports download progress on an error response too: the
// stream reader sees the refusal's JSON body before axios rejects.
function refuseAfterProgress(status, body) {
  post.mockImplementation(async (_url, _body, options) => {
    options.onDownloadProgress?.({
      event: { target: { responseText: JSON.stringify(body) } },
    });
    throw rejected(status, body);
  });
}

function renderTryIt(props = {}) {
  const onOpenApi = vi.fn();
  render(
    <MemoryRouter>
      <ModelTryIt row={row} gatewayId="gw-1" onOpenApi={onOpenApi} {...props} />
    </MemoryRouter>,
  );
  return { onOpenApi };
}

// A block body: vitest runs a function returned from beforeEach as teardown.
beforeEach(() => {
  post.mockReset();
});
afterEach(cleanup);

describe("Try it when this release has no delegated calls", () => {
  it("says so before any prompt, offers the API instead, and sends nothing", async () => {
    const { onOpenApi } = renderTryIt({ delegatedPlaygroundAvailable: false });
    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    expect(screen.getByText(NEXT)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Prompt" })).toBeNull();
    expect(
      screen.queryByRole("textbox", { name: "System prompt (optional)" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(
      screen.queryByRole("checkbox", { name: "Stream the answer" }),
    ).toBeNull();
    // One action, reachable by keyboard.
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const action = screen.getByRole("button", {
      name: "Open API instructions",
    });
    action.focus();
    await userEvent.keyboard("{Enter}");
    expect(onOpenApi).toHaveBeenCalledTimes(1);
    expect(document.body).not.toHaveTextContent(BILLED);
    expect(document.body).not.toHaveTextContent(/will return when/);
    expect(post).not.toHaveBeenCalled();
  });

  it("shows a streamed 733 refusal as that state, without an empty answer or billing doubt", async () => {
    refuseAfterProgress(409, ENVELOPE_733);
    renderTryIt();
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(
      await screen.findByRole("heading", { name: TITLE }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(NEXT);
    expect(
      screen.getByRole("button", { name: "Open API instructions" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(screen.queryByText("(empty answer)")).toBeNull();
    expect(document.body).not.toHaveTextContent(BILLED);
    expect(screen.queryByText(/Completion unknown/)).toBeNull();
    expect(screen.queryByText("Request diagnostics")).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe(
      endpoints.gateway.testPlaygroundStream("gw-1"),
    );
  });

  it("shows a non-streamed 733 refusal as the same state", async () => {
    post.mockRejectedValue(rejected(409, ENVELOPE_733));
    renderTryIt();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(
      await screen.findByRole("heading", { name: TITLE }),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(BILLED);
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("a refusal before the stream began", () => {
  it("shows the refusal message only, never an empty answer or the stopped-stream line", async () => {
    refuseAfterProgress(403, {
      status: false,
      type: "permission_denied",
      code: "permission_denied",
      message: "Only an organization Owner or Admin can run a test request.",
    });
    renderTryIt();
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only an organization Owner or Admin can run a test request.",
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
    );
    expect(screen.queryByText("(empty answer)")).toBeNull();
    expect(screen.queryByText("Waiting for the first words…")).toBeNull();
    expect(document.body).not.toHaveTextContent(BILLED);
    // The draft is still there to edit and run again.
    expect(screen.getByRole("textbox", { name: "Prompt" })).toHaveValue(
      "Write one sentence about the sea.",
    );
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("a stream cut after it began", () => {
  it("keeps the partial answer and the billing line", async () => {
    post.mockImplementation(async (_url, _body, options) => {
      options.onDownloadProgress?.({
        event: { target: { responseText: PARTIAL } },
      });
      throw new Error("Connection interrupted");
    });
    renderTryIt({ delegatedPlaygroundAvailable: true });
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(
      await screen.findByText("Connection interrupted"),
    ).toBeInTheDocument();
    expect(screen.getByText("Partial answer")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Stopped before the answer was complete. A partial request may still be billed; check Logs before running it again.",
      ),
    ).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("Try it when delegated calls are available", () => {
  it.each([
    ["says they are", true],
    ["does not say (an older server)", undefined],
  ])("runs as before when the gateway %s", async (_label, available) => {
    post.mockResolvedValue({ data: FULL });
    renderTryIt({ delegatedPlaygroundAvailable: available });
    expect(screen.queryByRole("heading", { name: TITLE })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Prompt" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Partial answer")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
    );
    expect(screen.getByText("4 in · 2 out")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(BILLED);
    expect(post).toHaveBeenCalledTimes(1);
  });
});
