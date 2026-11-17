import React from "react";
import { Buffer } from "node:buffer";
import { createServer } from "node:http";
import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";

// Exercise discovery-ON behavior; launch-OFF has a separate unmocked flag suite.
vi.mock("./providerDiscovery", () => ({ PROVIDER_MODEL_DISCOVERY_ENABLED: true }));
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";
import httpClient from "src/utils/axios";
import { act, renderHook } from "@testing-library/react";
import { useFetchProviderModels } from "./hooks/useGatewayConfig";

// Real useFetchProviderModels/useUpdateProvider, real axios HTTP adapter, real
// loopback sockets. Only the app's authentication-configured axios singleton is
// replaced; no user credentials, hosted provider or external network is used.
vi.mock("src/utils/axios", async () => {
  const { default: axios } = await import("axios");
  return {
    default: axios.create({ adapter: "http", proxy: false }),
    endpoints: {
      gateway: {
        providerCredentials: {
          fetchModels: "/agentcc/provider-credentials/fetch_models/",
        },
        updateProvider: () => "/agentcc/gateways/default/update-provider/",
      },
    },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const saved = "original-fixture-only";
const replacement = "replacement-fixture-only";
const echoes = [
  saved,
  "original-fix",
  "b3JpZ2luYWwtZml4dHVyZS1vbmx5",
  "auxiliary-fixture-only",
  "cataloguesk-test-not-a-real-provider-key",
  "aB3dE6gH9jK2mN5p/Q8sT1vW4yZ7cF0iL",
];
let server;
let requests;
let responses;

beforeAll(async () => {
  server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    requests.push({ url: request.url, body });
    let result;
    if (request.url.endsWith("update-provider/")) {
      result = { gateway_synced: true };
    } else if (body.saved_models_only === true) {
      result = { models: ["gpt-4o"], dropped_model_count: echoes.length };
    } else if (body.api_key) {
      // Scripted own-key response is independent of saved context. Python's
      // member HTTP/DB differential proves the real server's noninterference.
      result = { models: ["gpt-4o", "gpt-4o-mini"], dropped_model_count: 1 };
    } else {
      result = { models: ["gpt-4o"] };
    }
    responses.push(result);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ result }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  httpClient.defaults.baseURL = `http://127.0.0.1:${server.address().port}`;
});
afterAll(async () => {
  server.closeAllConnections?.();
  await new Promise((resolve) => server.close(resolve));
});
beforeEach(() => {
  requests = [];
  responses = [];
});

it("the unmocked hook posts saved context and replacement together", async () => {
  const client = new QueryClient();
  const { result } = renderHook(() => useFetchProviderModels(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  act(() =>
    result.current.mutate({
      providerName: "openai",
      apiKey: replacement,
      baseUrl: "http://127.0.0.1:9",
      apiFormat: "openai",
      apiPathPrefix: "/v1",
    }),
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(requests).toHaveLength(1);
  expect(requests[0].body).toEqual({
    provider_name: "openai",
    api_key: replacement,
    base_url: "http://127.0.0.1:9",
    api_format: "openai",
    api_path_prefix: "/v1",
  });
  expect(result.current.data.models).toEqual(["gpt-4o", "gpt-4o-mini"]);
});

it("carries edit context AND replacement bytes through the real hook to HTTP", async () => {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <AddProviderDialog
        open
        onClose={vi.fn()}
        gatewayId="synthetic-gateway"
        provider={{
          name: "openai",
          config: {
            models: [...echoes, "gpt-4o"],
            base_url: "http://127.0.0.1:9",
            api_format: "openai",
          },
        }}
      />
    </QueryClientProvider>,
  );
  // Even the first paint must not reflect legacy gateway config IDs.
  for (const value of echoes)
    expect(document.body.textContent).not.toContain(value);
  await waitFor(() =>
    expect(requests.some(({ body }) => body.saved_models_only)).toBe(true),
  );
  await userEvent.type(screen.getByLabelText(/^API Key/i), replacement);
  await waitFor(
    () =>
      expect(requests.some(({ body }) => body.api_key === replacement)).toBe(
        true,
      ),
    { timeout: 4000 },
  );
  const sent = requests.find(({ body }) => body.api_key === replacement);
  expect(sent.body).toEqual({
    provider_name: "openai",
    api_key: replacement,
    base_url: "http://127.0.0.1:9",
    api_format: "openai",
    api_path_prefix: "/v1",
  });
  await screen.findByText("(2 available)");
  for (const value of echoes) {
    expect(JSON.stringify(responses)).not.toContain(value);
    expect(document.body.textContent).not.toContain(value);
  }
  await userEvent.click(screen.getByRole("button", { name: "Select All" }));
  await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
  await waitFor(() =>
    expect(requests.some(({ url }) => url.endsWith("update-provider/"))).toBe(
      true,
    ),
  );
  const config = requests.find(({ url }) => url.endsWith("update-provider/"))
    .body.config;
  expect(config.api_key).toBe(replacement);
  expect(config.models).toEqual(["gpt-4o", "gpt-4o-mini"]);
  // Dropping providerName in either caller or hook fails exact body assertions.
  // It must not change own-key filtering based on any saved credential.
}, 10000);
