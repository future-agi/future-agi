import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import ModelTryIt from "./ModelTryIt";
import { ACCESS } from "../catalogRows";
import { readTryResult, readStreamResult } from "./modelFacts";
import { interpretTestResult } from "../../guardrails/test/interpret-result";
import TestResult from "../../guardrails/test/TestResult";
import {
  sendGatewayTest,
  streamGatewayTest,
} from "../../components/gateway-test-request";

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
const cases = [
  {
    name: "spend unproven",
    status: 429,
    code: "managed_spend_velocity",
    text: "Review the spending limit with an administrator",
    metadata: {},
  },
  {
    name: "spend full window",
    status: 429,
    code: "managed_spend_velocity",
    text: "same billing source",
    metadata: { remedy_hint: "retry" },
  },
  {
    name: "enforceable cap",
    status: 402,
    code: "call_cap_unenforceable",
    text: "identity, route or cap policy",
    metadata: {},
  },
  {
    name: "budget unproven",
    status: 503,
    code: "budget_state_unavailable",
    text: "missing activation",
    metadata: {},
  },
  {
    name: "budget transient",
    status: 503,
    code: "budget_state_unavailable",
    text: "If it still fails, stop",
    metadata: { reason: "redis_unavailable", remedy_hint: "retry" },
  },
  {
    name: "no code",
    status: 503,
    text: "The gateway refused the request.",
    metadata: { dispatched: false, remedy_hint: "retry" },
  },
  {
    name: "forward unpriced",
    status: 403,
    code: "managed_model_unpriced",
    text: "Choose a priced model",
    metadata: { reason: "unpriced" },
  },
  {
    name: "forward ambiguous",
    status: 400,
    code: "managed_model_ambiguous",
    text: "Choose an exact provider-prefixed model ID",
    metadata: {
      candidates: [
        "openai/model-a",
        "vendor-b/model-b",
        "<script>bad</script>",
      ],
    },
  },
];
const wire = ({ status, code, metadata }) => ({
  status_code: status,
  guardrail_headers: {
    "Retry-After": "9",
    "x-agentcc-dispatched": "false",
    "x-agentcc-request-id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "x-agentcc-generation-id": "unrelated-generation-id",
  },
  body: {
    error: {
      code,
      metadata: { ...metadata, dispatched: true, request_id: "metadata-id" },
      message: "Untrusted server prose",
    },
  },
});
const streamData = (result) =>
  `event: result\ndata: ${JSON.stringify(result)}\n\n`;

beforeEach(() => post.mockReset());
afterEach(cleanup);

async function run(transport, result) {
  if (transport === "raw")
    post.mockRejectedValue({
      response: {
        status: result.status_code,
        data: result.body,
        headers: result.guardrail_headers,
      },
    });
  else if (transport === "shared")
    post.mockRejectedValue({
      statusCode: result.status_code,
      ...result.body,
      responseHeaders: result.guardrail_headers,
    });
  else if (transport === "stream") {
    const data = streamData(result);
    post.mockImplementation(async (_url, _body, options) => {
      options.onDownloadProgress?.({
        event: { target: { responseText: data } },
      });
      return { data };
    });
  } else post.mockResolvedValue({ data: { result } });
  render(
    <MemoryRouter>
      <ModelTryIt row={row} gatewayId="gw-1" />
    </MemoryRouter>,
  );
  if (transport === "json")
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    );
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByRole("alert");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
  );
}

function assertAlert(testCase) {
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(testCase.text);
  expect(alert).toHaveTextContent("Request ID: 01ARZ3NDEKTSV4RRFFQ69G5FAV");
  expect(alert).toHaveTextContent("Gateway dispatch: no");
  expect(alert).not.toHaveTextContent(
    /metadata-id|Untrusted|<script>|unrelated-generation-id/,
  );
  expect(within(alert).queryByRole("button")).toBeNull();
  expect(within(alert).queryByRole("link")).toBeNull();
  if (testCase.name === "forward ambiguous") {
    expect(
      within(alert)
        .getAllByRole("listitem")
        .map((node) => node.textContent),
    ).toEqual(["openai/model-a", "vendor-b/model-b"]);
  }
}

describe.each(["json", "stream", "raw", "shared"])(
  "round-five refusals through %s",
  (transport) => {
    it.each(cases)(
      "renders $name without automatic replay or billing-source switch",
      async (testCase) => {
        await run(transport, wire(testCase));
        assertAlert(testCase);
        expect(post).toHaveBeenCalledTimes(1);
        expect(post.mock.calls[0][1].model).toBe(row.id);
      },
    );
  },
);

describe("Guardrails uses the same refusal mapping", () => {
  it.each(cases)(
    "renders $name with the upstream request ID, not a generation ID",
    (testCase) => {
      const result = interpretTestResult(wire(testCase));
      expect(result.requestId).toBe("01ARZ3NDEKTSV4RRFFQ69G5FAV");
      render(
        <MemoryRouter>
          <TestResult result={result} />
        </MemoryRouter>,
      );
      assertAlert(testCase);
      expect(post).not.toHaveBeenCalled();
    },
  );

  it("keeps absent header evidence unknown and does not display body IDs", () => {
    const result = wire(cases[5]);
    result.guardrail_headers = {};
    const interpreted = interpretTestResult(result);
    expect(interpreted.requestId).toBeNull();
    render(
      <MemoryRouter>
        <TestResult result={interpreted} />
      </MemoryRouter>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Gateway dispatch: unknown");
    expect(alert).not.toHaveTextContent(/Request ID:|metadata-id/);
  });
});

describe("response-boundary evidence survives transport without inference", () => {
  it.each(["json", "stream"])(
    "preserves header evidence across one %s request",
    async (transport) => {
      const original = wire(cases[7]);
      let result;
      if (transport === "json") {
        post.mockResolvedValue({ data: { result: original } });
        result = await sendGatewayTest("gw-1", { prompt: "Hi" });
      } else {
        const data = streamData(original);
        const onEvents = vi.fn();
        post.mockImplementation(async (_url, _body, options) => {
          options.onDownloadProgress({
            event: { target: { responseText: data } },
          });
          return { data };
        });
        const events = await streamGatewayTest(
          "gw-1",
          { prompt: "Hi" },
          { onEvents },
        );
        result = events[0].data;
        expect(onEvents.mock.calls[0][0][0].data.refusal).toEqual(
          result.refusal,
        );
        expect(readStreamResult(events, { finished: true }).requestId).toBe(
          "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        );
      }
      expect(result.refusal.evidence).toEqual({
        dispatched: false,
        requestId: "01ARZ3NDEKTSV4RRFFQ69G5FAV",
      });
      expect(readTryResult(result).requestId).toBe(
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",
      );
      expect(result.body).toEqual(original.body);
      expect(original).not.toHaveProperty("refusal");
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it("never substitutes streamed body metadata for the response request ID", () => {
    const done = {
      status_code: 200,
      agentcc_metadata: { request_id: "body-id" },
      guardrail_headers: {
        "x-agentcc-request-id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
      },
    };
    expect(readStreamResult([{ event: "done", data: done }]).requestId).toBe(
      "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    );
    done.guardrail_headers = {};
    expect(readStreamResult([{ event: "done", data: done }]).requestId).toBe(
      "",
    );
  });
});
