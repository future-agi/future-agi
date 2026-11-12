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

import { endpoints } from "src/utils/axios";
import ModelTryIt from "./ModelTryIt";
import { ACCESS } from "../catalogRows";
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

const row = {
  id: "openai/gpt-4o-mini",
  type: "chat",
  access: ACCESS.MANAGED,
  managedOn: true,
  ownRoutes: [],
  model: {},
};
const OPERATOR_REMEDY =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";
const PRIVATE_DETAIL = "fixture-model; policy:fixture-policy; $12.345678";
const FIELD_CASES = [
  ["invalid_provider_preference", "provider.order"],
  ["invalid_provider_preference", "providerOptions.gateway.only"],
  ["invalid_routing_field", "fallbacks"],
  ["unsupported_routing_field", "plugins"],
  ["routing_field_not_yet_supported", "transforms"],
  ["extra_body_conflict", "extra_body.model"],
  ["invalid_extra_body", "extra_body.extra_body"],
];
const routingRefusal = (code, param) => ({
  status_code: 400,
  body: {
    error: {
      code,
      param,
      message: PRIVATE_DETAIL,
      metadata: { param: "models", model: PRIVATE_DETAIL },
    },
  },
});

beforeEach(() => post.mockReset());
afterEach(cleanup);

async function runTryIt(stream = true) {
  render(
    <MemoryRouter>
      <ModelTryIt row={row} gatewayId="gw-1" />
    </MemoryRouter>,
  );
  if (!stream)
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    );
  await userEvent.click(screen.getByRole("button", { name: "Run" }));
  await screen.findByRole("alert");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
  );
}

function expectField(param) {
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(`Field: ${param}.`);
  expect(alert).not.toHaveTextContent(
    /fixture-model|fixture-policy|12\.345678|Field: models\./,
  );
  expect(within(alert).queryByRole("button")).toBeNull();
}

describe("top-level coded 404 replay protection", () => {
  it.each([
    "unknown_future_code",
    "model_not_found",
    "managed_models_disabled",
    "invalid_api_key",
    "",
    null,
    0,
    false,
  ])(
    "does not replay a normalized missing-endpoint shape with code %j",
    async (code) => {
      post.mockRejectedValue({ statusCode: 404, detail: "Not found.", code });
      await runTryIt();
      expect(post).toHaveBeenCalledTimes(1);
      expect(post.mock.calls[0][0]).toBe(
        endpoints.gateway.testPlaygroundStream("gw-1"),
      );
      expect(screen.queryByText(/answer arrived in one piece/)).toBeNull();
    },
  );

  it("retains the uncoded legacy missing-endpoint fallback", async () => {
    post.mockRejectedValueOnce({ statusCode: 404, detail: "Not found." });
    post.mockResolvedValueOnce({
      data: { result: routingRefusal("invalid_routing_field", "models") },
    });
    await runTryIt();
    expect(post.mock.calls.map(([url]) => url)).toEqual([
      endpoints.gateway.testPlaygroundStream("gw-1"),
      endpoints.gateway.testPlayground("gw-1"),
    ]);
    expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
    expect(screen.getByText(/answer arrived in one piece/)).toBeInTheDocument();
  });
});

describe.each([true, false])(
  "operator credentials with streaming=%s",
  (stream) => {
    it.each([
      { message: OPERATOR_REMEDY },
      { message: PRIVATE_DETAIL },
      {
        message: PRIVATE_DETAIL,
        detail: PRIVATE_DETAIL,
        error: { code: "invalid_api_key", message: PRIVATE_DETAIL },
      },
    ])("keeps the fixed operator remedy for %j", async (data) => {
      post.mockRejectedValue({
        message: "Request failed with status code 401",
        response: {
          status: 401,
          data: { ...data, code: "gateway_request_rejected" },
        },
      });
      await runTryIt(stream);
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(OPERATOR_REMEDY);
      expect(alert).not.toHaveTextContent(
        /fixture-model|fixture-policy|12\.345678|Create or rotate|Request failed with status/,
      );
      expect(
        within(alert).queryByRole("link", { name: "Open gateway API keys" }),
      ).toBeNull();
      expect(post).toHaveBeenCalledTimes(1);
    });
  },
);

describe.each(["json", "stream", "shared", "raw"])(
  "routing labels through %s",
  (transport) => {
    it.each(FIELD_CASES)(
      "shows the local field for %s / %s without replay",
      async (code, param) => {
        const result = routingRefusal(code, param);
        if (transport === "shared")
          post.mockRejectedValue({ statusCode: 400, ...result.body });
        else if (transport === "raw")
          post.mockRejectedValue({
            response: { status: 400, data: result.body },
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
        await runTryIt(transport !== "json");
        expectField(param);
        expect(post).toHaveBeenCalledTimes(1);
      },
    );
  },
);

describe("Guardrails routing field labels", () => {
  it.each(FIELD_CASES)(
    "shows the same local field for %s / %s",
    (code, param) => {
      render(
        <MemoryRouter>
          <TestResult
            result={interpretTestResult(routingRefusal(code, param))}
          />
        </MemoryRouter>,
      );
      expectField(param);
      expect(post).not.toHaveBeenCalled();
    },
  );

  it("does not display a field value disguised as a field identifier", () => {
    render(
      <MemoryRouter>
        <TestResult
          result={interpretTestResult(
            routingRefusal(
              "invalid_provider_preference",
              `provider.${PRIVATE_DETAIL}`,
            ),
          )}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      /Field:|fixture-model|fixture-policy|12\.345678/,
    );
    expect(post).not.toHaveBeenCalled();
  });
});
