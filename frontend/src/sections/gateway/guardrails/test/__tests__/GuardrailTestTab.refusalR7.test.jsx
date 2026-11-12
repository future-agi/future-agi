import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MockAgGrid, renderPage } from "../../__tests__/helpers";
import GuardrailTestTab from "../GuardrailTestTab";
import TestResult from "../TestResult";
import { interpretTestResult } from "../interpret-result";
import { parseEvents } from "../../../components/gateway-test-request";
import {
  REMEDIES,
  blockedWire,
  directFailure,
  leakedCopy,
  refusalWire,
} from "../../../utils/__tests__/guardrailRefusalR7Fixtures";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8, currentOrganizationId: "org-1" }),
}));
vi.mock("../../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../../hooks/useGuardrailsOff", () => ({
  useGuardrailsOff: () => false,
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
beforeEach(() => {
  post.mockReset();
  get.mockReset();
  get.mockImplementation((url) => {
    if (url.endsWith("/providers/")) {
      return Promise.resolve({
        data: {
          result: {
            providers: [
              { id: "team-openai", models: ["gpt-4o-mini"], pushed: true },
            ],
          },
        },
      });
    }
    throw new Error(`Unexpected read: ${url}`);
  });
});
afterEach(cleanup);

async function run(result, transport = "json") {
  if (transport === "json") post.mockResolvedValue({ data: { result } });
  else
    post.mockRejectedValue(directFailure(result, transport === "normalized"));
  const user = userEvent.setup();
  const view = renderPage(<GuardrailTestTab gatewayId="gw-1" />);
  await user.type(screen.getByLabelText(/^Prompt/), "A test prompt.");
  const select = await screen.findByRole("combobox", { name: /^Model/ });
  await waitFor(() => expect(select).not.toHaveAttribute("aria-disabled", "true"));
  await user.click(select);
  await user.click(
    await screen.findByRole("option", {
      name: "team-openai/gpt-4o-mini",
      exact: true,
    }),
  );
  await user.click(screen.getByRole("button", { name: "Run test" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Run test" })).toBeEnabled(),
  );
  expect(post).toHaveBeenCalledTimes(1);
  return view;
}

function expectControlled(view, guidance) {
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(guidance);
  expect(alert).toHaveTextContent("not retry automatically");
  // Check the whole page, including outcome, metadata, headers and history.
  expect(view.container).not.toHaveTextContent(leakedCopy);
  expect(screen.queryByText("Blocked (400)")).toBeNull();
  expect(screen.queryByText("Blocked (403)")).toBeNull();
  expect(within(alert).queryByRole("button")).toBeNull();
  expect(within(alert).queryByRole("link")).toBeNull();
}

describe.each(["json", "raw", "normalized"])(
  "r7 Guardrails page body-type conflict via %s",
  (transport) => {
    it.each(REMEDIES)(
      "maps %s and never retries or changes billing",
      async (code, guidance) => {
        const view = await run(refusalWire(code), transport);
        await screen.findByRole("alert");
        expectControlled(view, guidance);
        expect(post.mock.calls[0][1]).toEqual({
          prompt: "A test prompt.",
          model: "team-openai/gpt-4o-mini",
        });
        if (transport === "json") {
          expect(screen.getByText("Error (400)")).toBeInTheDocument();
          expect(
            within(
              screen.getByRole("region", { name: "Recent tests" }),
            ).getByText("Error 400"),
          ).toBeInTheDocument();
        }
      },
    );

    it("does not treat a 403 body type as a block", async () => {
      const view = await run(
        refusalWire("unknown_future_code", { status_code: 403 }),
        transport,
      );
      await screen.findByRole("alert");
      expectControlled(view, "The gateway refused the request.");
    });
  },
);

it.each(REMEDIES)(
  "r7 Guardrails SSE result renderer maps %s",
  (code, guidance) => {
    // The page sends JSON; exercise its shared result renderer with parsed SSE too.
    const result = refusalWire(code);
    const events = parseEvents(
      `event: result\ndata: ${JSON.stringify(result)}\n\n`,
    );
    const view = renderPage(
      <TestResult result={interpretTestResult(events[0].data)} />,
    );
    expectControlled(view, guidance);
    expect(post).not.toHaveBeenCalled();
  },
);

it("r7 Guardrails preserves genuine relay block copy and history", async () => {
  const result = blockedWire();
  result.body.error.type = "permission_error";
  await run(result);
  const region = await screen.findByRole("region", { name: "Result" });
  expect(within(region).getByText("Blocked (403)")).toBeInTheDocument();
  expect(region).toHaveTextContent(
    "Keyword Blocklist refused the prompt or the model's answer with a 403.",
  );
  expect(region).toHaveTextContent("Blocked keywords found: internal only");
  expect(
    within(screen.getByRole("region", { name: "Recent tests" })).getByText(
      "Blocked",
    ),
  ).toBeInTheDocument();
});

it("r7 Guardrails rejects a block flag on a non-403 refusal", async () => {
  const view = await run(
    refusalWire("managed_output_ceiling_required", { blocked: true }),
  );
  await screen.findByRole("alert");
  expectControlled(view, "Set a positive max_tokens value");
});
