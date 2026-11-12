import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
const LOGS = "/dashboard/gateway/logs";
// The actual GeneralMethods.bad_request envelope returned by test_playground
// after a GatewayClientError, including a deadline exceeded after dispatch.
const consoleTimeoutBody = {
  status: false,
  type: "validation_error",
  code: "invalid",
  detail: "Gateway request failed. (gateway_error)",
  message: "Gateway request failed. (gateway_error)",
  error: "Gateway request failed. (gateway_error)",
  result: "Gateway request failed. (gateway_error)",
};
const timeoutResult = (headers = {}) => ({
  status_code: 504,
  body: { error: { code: "gateway_timeout", message: "fixture-private-upstream-details" } },
  guardrail_headers: headers,
});

beforeEach(() => post.mockReset());
afterEach(cleanup);

async function run(stream = true) {
  render(<MemoryRouter><ModelTryIt row={row} gatewayId="gw-1" /></MemoryRouter>);
  if (!stream) await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByRole("alert");
  await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
}

function expectUncertainty(href = LOGS) {
  const notice = screen.getByText(/Completion unknown\./);
  expect(notice).toHaveTextContent("may have run and been billed");
  expect(notice).toHaveTextContent("before running it again");
  expect(notice).toHaveTextContent("The console will not retry automatically.");
  expect(screen.getByRole("link", { name: "Check Logs" })).toHaveAttribute("href", href);
  expect(post).toHaveBeenCalledTimes(1);
}

function expectMappedTimeout() {
  const alert = screen.getByRole("alert");
  expect(within(alert).getByText("Gateway request timed out")).toBeInTheDocument();
  expect(alert).toHaveTextContent("HTTP 504");
  expect(alert).toHaveTextContent("Check request status before sending again");
  expect(alert).toHaveTextContent("A timeout alone does not prove that the provider did not process the request.");
  expect(document.body).not.toHaveTextContent("fixture-private-upstream-details");
}

function resolveStream(result) {
  const data = `event: result\ndata: ${JSON.stringify(result)}\n\n`;
  post.mockImplementation(async (_url, _body, options) => {
    options.onDownloadProgress?.({ event: { target: { responseText: data } } });
    return { data };
  });
}

const httpFailure = (status, body, raw) => raw
  ? { response: { status, data: body, headers: {} } }
  : { ...body, statusCode: status, transportCode: "ERR_BAD_REQUEST", responseHeaders: {} };

describe("Try it completion uncertainty", () => {
  it.each(["normalized", "raw"])("keeps billing uncertainty for a non-streaming console relay timeout (%s)", async (shape) => {
    post.mockRejectedValue(httpFailure(400, consoleTimeoutBody, shape === "raw"));
    await run(false);
    expectUncertainty();
    expect(screen.getByRole("alert")).toHaveTextContent(consoleTimeoutBody.message);
    expect(post.mock.calls[0][0]).toBe(endpoints.gateway.testPlayground("gw-1"));
    await userEvent.click(screen.getByText("Request diagnostics"));
    expect(JSON.parse((await screen.findByLabelText("Received request evidence")).textContent)).toEqual({
      outcome: "unknown", status: 400, code: "invalid", message: consoleTimeoutBody.message,
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it.each(["sse", "json", "normalized", "raw"])("keeps the mapped timeout remedy AND Logs recovery without a request ID (%s)", async (transport) => {
    const result = timeoutResult();
    if (transport === "sse") resolveStream(result);
    else if (transport === "json") post.mockResolvedValue({ data: { status: true, result } });
    else post.mockRejectedValue(httpFailure(504, result.body, transport === "raw"));
    await run(transport !== "json");
    expectMappedTimeout();
    expectUncertainty();
    expect(screen.queryByRole("link", { name: "View in logs" })).toBeNull();
    expect(post.mock.calls[0][0]).toBe(transport === "json"
      ? endpoints.gateway.testPlayground("gw-1")
      : endpoints.gateway.testPlaygroundStream("gw-1"));
  });

  it("uses a supplied request ID for the mapped timeout's Logs recovery", async () => {
    const requestId = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
    resolveStream(timeoutResult({ "x-agentcc-request-id": requestId }));
    await run();
    expectMappedTimeout();
    expectUncertainty(`${LOGS}?request_id=${requestId}`);
  });

  it("keeps a definite rate-limit refusal distinct from an uncertain completion", async () => {
    post.mockRejectedValue(httpFailure(429, {
      error: { code: "rate_limit_exceeded", message: "Rate limited." },
    }, false));
    await run();
    expect(screen.getByRole("alert")).toHaveTextContent("Rate limit reached");
    expect(screen.getByRole("alert")).toHaveTextContent("Try again after waiting");
    expect(screen.queryByText(/Completion unknown\./)).toBeNull();
    expect(screen.queryByRole("link", { name: "Check Logs" })).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });
});
