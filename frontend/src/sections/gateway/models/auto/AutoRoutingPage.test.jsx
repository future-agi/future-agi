import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import { AUTO_LOGS } from "../test-fixtures/autoLogs";
import {
  MANAGED_ON,
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";
import AutoRoutingPage from "./AutoRoutingPage";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// Saving auto routing is Owner or Admin only (useGatewayAdmin); these cases
// act as an Owner.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
  }),
}));
const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

const URL = "/dashboard/gateway/models/auto";

const responses = (overrides = {}) =>
  demoResponses(endpoints, {
    [endpoints.gateway.config("default")]: {
      status: true,
      result: { routing: { auto: { preference: "balanced" } } },
    },
    [endpoints.gateway.requestLogs]: AUTO_LOGS,
    ...overrides,
  });

const open = (r = responses()) => {
  api.get.mockImplementation(answerFrom(r));
  return renderModelsAt(URL, <AutoRoutingPage />);
};

// Every request the page sent must pass the API contract.
afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});

beforeEach(() => {
  api.get.mockReset();
  grid.props = null;
});

describe("auto routing page", () => {
  it("is its own Future AGI page", async () => {
    open();
    const title = await screen.findByRole("heading", {
      level: 1,
      name: "Auto routing",
    });
    expect(window.getComputedStyle(title).fontSize).toBe("0.9375rem");
  });

  // The page reads the latest requests (GET /agentcc/request-logs/, within
  // the API contract, whose query keys the app's contract layer checks) and
  // keeps the auto-routed ones itself.
  it("reads the latest requests within the API contract", async () => {
    open();
    await waitFor(() =>
      expect(
        api.get.mock.calls.find(
          ([url]) => url === endpoints.gateway.requestLogs,
        ),
      ).toBeTruthy(),
    );
    const [url, config] = api.get.mock.calls.find(
      ([u]) => u === endpoints.gateway.requestLogs,
    );
    expect(config.params).toEqual({ limit: 200, ordering: "-started_at" });
    expect(
      validateContractedRequestConfig({ method: "get", url, ...config }).ok,
    ).toBe(true);
  });

  it("keeps only the auto-routed requests and says how many it looked at", async () => {
    open(
      responses({
        [endpoints.gateway.requestLogs]: {
          count: 603,
          results: [
            {
              id: "plain",
              request_id: "r0",
              model: "gpt-4o-mini",
              metadata: {},
            },
            ...AUTO_LOGS.results,
          ],
        },
      }),
    );
    await waitFor(() => expect(grid.props?.rowData).toHaveLength(4));
    expect(
      screen.getByText(/4 of your latest 5 requests were sent as model "auto"/),
    ).toBeInTheDocument();
  });

  // The ranking sentence matches routing/auto.go ChooseAuto: the score starts
  // from how well the price tier fits the task and the preference. It
  // promises no health handling (the latency and error figures are shared per
  // provider id, and a candidate whose calls all failed takes no deduction).
  it("explains what auto does", async () => {
    open();
    const how = await screen.findByRole("region", { name: "How auto chooses" });
    expect(how).toHaveTextContent(
      "Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
    );
    expect(how).toHaveTextContent(
      /next two models in the ranking become fallbacks/,
    );
    expect(how).toHaveTextContent(
      "plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on",
    );
    expect(how).not.toHaveTextContent(/5%|Recent calls|failing/);
    expect(how).not.toHaveTextContent(/preview/i);
  });

  it("shows the models auto chose most often", async () => {
    open();
    const share = await screen.findByRole("region", {
      name: "Chosen most often",
    });
    await waitFor(() =>
      expect(share).toHaveTextContent("mistralai/ministral-8b-2512"),
    );
    expect(share).toHaveTextContent("75%");
    expect(share).toHaveTextContent(/last 4 auto-routed requests/);
  });

  it("lists recent decisions and shows the candidates of the selected one", async () => {
    open();
    await waitFor(() => expect(grid.props?.rowData).toHaveLength(4));
    const candidates = await screen.findByRole("region", {
      name: "Candidates",
    });
    // The latest decision is selected first.
    expect(
      within(candidates).getByRole("table", { name: "Ranked candidates" }),
    ).toHaveTextContent("mistralai/ministral-14b-2512");
    expect(candidates).toHaveTextContent("Top 5 of 64 considered");
    // Ten models set aside for one reason read as one line, not ten.
    const setAside = within(candidates).getByRole("table", {
      name: "Set aside",
    });
    expect(setAside.rows).toHaveLength(1);
    expect(setAside.rows[0]).toHaveTextContent(
      "Your organization's Future AGI model policy does not allow it",
    );
    expect(setAside.rows[0]).toHaveTextContent("10 models");
    expect(setAside.rows[0]).toHaveTextContent("amazon/nova-2-lite-v1");
    expect(setAside.rows[0]).toHaveTextContent("deepseek/deepseek-v4.1-flash");
    act(() => grid.props.onRowClicked({ data: grid.props.rowData[1] }));
    await waitFor(() =>
      expect(
        within(candidates).getByRole("table", { name: "Ranked candidates" })
          .rows[1],
      ).toHaveTextContent("mistralai/ministral-8b-2512"),
    );
  });

  // A ranked candidate opens its model page (Overview, Try it, API) where it
  // has one: the catalog's page here. One neither the catalog nor any of the
  // org's providers has is shown without a link.
  it("links each ranked candidate that has a model page to it", async () => {
    open();
    const candidates = await screen.findByRole("region", {
      name: "Candidates",
    });
    const ranked = within(candidates).getByRole("table", {
      name: "Ranked candidates",
    });
    await waitFor(() =>
      expect(
        within(ranked).getByRole("link", {
          name: "anthropic/claude-haiku-5.5",
        }),
      ).toHaveAttribute(
        "href",
        "/dashboard/gateway/models/anthropic/claude-haiku-5.5",
      ),
    );
    expect(
      within(ranked).getByText("mistralai/ministral-14b-2512"),
    ).toBeTruthy();
    expect(
      within(ranked).queryByRole("link", {
        name: "mistralai/ministral-14b-2512",
      }),
    ).toBeNull();
  });

  // With managed models off there is no catalog: an own-key candidate (the
  // model its provider lists, and that provider) opens
  // /models/<provider>/<model>, and a model no provider of the org serves
  // has no link.
  it("links own-key candidates to their pages where no managed models are offered", async () => {
    const decision = {
      model: "gpt-4o-mini",
      provider: "openai",
      task_tier: "simple",
      preference: "balanced",
      considered: 4,
      ranked: [
        { model: "gpt-4o-mini", provider: "openai", tier: 0.1, score: 0.9 },
        { model: "gpt-4o", provider: "flaky-upstream", tier: 0.2, score: 0.8 },
        { model: "llama-3", provider: "openai", tier: 0.2, score: 0.7 },
        {
          model: "anthropic/claude-haiku-5.5",
          provider: "openrouter",
          tier: 0.1,
          score: 0.6,
        },
      ],
      rejected: [],
      fallbacks: [],
    };
    open(
      responses({
        [endpoints.gateway.managedModels]: {
          status: true,
          result: { available: false, enabled: false },
        },
        [endpoints.gateway.requestLogs]: {
          count: 1,
          results: [
            {
              id: "own-1",
              request_id: "01OWNKEYAUTO",
              model: "gpt-4o-mini",
              provider: "openai",
              started_at: "2026-10-24T10:00:00Z",
              latency_ms: 12,
              cost: "0.000001",
              status_code: 200,
              is_error: false,
              metadata: {
                auto_model: "gpt-4o-mini",
                auto_requested: "auto",
                auto_task_tier: "simple",
                auto_preference: "balanced",
                auto_considered: "4",
                auto_decision: JSON.stringify(decision),
                billing_mode: "byok",
              },
            },
          ],
        },
      }),
    );
    const candidates = await screen.findByRole("region", {
      name: "Candidates",
    });
    const ranked = within(candidates).getByRole("table", {
      name: "Ranked candidates",
    });
    await waitFor(() =>
      expect(
        within(ranked).getByRole("link", { name: "gpt-4o-mini" }),
      ).toHaveAttribute("href", "/dashboard/gateway/models/openai/gpt-4o-mini"),
    );
    expect(
      within(ranked).getByRole("link", { name: "gpt-4o" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/flaky-upstream/gpt-4o",
    );
    expect(within(ranked).queryByRole("link", { name: "llama-3" })).toBeNull();
    expect(
      within(ranked).queryByRole("link", {
        name: "anthropic/claude-haiku-5.5",
      }),
    ).toBeNull();
    expect(within(ranked).getAllByRole("link")).toHaveLength(2);
  });

  it("says when no request has used auto yet", async () => {
    open(
      responses({
        [endpoints.gateway.requestLogs]: { count: 0, results: [] },
      }),
    );
    expect(
      await screen.findByText("No auto-routed requests yet"),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/"model": "auto"/).length).toBeGreaterThan(0);
  });

  it("shows a retryable error when the logs cannot load", async () => {
    open(
      responses({
        [endpoints.gateway.requestLogs]: new Error("timeout"),
      }),
    );
    expect(
      await screen.findByText("Couldn't load recent auto decisions"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  // Without the managed catalog the candidate field offers only
  // the org's providers. The page says so once, above Settings, with Retry,
  // and raises no app-wide toast (the status read is errorHandled).
  it("says once, with Retry, when the managed catalog cannot be read", async () => {
    const r = responses({
      [endpoints.gateway.managedModels]: new Error("Request failed (500)"),
    });
    open(r);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load the Future AGI catalogue");
    expect(alert).toHaveTextContent(/your providers' models only/);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    r[endpoints.gateway.managedModels] = { status: true, result: MANAGED_ON };
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        screen.queryByText(/Couldn't load the Future AGI catalogue/),
      ).toBeNull(),
    );
  });

  it("says the same when the status answers but the catalog does not", async () => {
    open(
      responses({
        [endpoints.gateway.managedModelsCatalog]: new Error("timeout"),
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't load the Future AGI catalogue",
    );
  });

  it("keeps the settings: preference, candidates and price caps", async () => {
    open();
    expect(
      await screen.findByRole("button", { name: /^Balanced/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Candidate models")).toBeInTheDocument();
    expect(screen.getByLabelText("Max input price")).toBeInTheDocument();
  });

  it("shows how to call auto, with the preference header", async () => {
    open();
    expect(
      (await screen.findAllByText(/"model": "auto"/)).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/x-agentcc-auto-preference/).length,
    ).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Python" }));
    expect(screen.getByText(/model="auto"/)).toBeInTheDocument();
  });
});
