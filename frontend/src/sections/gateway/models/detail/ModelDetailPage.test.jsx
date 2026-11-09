import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";

import {
  MANAGED_ON,
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";
import { GPT_4O_LOGS, fakeRequestLogs } from "../test-fixtures/requestLogs";
import ModelDetailPage from "./ModelDetailPage";
import { CATALOG } from "../test-fixtures/catalog";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
// Try it runs through the gateway's admin path: Owner or Admin only
// (useGatewayAdmin). These cases act as an Owner unless they say otherwise.
const org = vi.hoisted(() => ({ level: 15 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
  }),
}));
// The grid renders its rows and cells (formatters and cell renderers
// applied), so a test reads the fact tables as a user would.
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  const { default: PropTypes } = await import("prop-types");
  function MockGrid({ columnDefs, ...props }) {
    return (
      <>
        <div>
          {columnDefs.map((col) => (
            <div role="columnheader" key={col.field || col.colId}>
              {col.headerName}
            </div>
          ))}
        </div>
        <MockAgGrid columnDefs={columnDefs} {...props} />
      </>
    );
  }
  MockGrid.propTypes = {
    columnDefs: PropTypes.arrayOf(PropTypes.object).isRequired,
  };
  return { AgGridReact: MockGrid };
});

const TRAFFIC = {
  status: true,
  result: {
    models: [
      {
        model: "gpt-4o",
        provider: "flaky-upstream",
        request_count: 160,
        error_rate: 7.5,
        p50_latency_ms: 4,
        p95_latency_ms: 1502,
      },
      {
        model: "openai/gpt-4o",
        provider: "openrouter",
        request_count: 6,
        error_rate: 0,
        p50_latency_ms: 8,
        p95_latency_ms: 32,
      },
      {
        model: "gpt-4o-mini",
        provider: "openai",
        request_count: 289,
        error_rate: 14.53,
        p50_latency_ms: 2,
        p95_latency_ms: 59,
      },
    ],
  },
};

const open = (path, overrides = {}) => {
  api.get.mockImplementation(
    answerFrom(
      demoResponses(endpoints, {
        [endpoints.gateway.analyticsModels]: TRAFFIC,
        ...overrides,
      }),
    ),
  );
  return renderModelsAt(path, <ModelDetailPage />);
};

const HAIKU = "/dashboard/gateway/models/anthropic/claude-haiku-5.5";

// Every request the page sent must pass the API contract.
afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  org.level = 15;
});

describe("model page", () => {
  it("opens from its deep link with the model's name, id and a way back", async () => {
    open(HAIKU);
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Claude Haiku 5.5",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("anthropic/claude-haiku-5.5").length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Catalog/ })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models",
    );
    expect(
      screen.getByRole("tab", { name: "Overview", selected: true }),
    ).toBeInTheDocument();
  });

  it("opens the Try it and API tabs from ?tab=, and keeps the tab in the URL", async () => {
    const { location } = open(`${HAIKU}?tab=api`);
    expect(
      await screen.findByRole("tab", { name: "API", selected: true }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Try it" }));
    await waitFor(() => expect(location.search).toBe("?tab=try"));
    await userEvent.click(screen.getByRole("tab", { name: "Overview" }));
    await waitFor(() => expect(location.search).toBe(""));
  });

  it("decodes an encoded model segment", async () => {
    open("/dashboard/gateway/models/cohere/north-mini-code%3Afree");
    expect(
      await screen.findByRole("heading", { level: 1, name: /North Mini Code/ }),
    ).toBeInTheDocument();
  });

  it("prices the model at the provider's price plus the 5% markup, tiers included", async () => {
    const { container } = open(HAIKU);
    const pricing = await screen.findByRole("region", { name: "Pricing" });
    const row = (label) =>
      within(pricing).getByRole("row", {
        name: new RegExp(`^${label} \\$`),
      });
    expect(row("Input")).toHaveTextContent("$0.10");
    expect(row("Input")).toHaveTextContent("$0.105");
    expect(row("Output")).toHaveTextContent("$0.525");
    expect(row("Cached input")).toHaveTextContent("$0.0105");
    expect(row("Input above 100K prompt tokens")).toHaveTextContent("$0.525");
    expect(
      within(pricing).getByText("Future AGI price (provider + 5%)"),
    ).toBeInTheDocument();
    expect(
      within(pricing).getByText(
        /Requests on Future AGI credits are billed the Future AGI price/,
      ),
    ).toBeInTheDocument();
    expect(pricing).not.toHaveTextContent(/managed/i);
    expect(container).not.toHaveTextContent(/managed/i);
    expect(document.body).not.toHaveTextContent(/managed/i);
  });

  it("lists how the gateway reaches it: managed and the org's own key", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o");
    const routes = await screen.findByRole("region", { name: "Routes" });
    // The managed route is Future AGI's,
    // and its upstream reseller is not named.
    expect(within(routes).getByText("Future AGI")).toBeInTheDocument();
    expect(
      within(routes).getByText(/^\d+ upstream endpoints?$/),
    ).toBeInTheDocument();
    expect(within(routes).queryByText(/openrouter|via /)).toBeNull();
    expect(
      within(routes).getByText("OpenAI (local mock LLM)"),
    ).toBeInTheDocument();
    expect(within(routes).getAllByText("gpt-4o").length).toBeGreaterThan(0);
    expect(within(routes).getAllByText("openai/gpt-4o").length).toBeGreaterThan(
      0,
    );
  });

  // AG Grid cells do not wrap, so a long model id ran on
  // under the Billing cell. Every Routes cell wraps inside its own column.
  it("Send model cell wraps like Route and Billing", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o");
    const routes = await screen.findByRole("region", { name: "Routes" });
    const cells = await within(routes).findAllByRole("gridcell");
    const byCol = (col) => cells.filter((c) => c.dataset.col === col);
    expect(byCol("sendAs").length).toBeGreaterThan(0);
    for (const col of ["label", "sendAs", "billing"]) {
      for (const cell of byCol(col)) {
        expect(cell.firstElementChild).toHaveStyle({
          whiteSpace: "normal",
          minWidth: "0",
        });
      }
    }
  });

  it("shows live health from this organization's own requests, never a provider 'healthy' chip", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o", {
      [endpoints.gateway.requestLogs]: fakeRequestLogs(GPT_4O_LOGS),
    });
    const health = await screen.findByRole("region", { name: "Live health" });
    // Managed copy (openrouter) and the own-key copy the openai provider
    // serves; flaky-upstream's gpt-4o is not matched to this catalog model.
    const managed = await within(health).findByRole("row", {
      name: /^Future AGI /,
    });
    expect(managed).toHaveTextContent("6");
    expect(managed).toHaveTextContent("0%");
    expect(managed).toHaveTextContent("32 ms");
    expect(screen.queryByText(/healthy/i)).toBeNull();
  });

  // Two providers serve gpt-4o. analytics/model-comparison groups by model and
  // names only the busiest provider (flaky-upstream here), so it cannot say
  // what the OpenAI key route served. Each route reads its own requests.
  it("gives each route its own traffic when more than one provider serves the model", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o", {
      [endpoints.gateway.requestLogs]: fakeRequestLogs(GPT_4O_LOGS),
    });
    const health = await screen.findByRole("region", { name: "Live health" });
    const own = await within(health).findByRole("row", {
      name: /OpenAI \(local mock LLM\)/,
    });
    const cells = within(own).getAllByRole("gridcell");
    expect(cells.map((c) => c.textContent)).toEqual([
      "OpenAI (local mock LLM)",
      "5",
      "20%",
      "120 ms",
      "400 ms",
      "Open logs",
    ]);
    const managed = within(health).getByRole("row", {
      name: /^Future AGI /,
    });
    expect(
      within(managed)
        .getAllByRole("gridcell")
        .map((c) => c.textContent),
    ).toEqual(["Future AGI", "6", "0%", "8 ms", "32 ms", "Open logs"]);
    // flaky-upstream's requests belong to no route of this catalog model.
    expect(within(health).queryByText(/flaky/i)).toBeNull();
    expect(within(health).getAllByRole("row")).toHaveLength(2);
    expect(
      api.get.mock.calls.some(
        ([url]) => url === endpoints.gateway.analyticsModels,
      ),
    ).toBe(false);
  });

  // Each route's "Open logs" opens Logs on that route's requests
  // (its model and provider, as the health figures count them); the
  // section's link opens every route's requests of the model.
  it("opens Logs filtered to each route's model and provider", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o", {
      [endpoints.gateway.requestLogs]: fakeRequestLogs(GPT_4O_LOGS),
    });
    const health = await screen.findByRole("region", { name: "Live health" });
    const query = (link) =>
      Object.fromEntries(
        new URL(link.getAttribute("href"), "http://x").searchParams,
      );
    const managed = await within(health).findByRole("row", {
      name: /^Future AGI /,
    });
    const managedLink = within(managed).getByRole("link", {
      name: "Open logs",
    });
    expect(new URL(managedLink.href).pathname).toBe("/dashboard/gateway/logs");
    // The managed route's requests are the model's managed requests: Logs
    // opens on Billing: Managed, never on the upstream's name.
    expect(query(managedLink)).toEqual({
      model: "openai/gpt-4o",
      billing_mode: "managed",
    });
    const own = within(health).getByRole("row", { name: /OpenAI \(local/ });
    expect(query(within(own).getByRole("link", { name: "Open logs" }))).toEqual(
      { model: "gpt-4o,openai/gpt-4o", provider: "openai" },
    );
    const all = within(health)
      .getAllByRole("link", { name: "Open logs" })
      .find((link) => !link.closest('[role="row"]'));
    expect(query(all)).toEqual({ model: "openai/gpt-4o,gpt-4o" });
  });

  // The fact tables are Future AGI tables (AG Grid on the FA
  // theme, the header the other gateway grids have), not MUI tables.
  it("draws Pricing, Routes and Live health as Future AGI grids", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o", {
      [endpoints.gateway.requestLogs]: fakeRequestLogs(GPT_4O_LOGS),
    });
    for (const name of ["Pricing", "Routes", "Live health"]) {
      const region = await screen.findByRole("region", { name });
      expect(
        await within(region).findByTestId("gateway-grid"),
      ).toBeInTheDocument();
      expect(within(region).queryByRole("table")).toBeNull();
    }
  });

  it("says when this model's traffic cannot be read", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o", {
      [endpoints.gateway.requestLogs]: new Error("timeout"),
    });
    expect(
      await screen.findByText(/Couldn't load this model's traffic/),
    ).toBeInTheDocument();
  });

  it("says when the model had no requests", async () => {
    open(HAIKU);
    expect(
      await screen.findByText(/No requests to this model in the last 24 hours/),
    ).toBeInTheDocument();
  });

  // Managed off, but the org's own key serves the model: the chip says what
  // can be called, not "Managed + own key".
  it("says only the own key serves the model while managed models are off", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o", {
      [endpoints.gateway.managedModels]: {
        status: true,
        result: { ...MANAGED_ON, enabled: false },
      },
    });
    expect(
      await screen.findByText("Own key · Future AGI credits off"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Managed + own key")).toBeNull();
  });

  it("flags a retiring model", async () => {
    open("/dashboard/gateway/models/google/gemini-2.5-flash");
    expect(await screen.findByText("Retires 2026-10-20")).toBeInTheDocument();
  });

  // A model managed credits cannot buy shows why, never a markup.
  it("is honest about a model it cannot call", async () => {
    open("/dashboard/gateway/models/black-forest-labs/flux-3-image");
    expect(await screen.findByText("Not callable")).toBeInTheDocument();
    expect(
      screen.getAllByText(/Future AGI credits pay for chat models only/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/\+ 5%/)).toBeNull();
    expect(screen.queryByText(/Turn on managed models/i)).toBeNull();
  });

  // A deployment that offers no managed models has no catalog: a deep link
  // to a model (futureagi.com/models) says how to call it, never refuses.
  it("points a deployment without managed models at Providers for a model link", async () => {
    open(HAIKU, {
      [endpoints.gateway.managedModels]: {
        status: true,
        result: { ...MANAGED_ON, available: false },
      },
    });
    expect(
      await screen.findByText("Call it with your own provider key"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "No provider of your organization serves anthropic/claude-haiku-5.5. Add a provider that serves it under Providers, then send its model id with one of your API keys.",
      ),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/managed/i);
    expect(
      screen.getByRole("button", { name: "Go to Providers" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No managed catalog/)).toBeNull();
    expect(
      api.get.mock.calls.some(
        ([url]) => url === endpoints.gateway.managedModelsCatalog,
      ),
    ).toBe(false);
  });

  it("explains a model this catalog does not list", async () => {
    open("/dashboard/gateway/models/nope/nothing");
    expect(
      await screen.findByText("This model is not in the catalog"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Back to the catalog" }),
    ).toBeInTheDocument();
  });

  it("shows a retryable error when the catalog cannot load", async () => {
    open(HAIKU, {
      [endpoints.gateway.managedModelsCatalog]: new Error(
        "Gateway error: down",
      ),
    });
    expect(
      await screen.findByText("Couldn't load this model"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("model page: API tab", () => {
  it("S053 states the disabled request is refused without promising Catalog activation", async () => {
    open(`${HAIKU}?tab=api`, {
      [endpoints.gateway.managedModels]: {
        status: true,
        result: { ...MANAGED_ON, enabled: false },
      },
    });
    expect(
      await screen.findByText(
        "Future AGI credits are off for your organization, so the gateway refuses this request.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Call this model")).toBeInTheDocument();
    expect(screen.queryByText(/turned on in the Catalog page/)).toBeNull();
  });

  it("gives copy-ready requests for the exact model, with a key placeholder and a Create key link", async () => {
    open(`${HAIKU}?tab=api`);
    // The request and the headers example both carry the exact id.
    expect(
      await screen.findAllByText(/"model": "anthropic\/claude-haiku-5.5"/),
    ).toHaveLength(2);
    expect(screen.getAllByText(/\$AGENTCC_API_KEY/).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Create key" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/keys?create=1",
    );
    expect(
      screen.getAllByText(/http:\/\/localhost:28490\/v1/).length,
    ).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "TypeScript" }));
    expect(screen.getByText(/import OpenAI from "openai"/)).toBeInTheDocument();
  });

  it("switches to a streaming request", async () => {
    open(`${HAIKU}?tab=api`);
    await screen.findAllByText(/"model": "anthropic\/claude-haiku-5.5"/);
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the response" }),
    );
    expect(screen.getByText(/"stream": true/)).toBeInTheDocument();
  });

  it("names the own-key id for a model the org's provider also serves", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o?tab=api");
    expect(
      await screen.findByText(
        /To use your OpenAI \(local mock LLM\) key instead, send model/,
      ),
    ).toBeInTheDocument();
  });

  it("offers no request for a model it cannot call, and points to Providers", async () => {
    open("/dashboard/gateway/models/alibaba/happyhorse-1.0?tab=api");
    expect(
      (await screen.findAllByText(/does not serve video generation/)).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/chat\/completions/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Go to Providers" }),
    ).toBeInTheDocument();
  });
});

describe("model page: Try it tab", () => {
  const RESULT = {
    status: true,
    result: {
      status_code: 200,
      model: "anthropic/claude-haiku-5.5",
      blocked: false,
      body: {
        choices: [{ message: { role: "assistant", content: "echo: Hello" } }],
        usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 },
      },
      guardrail_headers: {
        "X-Agentcc-Request-Id": "01M5DBMGBH3G18SMMM8XG9F2M5",
        "x-agentcc-cost": "0.000002",
        "x-agentcc-latency-ms": "8",
        "x-agentcc-billing-mode": "managed",
      },
    },
  };

  it("sends one prompt through the gateway and shows answer, tokens, cost, latency and request id", async () => {
    api.post.mockResolvedValue({ data: RESULT });
    open(`${HAIKU}?tab=try`);
    const prompt = await screen.findByRole("textbox", { name: "Prompt" });
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "Hello");
    await userEvent.type(
      screen.getByRole("textbox", { name: "System prompt (optional)" }),
      "Be brief",
    );
    // The answer in one piece: the streamed request is the default.
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe(
      endpoints.gateway.testPlayground("default"),
    );
    expect(api.post.mock.calls[0][1]).toEqual({
      model: "anthropic/claude-haiku-5.5",
      prompt: "Hello",
      system_prompt: "Be brief",
    });
    expect(await screen.findByText("echo: Hello")).toBeInTheDocument();
    expect(screen.getByText("8 ms")).toBeInTheDocument();
    expect(screen.getByText("7 in · 7 out")).toBeInTheDocument();
    expect(screen.getByText("$0.000002")).toBeInTheDocument();
    expect(screen.getByText("01M5DBMGBH3G18SMMM8XG9F2M5")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs?request_id=01M5DBMGBH3G18SMMM8XG9F2M5",
    );
  });

  // The Prompts model list is the platform catalog, searched the way the
  // handoff searches it (the id after its first "/").
  const PROMPTS_GPT_4O_MINI = {
    model_name: "gpt-4o-mini",
    providers: "openai",
    is_available: true,
    logo_url: "",
    type: "chat",
  };

  it("carries the model and prompt into Prompts for anything more", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o-mini?tab=try", {
      [endpoints.develop.modelList]: { results: [PROMPTS_GPT_4O_MINI] },
    });
    const prompt = await screen.findByRole("textbox", { name: "Prompt" });
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "Hi there");
    const link = await screen.findByRole("link", { name: "Open in Prompts" });
    const url = new URL(link.getAttribute("href"), "http://x");
    expect(url.pathname).toBe("/dashboard/gateway/playground");
    expect(url.searchParams.get("model")).toBe("openai/gpt-4o-mini");
    expect(url.searchParams.get("prompt")).toBe("Hi there");
    expect(
      api.get.mock.calls.find(([u]) => u === endpoints.develop.modelList)[1],
    ).toMatchObject({ params: { page: 1, search: "gpt-4o-mini" } });
    expect(screen.queryByText(/Prompts does not list/)).toBeNull();
  });

  it("offers Open in Prompts only once Prompts says it lists the model", async () => {
    let answer;
    open("/dashboard/gateway/models/openai/gpt-4o-mini?tab=try", {
      [endpoints.develop.modelList]: () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    await screen.findByRole("textbox", { name: "Prompt" });
    expect(screen.queryByRole("link", { name: /Prompts$/ })).toBeNull();
    answer({ results: [PROMPTS_GPT_4O_MINI] });
    expect(
      await screen.findByRole("link", { name: "Open in Prompts" }),
    ).not.toHaveAttribute("aria-disabled", "true");
  });

  // A model the Prompts list does not know cannot run in Prompts: the page
  // offers no handoff for it (a Prompts draft would open with no model).
  it("offers no Prompts handoff when Prompts does not list the model", async () => {
    open(`${HAIKU}?tab=try`, {
      [endpoints.develop.modelList]: { results: [] },
    });
    const prompt = await screen.findByRole("textbox", { name: "Prompt" });
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "Hi there");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("link", { name: /Copy prompt to Prompts/ }),
    ).toBeNull();
    expect(screen.queryByRole("link", { name: "Open in Prompts" })).toBeNull();
    expect(screen.queryByText(/Copy prompt/)).toBeNull();
  });

  // The streamed answer: server-sent events read as they arrive (axios
  // onDownloadProgress), then the totals from the stream's last chunk.
  const sse = (event, data) =>
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const META = {
    status_code: 200,
    model: "openai/gpt-4o-mini",
    guardrail_headers: { "X-Agentcc-Request-Id": "req_stream_1" },
    blocked: false,
    warned: false,
  };
  const PARTIAL = sse("meta", META) + sse("delta", { content: "The sea " });
  const FULL =
    PARTIAL +
    sse("delta", { content: "is wide." }) +
    sse("done", {
      ...META,
      usage: { prompt_tokens: 12, completion_tokens: 4 },
      agentcc_metadata: {
        cost: 0.000021,
        cost_source: "catalog",
        latency_ms: 412,
        request_id: "req_stream_1",
        billing_mode: "managed",
      },
      finish_reason: "stop",
    });

  it("streams the answer as it arrives, then shows tokens, cost, latency and request id", async () => {
    let finish;
    api.post.mockImplementation((url, body, config) => {
      config.onDownloadProgress({
        event: { target: { responseText: PARTIAL } },
      });
      return new Promise((resolve) => {
        finish = () => resolve({ data: FULL });
      });
    });
    open("/dashboard/gateway/models/openai/gpt-4o-mini?tab=try");
    const prompt = await screen.findByRole("textbox", { name: "Prompt" });
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "Hello");
    expect(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    ).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe(
      endpoints.gateway.testPlaygroundStream("default"),
    );
    // Part of the answer, before the request has finished.
    expect(await screen.findByText("The sea")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    finish();
    expect(await screen.findByText("The sea is wide.")).toBeInTheDocument();
    expect(screen.getByText("412 ms")).toBeInTheDocument();
    expect(screen.getByText("12 in · 4 out")).toBeInTheDocument();
    expect(screen.getByText("$0.000021")).toBeInTheDocument();
    // The Try it result names the billing mode by its one name.
    const billed = screen.getByText("Billing mode").parentElement;
    expect(billed).toHaveTextContent(/^Billing mode\s*Future AGI credits$/);
    expect(screen.getByText("req_stream_1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
  });

  it("keeps what arrived and says why when the stream stops", async () => {
    api.post.mockResolvedValue({
      data:
        PARTIAL + sse("error", { message: "Gateway request timed out: read" }),
    });
    open("/dashboard/gateway/models/openai/gpt-4o-mini?tab=try");
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    expect(await screen.findByText(/timed out/)).toBeInTheDocument();
    expect(screen.getByText("The sea")).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 200/)).toBeNull();
  });

  // A server older than test-playground-stream answers it
  // 404 (a console deployed ahead of its backend, or the demo image). Try
  // it then asks once for the answer in one piece, and says so.
  it("answers in one piece, and says so, when this server does not stream Try it", async () => {
    api.post.mockImplementation((url) =>
      url === endpoints.gateway.testPlaygroundStream("default")
        ? Promise.reject({ statusCode: 404, detail: "Not found." })
        : Promise.resolve({ data: RESULT }),
    );
    open(`${HAIKU}?tab=try`);
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    expect(await screen.findByText("echo: Hello")).toBeInTheDocument();
    expect(api.post.mock.calls.map(([url]) => url)).toEqual([
      endpoints.gateway.testPlaygroundStream("default"),
      endpoints.gateway.testPlayground("default"),
    ]);
    expect(api.post.mock.calls[1][1]).toEqual(api.post.mock.calls[0][1]);
    expect(
      screen.getByText(
        "This server does not stream Try it, so the answer arrived in one piece.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Not found/)).toBeNull();
    expect(screen.getByText("01M5DBMGBH3G18SMMM8XG9F2M5")).toBeInTheDocument();
  });

  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["a user whose level is not loaded", null],
  ])("%s cannot run a test request, and is told why", async (_who, level) => {
    org.level = level;
    open(`${HAIKU}?tab=try`);
    const run = await screen.findByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(run.closest('[aria-label="Owner or Admin only"]')).not.toBeNull();
    expect(
      screen.getByText(/only an organization Owner or Admin can run a test/),
    ).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("sends temperature, max tokens and top P only when set", async () => {
    api.post.mockResolvedValue({ data: FULL });
    open("/dashboard/gateway/models/openai/gpt-4o-mini?tab=try");
    await userEvent.type(
      await screen.findByRole("spinbutton", { name: "Temperature" }),
      "0.3",
    );
    await userEvent.type(
      screen.getByRole("spinbutton", { name: "Max tokens" }),
      "200",
    );
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1]).toEqual({
      model: "openai/gpt-4o-mini",
      prompt: "Write one sentence about the sea.",
      temperature: 0.3,
      max_tokens: 200,
    });
  });

  it("refuses a parameter out of range before sending", async () => {
    open("/dashboard/gateway/models/openai/gpt-4o-mini?tab=try");
    await userEvent.type(
      await screen.findByRole("spinbutton", { name: "Top P" }),
      "1.5",
    );
    expect(screen.getByText("0 to 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("offers only the parameters the model takes", async () => {
    open(`${HAIKU}?tab=try`);
    expect(
      await screen.findByRole("spinbutton", { name: "Temperature" }),
    ).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "Top P" })).toBeDisabled();
    expect(
      screen.getByRole("spinbutton", { name: "Max tokens" }),
    ).not.toBeDisabled();
  });

  it("shows the gateway's refusal", async () => {
    // A refusal is not streamed: one result event, as the JSON test returns.
    api.post.mockResolvedValue({
      data: sse("result", {
        status_code: 402,
        model: "anthropic/claude-haiku-5.5",
        body: { error: { message: "managed credit exhausted" } },
        guardrail_headers: {},
        blocked: false,
        warned: false,
      }),
    });
    open(`${HAIKU}?tab=try`);
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    expect(
      await screen.findByText(/managed credit exhausted/),
    ).toBeInTheDocument();
    expect(screen.getByText(/HTTP 402/)).toBeInTheDocument();
  });

  it("offers no run for a model it cannot call", async () => {
    open("/dashboard/gateway/models/black-forest-labs/flux-3-image?tab=try");
    expect(
      (
        await screen.findAllByText(
          /Future AGI credits pay for chat models only/,
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("S050 explains the off state without directing users to an unavailable activation switch", async () => {
    open(`${HAIKU}?tab=try`, {
      [endpoints.gateway.managedModels]: {
        status: true,
        result: { ...MANAGED_ON, enabled: false },
      },
    });
    expect(
      await screen.findByText(
        "Future AGI credits are off for your organization.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });
});

// A model the catalog has no row for, that one of the organization's own
// providers serves: on a deployment with no managed models (no catalog at
// all) or one whose catalog does not list it. Its page calls it through that
// provider: the gateway routes "<provider>/<model>" to the org's provider of
// that id when it lists <model> (server/handlers.go orgModelMatches).
describe("model page: a model the organization's own provider serves", () => {
  const MANAGED_OFF = {
    [endpoints.gateway.managedModels]: {
      status: true,
      result: {
        available: false,
        enabled: false,
        balance_usd: null,
        markup_percent: null,
      },
    },
  };
  const ownProviders = (overrides = {}) => ({
    [endpoints.gateway.providers("default")]: {
      status: true,
      result: {
        providers: [
          {
            id: "my-vllm",
            name: "my-vllm",
            display_name: "My vLLM",
            models: ["qwen-chat"],
            status: "healthy",
            pushed: true,
            ...overrides,
          },
        ],
      },
    },
  });
  const OWN = "/dashboard/gateway/models/my-vllm/qwen-chat";
  const ANSWER = {
    status: true,
    result: {
      status_code: 200,
      model: "my-vllm/qwen-chat",
      blocked: false,
      body: {
        choices: [{ message: { role: "assistant", content: "echo: Hello" } }],
        usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 },
      },
      guardrail_headers: {
        "X-Agentcc-Request-Id": "01OWNKEYREQUEST000000000001",
        "x-agentcc-latency-ms": "9",
        "x-agentcc-billing-mode": "byok",
      },
    },
  };

  it("opens with Try it on a deployment with no managed models, never the add-a-provider state", async () => {
    open(OWN, { ...MANAGED_OFF, ...ownProviders() });
    expect(
      await screen.findByRole("heading", { level: 1, name: "qwen-chat" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("my-vllm/qwen-chat").length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: "Try it" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "API" })).toBeInTheDocument();
    expect(screen.queryByText("Call it with your own provider key")).toBeNull();
    expect(screen.queryByText(/Add a provider/i)).toBeNull();
    // The provider is the model's route.
    expect(screen.getAllByText("My vLLM").length).toBeGreaterThan(0);
    expect(
      api.get.mock.calls.some(
        ([url]) => url === endpoints.gateway.managedModelsCatalog,
      ),
    ).toBe(false);
  });

  it("runs Try it through the gateway's test endpoint with that provider", async () => {
    api.post.mockResolvedValue({ data: ANSWER });
    open(`${OWN}?tab=try`, { ...MANAGED_OFF, ...ownProviders() });
    const prompt = await screen.findByRole("textbox", { name: "Prompt" });
    expect(
      screen.getByText(
        /One request to my-vllm\/qwen-chat .* billed by your provider/,
      ),
    ).toBeInTheDocument();
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "Hello");
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Stream the answer" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe(
      endpoints.gateway.testPlayground("default"),
    );
    expect(api.post.mock.calls[0][1]).toEqual({
      model: "my-vllm/qwen-chat",
      prompt: "Hello",
    });
    expect(await screen.findByText("echo: Hello")).toBeInTheDocument();
    expect(screen.getByText("01OWNKEYREQUEST000000000001")).toBeInTheDocument();
  });

  it("gives the API tab's request the page's id", async () => {
    open(`${OWN}?tab=api`, { ...MANAGED_OFF, ...ownProviders() });
    expect(await screen.findByText("Call this model")).toBeInTheDocument();
    expect(screen.queryByText("No request to copy for this model")).toBeNull();
    expect(screen.getAllByText(/my-vllm\/qwen-chat/).length).toBeGreaterThan(0);
  });

  it("opens the same page for a model a managed catalog does not list", async () => {
    open("/dashboard/gateway/models/flaky-upstream/o1-mini");
    expect(
      await screen.findByRole("heading", { level: 1, name: "o1-mini" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Try it" })).toBeInTheDocument();
    expect(screen.queryByText("This model is not in the catalog")).toBeNull();
  });

  it("says when the gateway does not have the provider yet", async () => {
    open(OWN, {
      ...MANAGED_OFF,
      ...ownProviders({
        pushed: false,
        status: "unhealthy",
        status_reason: "not pushed: the stored credential cannot be decrypted",
      }),
    });
    expect(
      await screen.findByText(
        /The gateway does not have My vLLM yet: the stored credential cannot be decrypted\./,
      ),
    ).toBeInTheDocument();
  });

  it("waits for the providers before saying no provider serves the model", async () => {
    let answer;
    open(OWN, {
      ...MANAGED_OFF,
      [endpoints.gateway.providers("default")]: () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    await waitFor(() => expect(answer).toBeTypeOf("function"));
    expect(screen.queryByText("Call it with your own provider key")).toBeNull();
    answer({
      status: true,
      result: { providers: [{ id: "my-vllm", models: ["qwen-chat"] }] },
    });
    expect(
      await screen.findByRole("heading", { level: 1, name: "qwen-chat" }),
    ).toBeInTheDocument();
  });

  // A model the organization's own provider serves came from Providers,
  // where that provider lists it: the way back names Providers and goes
  // there.
  it("names Providers as the way back from a model the org's own provider serves", async () => {
    open(OWN, { ...MANAGED_OFF, ...ownProviders() });
    await screen.findByRole("heading", { level: 1, name: "qwen-chat" });
    // The back link is the page's first link.
    const [back] = screen.getAllByRole("link", { name: "Providers" });
    expect(back).toHaveAttribute("href", "/dashboard/gateway/providers");
    expect(screen.getAllByRole("link")[0]).toBe(back);
    expect(screen.queryByRole("link", { name: /Auto routing/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Catalog/ })).toBeNull();
  });

  // With no managed models there is no Catalog page: /models opens Auto
  // routing, so the way back from a model no provider serves names Auto
  // routing and goes there.

  it("names Auto routing as the way back from a model no provider serves too", async () => {
    open("/dashboard/gateway/models/my-vllm/llama", {
      ...MANAGED_OFF,
      ...ownProviders(),
    });
    await screen.findByText("Call it with your own provider key");
    expect(screen.getByRole("link", { name: /Auto routing/ })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/auto",
    );
    expect(screen.queryByRole("link", { name: /Catalog/ })).toBeNull();
  });

  it("keeps Providers as an own model's way back even where managed models are offered", async () => {
    open("/dashboard/gateway/models/flaky-upstream/o1-mini");
    await screen.findByRole("heading", { level: 1, name: "o1-mini" });
    expect(screen.getByRole("link", { name: "Providers" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/providers",
    );
  });

  // With managed models off, nothing on the page names managed models or
  // managed credit: not Overview, not Try it, not the API tab's headers.
  it("names no managed models on any tab where none are offered", async () => {
    const { location } = open(OWN, { ...MANAGED_OFF, ...ownProviders() });
    await screen.findByRole("heading", { level: 1, name: "qwen-chat" });
    expect(document.body).not.toHaveTextContent(/managed/i);
    await userEvent.click(screen.getByRole("tab", { name: "Try it" }));
    await screen.findByRole("textbox", { name: "Prompt" });
    expect(document.body).not.toHaveTextContent(/managed/i);
    await userEvent.click(screen.getByRole("tab", { name: "API" }));
    await screen.findByText("Call this model");
    await waitFor(() => expect(location.search).toBe("?tab=api"));
    expect(document.body).not.toHaveTextContent(/managed/i);
    expect(
      screen.getByText(/x-agentcc-billing-mode/).closest("li"),
    ).toHaveTextContent("Response x-agentcc-billing-mode: byok (own key).");
  });

  it("keeps the own-provider-key state for a model no provider serves", async () => {
    open("/dashboard/gateway/models/my-vllm/llama", {
      ...MANAGED_OFF,
      ...ownProviders(),
    });
    expect(
      await screen.findByText("Call it with your own provider key"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Try it" })).toBeNull();
  });
});

// The detail half of the two legacy availability scenarios.
describe("credit availability in the v2 model detail", () => {
  it("shows the blocked reason and offers no one-key request or cURL example", async () => {
    const reason =
      "Not available on Future AGI credits: supported pricing is unavailable. Use your own provider key.";
    const model = {
      ...CATALOG.models[0],
      id: "acme/unpriced-generation",
      name: "Acme: Unpriced generation",
      state: "blocked",
      state_reason: reason,
      billed_input_per_mtok: 0,
      billed_output_per_mtok: 0,
    };
    open("/dashboard/gateway/models/acme/unpriced-generation", {
      [endpoints.gateway.managedModelsCatalog]: {
        status: true,
        result: { ...CATALOG, models: [model] },
      },
      [endpoints.gateway.providers("default")]: {
        status: true,
        result: { providers: [] },
      },
    });
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.queryByText("Free")).toBeNull();
    await userEvent.click(
      screen.getByRole("tab", { name: "API", exact: true }),
    );
    expect(
      await screen.findByText("No request to copy for this model"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Call this model")).toBeNull();
    expect(screen.queryByRole("tab", { name: "cURL" })).toBeNull();
  });

  it("shows both token rates and a separately marked-up per-call fee", async () => {
    const model = {
      ...CATALOG.models[0],
      id: "acme/mixed-price",
      name: "Acme: Mixed price",
      state: "active",
      dynamic_price: false,
      unpriced: false,
      stale_price: false,
      input_per_mtok: 3,
      output_per_mtok: 15,
      billed_input_per_mtok: 3.15,
      billed_output_per_mtok: 15.75,
      prices: { prompt: 0.000003, completion: 0.000015, request: 0.04 },
    };
    open("/dashboard/gateway/models/acme/mixed-price", {
      [endpoints.gateway.managedModelsCatalog]: {
        status: true,
        result: { ...CATALOG, models: [model] },
      },
    });
    const pricing = await screen.findByRole("region", { name: "Pricing" });
    expect(within(pricing).getByText("$3.15")).toBeInTheDocument();
    expect(within(pricing).getByText("$15.75")).toBeInTheDocument();
    expect(
      within(pricing).getByText(
        "Request fee (per call): $0.04 from the provider, $0.042 on Future AGI credits",
      ),
    ).toBeInTheDocument();
    expect(
      within(pricing).getByText("Future AGI price (provider + 5%)"),
    ).toBeInTheDocument();
  });
});
