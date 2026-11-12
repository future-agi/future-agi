// SRC-HTTP-02: public Test uses the same builder/transform as row execution.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import EndpointTestPanel from "../EndpointTestPanel";
import { endpointFields, testEndpoint } from "../testEndpoint";
import { buildRequest, valuesById } from "../requestModel";
import { applyTransform, transformOf } from "../transformModel";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: { secrets: { list: "/model-hub/secrets/" } },
}));

const URL = "/model-hub/r4/endpoints/test/";
const columns = [
  { id: "c-question", name: "question" },
  { id: "c-topic", name: "topic" },
];
const row = {
  row_id: "11111111-1111-4111-8111-111111111111",
  "c-question": { cell_value: 'a "quoted" question' },
  "c-topic": "a/b",
};
const params = () => ({
  url: "https://fixture.example/{{topic}}",
  method: "POST",
  bodyTemplate: '{"message":"{{input}}","row":"{{row_id}}"}',
  outputPath: "$.data.answer",
  transform: {
    output: "{{ $.data.answer }} ({{ $.data.id }})",
    tool_calls: "$.actions",
    usage: { prompt_tokens: "$.usage.input" },
  },
  headers: [{ name: "Authorization", secret: "ENDPOINT_TOKEN" }],
  secretNames: ["ENDPOINT_TOKEN"],
  datasetId: "22222222-2222-4222-8222-222222222222",
  row,
  columns,
  mapping: { input: "c-question" },
});
const reply = {
  data: { answer: "from HTTPS fixture", id: "A1" },
  actions: [{ name: "lookup", arguments: { id: "A1" } }],
  usage: { input: 7 },
};
const envelope = (overrides = {}) => ({
  data: {
    status: true,
    result: {
      simulated: false,
      provenance: "upstream",
      host: "fixture.example",
      status: 200,
      latency_ms: 125,
      response: reply,
      response_is_json: true,
      error: null,
      request: {
        method: "POST", url: "https://fixture.example/a%2Fb",
        headers: { Authorization: "masked (from secret ENDPOINT_TOKEN)" },
        body: "sent body",
      },
      ...overrides,
    },
  },
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("crypto", { getRandomValues: (bytes) => bytes.fill(17) });
  axios.get.mockResolvedValue({ data: { results: [{ name: "ENDPOINT_TOKEN" }] } });
  axios.post.mockResolvedValue(envelope());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("public endpoint Test request and response", () => {
  it("sends selected-row bytes from buildRequest and only header references", async () => {
    const p = params();
    const before = JSON.stringify(p);
    await testEndpoint(p);
    const [path, sent] = axios.post.mock.calls[0];
    expect(path).toBe(URL);
    const expected = buildRequest({
      endpoint: endpointFields(p), mapping: p.mapping,
      values: valuesById(row, columns), columns, rowId: row.row_id,
      trial: 1, traceContext: sent.trace_context,
    });
    expect(sent.prepared_request).toEqual({
      url: expected.url, method: expected.method,
      headers: expected.headers, body: expected.body,
    });
    expect(sent.prepared_request.url).toBe("https://fixture.example/a%2Fb");
    expect(JSON.parse(sent.prepared_request.body)).toEqual({
      message: 'a "quoted" question', row: row.row_id,
    });
    expect(sent.prepared_request.headers[0]).toEqual({
      name: "Authorization", secret_ref: "ENDPOINT_TOKEN",
    });
    expect(sent.row_id).toBe(row.row_id);
    expect(JSON.stringify(p)).toBe(before);
  });

  it("uses actual provenance, measured latency and the run's response transform", async () => {
    const p = params();
    const traceparent = "00-11111111111111111111111111111111-1111111111111111-01";
    axios.post.mockResolvedValue(envelope({ traceparent, echoed: traceparent }));
    const result = await testEndpoint(p);
    const transformed = applyTransform(reply, transformOf(endpointFields(p)));
    expect(result.simulated).toBe(false);
    expect(result.ms).toBe(125);
    expect(result.output).toBe(transformed.output);
    expect(result.tool_calls).toEqual(transformed.tool_calls);
    expect(result.usage).toEqual(transformed.usage);
    expect(result.echoed).toBe(traceparent);
    expect(JSON.parse(result.body)).toEqual(reply);
    expect(result.error).toBeNull();
  });

  it("retains explicit simulated provenance instead of guessing from the host", async () => {
    axios.post.mockResolvedValue(envelope({ simulated: true, provenance: "simulated" }));
    expect((await testEndpoint(params())).simulated).toBe(true);
  });

  it("does not silently pass an empty or unproven API response", async () => {
    axios.post.mockResolvedValue({ data: { result: {} } });
    expect((await testEndpoint(params())).error).toMatch(/provenance/i);
  });

  it("reports missing output paths and retries with edited settings in place", async () => {
    const p = { ...params(), transform: null, outputPath: "$.missing" };
    const failed = await testEndpoint(p);
    expect(failed.error).toBe("Output path $.missing not found in the response");
    expect(JSON.parse(failed.body)).toEqual(reply);
    const recovered = await testEndpoint({ ...p, outputPath: "$.data.answer" });
    expect(recovered.output).toBe("from HTTPS fixture");
    expect(recovered.error).toBeNull();
    expect(axios.post.mock.calls[1][1].prepared_request.body)
      .toBe(axios.post.mock.calls[0][1].prepared_request.body);
  });

  it("shows non-2xx status even when the response contains a valid output", async () => {
    axios.post.mockResolvedValue(envelope({ status: 503 }));
    const result = await testEndpoint(params());
    expect(result.error).toMatch(/HTTP 503/);
    expect(result.status).toBe(503);
    expect(JSON.parse(result.body)).toEqual(reply);
  });

  it("preserves an unreachable-host diagnostic without inventing a response", async () => {
    axios.post.mockResolvedValue(envelope({
      status: null, response: null, response_is_json: false,
      error: "Couldn't reach the endpoint. Check the URL and retry.",
    }));
    const result = await testEndpoint(params());
    expect(result.error).toMatch(/Couldn't reach/);
    expect(result.body).toBeNull();
    expect(result.simulated).toBe(false);
  });

  it.each([false, 0, null])("transforms JSON scalar %s at $", async (value) => {
    axios.post.mockResolvedValue(envelope({ response: value }));
    const result = await testEndpoint({ ...params(), transform: null, outputPath: "$" });
    expect(result.error).toBeNull();
    expect(result.output).toBe(value == null ? "" : String(value));
    expect(result.body).toBe(JSON.stringify(value));
  });

  it("blocks missing mapped input before any request", async () => {
    const result = await testEndpoint({ ...params(), bodyTemplate: "{{unknown}}" });
    expect(result.error).toMatch(/unknown/);
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("blocks a missing secret_ref as well as a missing legacy secret name", async () => {
    const result = await testEndpoint({
      ...params(), headers: [{ name: "Authorization", secret_ref: "MISSING" }],
    });
    expect(result.error).toMatch(/No workspace secret named MISSING/);
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("normalizes rejected API errors to an actionable Error for the panel", async () => {
    axios.post.mockRejectedValue({ result: { prepared_request: ["Use a public URL."] } });
    await expect(testEndpoint(params())).rejects.toThrow(/Use a public URL/);
  });
});

it("the panel shows real results and retries without discarding configuration", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const http = { ...params(), transform: null, outputPath: "$.missing" };
  const ui = (config) => (
    <QueryClientProvider client={client}>
      <EndpointTestPanel http={config} row={row} columns={columns}
        mapping={params().mapping} datasetId={params().datasetId} rowLabel="Row 1" />
    </QueryClientProvider>
  );
  const { rerender } = render(ui(http));
  fireEvent.click(screen.getByTestId("http-test"));
  await waitFor(() => expect(screen.getByTestId("http-test-output").textContent)
    .toMatch(/Output path \$\.missing/));
  expect(screen.queryByTestId("http-test-simulated")).toBeNull();
  rerender(ui({ ...http, outputPath: "$.data.answer" }));
  fireEvent.click(screen.getByTestId("http-test"));
  await waitFor(() => expect(screen.getByTestId("http-test-output").textContent)
    .toBe("from HTTPS fixture"));
  expect(screen.getByTestId("http-test-result").textContent).toMatch(/125 ms/);
  expect(axios.post.mock.calls[1][1].headers).toEqual(http.headers);
  client.clear();
});

it("registers the real POST contract without granting a launch claim", () => {
  const route = OPENAPI_CONTRACT.endpoints[URL]?.post;
  expect(route.requestBody).toEqual({ $ref: "#/definitions/HttpEndpointTestRequest" });
  expect(route.responses[200]).toEqual({ $ref: "#/definitions/HttpEndpointTestResponse" });
  expect(route.runtimeRequestValidation).toBe(true);
  expect(route.runtimeResponseValidation).toBe(true);
  expect(OPENAPI_CONTRACT.definitions.HttpEndpointTestRequest.required).toEqual(["prepared_request"]);
  expect(OPENAPI_CONTRACT.definitions.HttpPreparedRequest.required).toEqual(["url", "method", "headers", "body"]);
});
