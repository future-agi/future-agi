import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate } from "react-router-dom";
import { endpoints } from "src/utils/axios";
import { answerFrom, contractFailures, demoResponses, renderModelsAt } from "../test-fixtures/harness";
import { modelSnippets } from "../snippets";
import ModelDetailPage from "./ModelDetailPage";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { ...actual.default, get: api.get, post: api.post } };
});
vi.mock("notistack", async (original) => ({ ...(await original()), enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({ useOrganization: () => ({ orgLevel: 15 }) }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "https://gateway.example.test/v1" },
    hosted: true,
    deploymentModeConfirmed: true,
  }),
}));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

const MODEL = "/dashboard/gateway/models/openai/gpt-4o-mini";
const OTHER = "/dashboard/gateway/models/openai/gpt-4o";
const OWN = "/dashboard/gateway/models/my-vllm/qwen-chat";
const RESULT = {
  status: true,
  result: {
    status_code: 200,
    body: { choices: [{ message: { content: "A completed answer." } }] },
    guardrail_headers: { "x-agentcc-request-id": "test-request-06" },
  },
};
const ownProviders = {
  [endpoints.gateway.providers("default")]: {
    status: true,
    result: { providers: [{ id: "my-vllm", models: ["qwen-chat"], pushed: true }] },
  },
};
const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const PARTIAL = sse("meta", { status_code: 200 }) + sse("delta", { content: "Partial answer" });
const FULL = PARTIAL + sse("done", { status_code: 200, usage: { prompt_tokens: 4, completion_tokens: 2 } });

function open(path = `${MODEL}?tab=try`, overrides = {}) {
  api.get.mockImplementation(answerFrom(demoResponses(endpoints, overrides)));
  const router = {};
  function Probe() {
    router.location = useLocation();
    router.navigate = useNavigate();
    return <ModelDetailPage />;
  }
  return { ...renderModelsAt(path, <Probe />), router };
}
const edit = (label, value) => fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
const tab = async (name) => userEvent.click(screen.getByRole("tab", { name }));

beforeEach(() => { api.get.mockReset(); api.post.mockReset(); });
afterEach(async () => { expect(await contractFailures(api)).toEqual([]); vi.restoreAllMocks(); });

describe("Try it continuity", () => {
  it("retains the draft and completed result across tabs, and exports the submitted request rather than later edits", async () => {
    api.post.mockResolvedValue({ data: RESULT });
    const { container, router } = open();
    await screen.findByRole("textbox", { name: "Prompt" });
    const prompt = '  Explain "tides".\nThen say: it\'s the sea.  ';
    const system = '  Keep "quotes".\nBe brief.  ';
    edit("Prompt", prompt);
    edit("System prompt (optional)", system);
    for (const [name, value] of [["Temperature", "0.3"], ["Max tokens", "200"], ["Top P", "0.8"]]) {
      fireEvent.change(screen.getByRole("spinbutton", { name }), { target: { value } });
    }
    await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("A completed answer.");
    expect(api.post.mock.calls[0][1]).toEqual({
      model: "openai/gpt-4o-mini", prompt: prompt.trim(), system_prompt: system.trim(),
      temperature: 0.3, max_tokens: 200, top_p: 0.8,
    });
    edit("Prompt", "A later, unsent draft");
    edit("System prompt (optional)", "A later system prompt");
    fireEvent.change(screen.getByRole("spinbutton", { name: "Temperature" }), { target: { value: "0.9" } });
    await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await tab("Overview");
    await tab("API");
    await tab("Try it");
    expect(screen.getByRole("textbox", { name: "Prompt" })).toHaveValue("A later, unsent draft");
    expect(screen.getByRole("textbox", { name: "System prompt (optional)" })).toHaveValue("A later system prompt");
    expect(screen.getByRole("spinbutton", { name: "Temperature" })).toHaveValue(0.9);
    expect(screen.getByRole("checkbox", { name: "Stream the answer" })).toBeChecked();
    expect(screen.getByText("A completed answer.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use this request in API" }));
    expect(screen.getByText(/Using your submitted request/)).toBeInTheDocument();
    const request = {
      model: "openai/gpt-4o-mini", stream: false,
      messages: [{ role: "system", content: system.trim() }, { role: "user", content: prompt.trim() }],
      temperature: 0.3, max_tokens: 200, top_p: 0.8,
    };
    const snippets = modelSnippets("https://gateway.example.test/v1", request.model, { type: "chat", request });
    for (const snippet of snippets) {
      await userEvent.click(within(screen.getByRole("group", { name: "Request language" })).getByRole("button", { name: snippet.label }));
      expect(container.querySelector("pre code").textContent).toBe(snippet.code);
    }
    expect(screen.getByRole("checkbox", { name: "Stream the response" })).not.toBeChecked();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(router.location.state)).not.toContain("tides");
    expect(router.location.search).toBe("?tab=api");
    act(() => router.navigate(`${OTHER}?tab=try`));
    await screen.findByRole("textbox", { name: "Prompt" });
    expect(screen.getByRole("textbox", { name: "Prompt" })).toHaveValue("Write one sentence about the sea.");
    expect(screen.queryByText("A completed answer.")).toBeNull();
    await tab("API");
    expect(screen.queryByText(/Using your submitted request/)).toBeNull();
  });

  it("exports the streaming choice of the completed request", async () => {
    api.post.mockResolvedValue({ data: FULL });
    const { container } = open();
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    await screen.findByText("Partial answer");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
    await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await userEvent.click(screen.getByRole("button", { name: "Use this request in API" }));
    expect(screen.getByRole("checkbox", { name: "Stream the response" })).toBeChecked();
    expect(container.querySelector("pre code").textContent).toContain('"stream": true');
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it.each(["tab", "model", "page"])("aborts an active stream once on leaving the %s and never replays it", async (destination) => {
    let config;
    const stopped = vi.fn();
    api.post.mockImplementation((_url, _body, options) => {
      config = options;
      options.signal.addEventListener("abort", stopped);
      options.onDownloadProgress({ event: { target: { responseText: PARTIAL } } });
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject({ code: "ERR_CANCELED" }));
      });
    });
    const { router, unmount } = open();
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    await screen.findByText("Partial answer");
    if (destination === "tab") await tab("Overview");
    else if (destination === "model") act(() => router.navigate(`${OTHER}?tab=try`));
    else unmount();
    await waitFor(() => expect(stopped).toHaveBeenCalledTimes(1));
    act(() => config.onDownloadProgress({ event: { target: { responseText: FULL } } }));
    if (destination === "tab") {
      await tab("Try it");
      expect(screen.getByText("Partial answer")).toBeInTheDocument();
      expect(screen.getByText(/Stopped before the answer was complete/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Use this request in API" })).toBeNull();
      await tab("API");
    }
    expect(stopped).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("marks partial transport failures incomplete and retains the failure across tabs without replay", async () => {
    api.post.mockImplementation((_url, _body, options) => {
      options.onDownloadProgress({ event: { target: { responseText: PARTIAL } } });
      return Promise.reject(new Error("Connection interrupted"));
    });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    expect(await screen.findByText("Connection interrupted")).toBeInTheDocument();
    expect(screen.getByText(/Stopped before the answer was complete/)).toBeInTheDocument();
    await tab("Overview");
    await tab("Try it");
    expect(screen.getByText("Partial answer")).toBeInTheDocument();
    expect(screen.getByText("Connection interrupted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use this request in API" })).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it.each(["json timeout", "partial timeout", "truncated stream"])("keeps work, explains uncertain billing and never replays a %s", async (mode) => {
    api.post.mockImplementation((_url, _body, options) => {
      if (mode === "truncated stream") return Promise.resolve({ data: PARTIAL });
      if (mode === "partial timeout") options.onDownloadProgress({ event: { target: { responseText: PARTIAL } } });
      return Promise.reject({ message: "Gateway test timed out", transportCode: "ECONNABORTED" });
    });
    open();
    await screen.findByRole("textbox", { name: "Prompt" });
    edit("Prompt", "Keep my original prompt");
    edit("System prompt (optional)", "Keep my system text");
    if (mode === "json timeout") await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText(/Completion unknown/)).toHaveTextContent(/may have run and been billed/);
    expect(screen.getByRole("link", { name: "Check Logs" })).toHaveAttribute("href", "/dashboard/gateway/logs");
    await tab("Overview");
    await tab("Try it");
    expect(screen.getByRole("textbox", { name: "Prompt" })).toHaveValue("Keep my original prompt");
    expect(screen.getByRole("textbox", { name: "System prompt (optional)" })).toHaveValue("Keep my system text");
    if (mode !== "json timeout") expect(screen.getByText("Partial answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use this request in API" })).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("does not treat a coded 404 refusal as a missing streaming endpoint", async () => {
    api.post.mockRejectedValue({ statusCode: 404, detail: "Not found.", code: "model_unavailable" });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe(endpoints.gateway.testPlaygroundStream("default"));
  });

  it("does not resend a pending non-streaming request when tabs change", async () => {
    let finish;
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    open();
    await screen.findByRole("textbox", { name: "Prompt" });
    await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await tab("API");
    await tab("Try it");
    expect(screen.getByRole("button", { name: /Run/ })).toBeDisabled();
    await act(async () => finish({ data: RESULT }));
    expect(await screen.findByText("A completed answer.")).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("requires an explicit unknown-model chat choice; choosing an embedding API recipe never sends chat", async () => {
    open(`${OWN}?tab=try`, ownProviders);
    expect(await screen.findByText(/compatibility has not been verified/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Open API instructions" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Request kind" }));
    await userEvent.click(screen.getByRole("option", { name: "Embeddings" }));
    expect(screen.getByText(/\/embeddings/)).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    await tab("Try it");
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Prepare a chat test" }));
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
    expect(api.post).not.toHaveBeenCalled();
    await tab("Overview");
    await tab("Try it");
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
  });

  it("exports an explicitly chosen unknown-model chat test without inventing its type", async () => {
    api.post.mockResolvedValue({ data: RESULT });
    const { container } = open(`${OWN}?tab=try`, ownProviders);
    await userEvent.click(await screen.findByRole("button", { name: "Prepare a chat test" }));
    edit("Prompt", 'An own-provider "chat" request');
    await userEvent.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("A completed answer.");
    edit("Prompt", "An unsent edit");
    await userEvent.click(screen.getByRole("button", { name: "Use this request in API" }));
    expect(screen.getByText(/Your submitted request is a chat request/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Request kind" }));
    await userEvent.click(screen.getByRole("option", { name: "Chat" }));
    const code = container.querySelector("pre code").textContent;
    expect(code).toContain('"model": "my-vllm/qwen-chat"');
    expect(code).toContain(JSON.stringify('An own-provider "chat" request'));
    expect(code).not.toContain("An unsent edit");
    expect(screen.getByText("Type not reported")).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("preserves browse context through every tab and returns the exact catalog query", async () => {
    const browse = { pathname: "/dashboard/gateway/models", search: "?q=fast&provider=openai&sort=price_asc", scrollTop: 840 };
    const { router } = open({ pathname: MODEL, search: "?tab=try", state: { gatewayModelBrowse: browse } });
    await screen.findByRole("textbox", { name: "Prompt" });
    for (const name of ["API", "Overview", "Try it"]) {
      await tab(name);
      expect(router.location.state.gatewayModelBrowse).toEqual(browse);
      expect(screen.getByRole("link", { name: "Models" })).toHaveAttribute("href", `${browse.pathname}${browse.search}`);
    }
    await userEvent.click(screen.getByRole("link", { name: "Models" }));
    expect(router.location.pathname).toBe(browse.pathname);
    expect(router.location.search).toBe(browse.search);
  });
});
