import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderPage } from "../../__tests__/helpers";
import GuardrailTestTab from "../GuardrailTestTab";
import GuardrailManagementSection from "../../GuardrailManagementSection";

const { get, post, navigate, gatewayContext } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  navigate: vi.fn(),
  gatewayContext: { current: {} },
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
  useParams: () => ({ tab: "playground" }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../../../context/useGatewayContext", () => ({
  useGatewayContext: () => gatewayContext.current,
}));

const TITLE = "Guardrail tests are not available in this release";
const BILLED = /billed/;
const SERVER_MESSAGE =
  "Unavailable in this release: Playground, Try-it and Guardrail test will return when delegated calls are available.";
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

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  navigate.mockReset();
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
    if (url.endsWith("/config/")) {
      return Promise.resolve({
        data: { result: { guardrail_pipeline: { enabled: true } } },
      });
    }
    throw new Error(`Unexpected read: ${url}`);
  });
});

function expectUnavailable() {
  expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /^Prompt/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run test" })).toBeNull();
  expect(document.body).not.toHaveTextContent(BILLED);
  expect(document.body).not.toHaveTextContent(/will return when/);
}

describe("the Test tab when this release has no delegated calls", () => {
  it("says so before any prompt and offers Logs instead, sending nothing", async () => {
    const user = userEvent.setup();
    renderPage(
      <GuardrailTestTab
        gatewayId="gw-1"
        delegatedPlaygroundAvailable={false}
      />,
    );
    expectUnavailable();
    expect(
      screen.getByText(
        "Send a request through this gateway with your API key, then see what your rules did in Logs.",
      ),
    ).toBeInTheDocument();
    const action = screen.getByRole("button", { name: "Open Logs" });
    action.focus();
    await user.keyboard("{Enter}");
    expect(navigate).toHaveBeenCalledWith("/dashboard/gateway/guardrails/logs");
    expect(post).not.toHaveBeenCalled();
  });

  it("shows a 733 refusal as that state, not a failed request", async () => {
    const user = userEvent.setup();
    post.mockRejectedValue({
      ...ENVELOPE_733,
      statusCode: 409,
      transportCode: "ERR_BAD_REQUEST",
      responseHeaders: {},
    });
    renderPage(<GuardrailTestTab gatewayId="gw-1" />);
    await user.type(screen.getByLabelText(/^Prompt/), "hello");
    const select = await screen.findByRole("combobox", { name: /^Model/ });
    await waitFor(() =>
      expect(select).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(select);
    await user.click(
      await screen.findByRole("option", {
        name: "team-openai/gpt-4o-mini",
        exact: true,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Run test" }));
    expect(
      await screen.findByRole("heading", { name: TITLE }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(TITLE);
    expectUnavailable();
    expect(screen.queryByText("Request refused")).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("is what the Guardrails page's Test tab shows when the gateway says so", async () => {
    gatewayContext.current = {
      gatewayId: "gw-1",
      gateway: { id: "gw-1", delegatedPlaygroundAvailable: false },
    };
    renderPage(<GuardrailManagementSection />);
    expect(
      await screen.findByRole("tab", { name: "Test", selected: true }),
    ).toBeInTheDocument();
    expectUnavailable();
  });

  it("keeps the Test tab's form when the gateway says delegated calls are available", async () => {
    gatewayContext.current = {
      gatewayId: "gw-1",
      gateway: { id: "gw-1", delegatedPlaygroundAvailable: true },
    };
    renderPage(<GuardrailManagementSection />);
    expect(
      await screen.findByRole("button", { name: "Run test" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Prompt/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: TITLE })).toBeNull();
  });
});
