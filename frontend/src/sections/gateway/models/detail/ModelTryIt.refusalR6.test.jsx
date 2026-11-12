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
import { readStreamResult } from "./modelFacts";
import { interpretTestResult } from "../../guardrails/test/interpret-result";
import TestResult from "../../guardrails/test/TestResult";

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

const ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
const row = {
  id: "openai/model-a",
  type: "chat",
  access: ACCESS.MANAGED,
  managedOn: true,
  ownRoutes: [],
  model: {},
};
const cases = [
  ["managed_async_unsupported", "Send the request without x-agentcc-async"],
  ["managed_output_ceiling_required", "Set a positive max_tokens value"],
  ["managed_unsupported_parameter", "omit n/best_of or use 1"],
  [
    "managed_openrouter_streaming_not_yet",
    "explicitly choose your own provider key",
  ],
];
const wire = (code) => ({
  status_code: 400,
  guardrail_headers: {
    "x-agentcc-request-id": ID,
    "x-agentcc-dispatched": "false",
    "Retry-After": "9",
  },
  body: {
    error: {
      code,
      message: "Not charged; private-value; try again in 123456 seconds",
      param: "max_tokens=private-value",
      metadata: {
        dispatched: true,
        request_id: "body-id",
        retry_after: 123456,
      },
    },
  },
});
beforeEach(() => post.mockReset());
afterEach(cleanup);

function assertAlert(text) {
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(text);
  expect(alert).toHaveTextContent(`Request ID: ${ID}`);
  expect(alert).toHaveTextContent("Gateway dispatch: no");
  expect(alert).toHaveTextContent("not retry automatically");
  expect(alert).not.toHaveTextContent(
    /private-value|body-id|123456|not charged|Retry-After:/i,
  );
  expect(within(alert).queryByRole("button")).toBeNull();
  expect(within(alert).queryByRole("link")).toBeNull();
}

describe.each(["json", "stream", "raw", "normalized"])(
  "r7 remedies through %s",
  (transport) => {
    it.each(cases)(
      "shows %s without a replay or billing switch",
      async (code, text) => {
        const result = wire(code);
        if (transport === "raw")
          post.mockRejectedValue({
            response: {
              status: 400,
              data: result.body,
              headers: result.guardrail_headers,
            },
          });
        else if (transport === "normalized")
          post.mockRejectedValue({
            statusCode: 400,
            ...result.body,
            responseHeaders: result.guardrail_headers,
            requestId: "body-id",
            retryAfterSeconds: 123456,
          });
        else if (transport === "stream") {
          const data = `event: result\ndata: ${JSON.stringify(result)}\n\n`;
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
        assertAlert(text);
        expect(post).toHaveBeenCalledTimes(1);
        expect(post.mock.calls[0][1].model).toBe(row.id);
      },
    );
  },
);

describe("r7 Guardrails and stream identity", () => {
  it.each(cases)("shows the same local remedy for %s", (code, text) => {
    const result = interpretTestResult(wire(code));
    render(
      <MemoryRouter>
        <TestResult result={result} />
      </MemoryRouter>,
    );
    assertAlert(text);
    expect(post).not.toHaveBeenCalled();
  });
  it.each(["meta", "done"])(
    "validates %s request IDs instead of trusting body aliases",
    (event) => {
      const data = {
        status_code: 200,
        guardrail_headers: { "x-agentcc-request-id": "header-id" },
        agentcc_metadata: { request_id: ID },
      };
      expect(readStreamResult([{ event, data }]).requestId).toBe("");
      data.guardrail_headers["x-agentcc-request-id"] = ID;
      expect(readStreamResult([{ event, data }]).requestId).toBe(ID);
    },
  );
});
