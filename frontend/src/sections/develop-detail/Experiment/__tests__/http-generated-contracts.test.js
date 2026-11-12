import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createHash } from "node:crypto";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import { normalizeExperimentEntrySchema } from "../../../../../scripts/experiment-entry-contract.mjs";
import {
  ModelHubExperimentsV2CreateBody,
  ModelHubExperimentsV2UpdateBody,
  ModelHubExperimentsV2ReadResponse,
  ModelHubR4EndpointsTestCreateBody,
  ModelHubR4EndpointsTestCreateResponse,
} from "src/generated/api-contracts/api.zod";

// Repository root: the nearest directory at or above the working directory that holds api_contracts/
// (vitest runs from frontend/). Resolved from the working directory, not import.meta.url, so this file
// runs in the default jsdom environment with the shared setup.
const REPO_ROOT = (() => {
  let dir = process.cwd();
  while (!existsSync(join(dir, "api_contracts"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`api_contracts/ not found at or above ${process.cwd()}`);
    dir = parent;
  }
  return dir;
})();
const repoFile = (relative) => join(REPO_ROOT, relative);

const snapshot = JSON.parse(readFileSync(repoFile("api_contracts/openapi/swagger.json"), "utf8"));
const TEST_ROUTE = "/model-hub/r4/endpoints/test/";

describe("HTTP contracts stay aligned", () => {
  it("uses Swagger 2 definitions and body parameters for the real Test route", () => {
    expect(snapshot.swagger).toBe("2.0");
    expect(snapshot.paths[TEST_ROUTE].post.parameters[0]).toEqual({
      name: "data", in: "body", required: true,
      schema: { $ref: "#/definitions/HttpEndpointTestRequest" },
    });
    expect(snapshot.paths[TEST_ROUTE].post).not.toHaveProperty("requestBody");
    const names = Object.keys(snapshot.definitions).filter((name) => name.startsWith("Http"));
    for (const name of [...names, "PromptConfigEntry", "ExperimentDetailV2"])
      expect(OPENAPI_CONTRACT.definitions[name], name).toEqual(snapshot.definitions[name]);
  });

  it("Zod keeps HTTP settings, header references and every mapping value", () => {
    const entry = { target_type: "http", http: {
      url: "https://fixture.example/run", method: "POST",
      headers: [{ name: "Authorization", secret_ref: "TOKEN" }],
      body_template: "{}", output_path: "$", streaming: false, audio: "text", timeout_seconds: 23,
    }, variable_mapping: { direct: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", constant: { kind: "constant", value: "" }, empty: { kind: "empty" } } };
    const parsed = ModelHubExperimentsV2CreateBody.parse({ name: "HTTP", dataset_id: "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01", prompt_config: [entry], user_eval_metrics: [] });
    expect(parsed.prompt_config[0]).toEqual(entry);
    expect(ModelHubExperimentsV2UpdateBody.parse({ prompt_config: [entry] }).prompt_config[0]).toEqual(entry);
    expect(ModelHubExperimentsV2ReadResponse.parse({ status: true, result: { name: "HTTP", http_configs: [{ ...entry, order: 0 }] } }).result.http_configs[0]).toMatchObject(entry);
  });

  it("the request contract the app checks takes the A2A entry the form sends", () => {
    // The server reads the card and the auth as open JSON, and the context
    // column may be null. The generated Zod client renders those JSON values
    // as objects only, so this checks the contract the app validates
    // requests with (openapi-contract.js), the same one strictBody reads.
    const dataset_id = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
    const entry = { target_type: "a2a", name: "Support agent · answer", a2a: {
      card_url: "https://fixture.example", skill_id: "answer",
      text_column: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", data_columns: [], context_column: null,
      auth: { kind: "bearer", secret: "TOKEN" },
      card: { name: "Support agent", version: "1.0.0", url: "https://fixture.example/a2a",
        capabilities: { streaming: false }, skills: [{ id: "answer", tags: [], inputModes: ["text"] }] },
    } };
    const create = (prompt_config) => validateContractedRequestConfig({
      method: "post", url: "/model-hub/experiments/v2/",
      data: { name: "A2A", dataset_id, prompt_config, user_eval_metrics: [] },
    });
    const checked = create([entry]);
    expect(checked.ok).toBe(true);
    expect(checked.skipped).not.toBe(true);
    // Not a free pass: a text column that is not a column id is refused.
    expect(create([{ ...entry, a2a: { ...entry.a2a, text_column: "question" } }]).ok).toBe(false);
  });

  it("Zod keeps a bearer header reference on the Test route", () => {
    const prepared = { url: "https://fixture.example/a2a", method: "POST", headers: [{ name: "Authorization", secret_ref: "TOKEN", scheme: "bearer" }], body: "{}" };
    expect(ModelHubR4EndpointsTestCreateBody.parse({ prepared_request: prepared }).prepared_request).toMatchObject(prepared);
  });

  it.each([null, false, 0, "", [], { answer: "ok" }])("Zod retains JSON response %j and nullable diagnostics", (response) => {
    const prepared = { url: "https://fixture.example/run", method: "POST", headers: [{ name: "Authorization", secret_ref: "TOKEN" }], body: "{}" };
    expect(ModelHubR4EndpointsTestCreateBody.parse({ prepared_request: prepared, dataset_id: null, row_id: null, transform: null, trace_context: null }).prepared_request).toMatchObject(prepared);
    const body = { status: true, result: { simulated: false, provenance: "upstream", host: "fixture.example", status: 200, latency_ms: 10, request: { ...prepared, headers: { Authorization: "[redacted]" } }, traceparent: null, echoed: null, response, response_is_json: true, error: null } };
    expect(ModelHubR4EndpointsTestCreateResponse.parse(body)).toEqual(body);
  });
});


describe("COR-05 HTTP request discriminator", () => {
  const dataset_id = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
  const parseEntries = (entry) => [
    ModelHubExperimentsV2CreateBody.parse({ name: "HTTP", dataset_id, prompt_config: [entry], user_eval_metrics: [] }).prompt_config[0],
    ModelHubExperimentsV2UpdateBody.parse({ prompt_config: [entry] }).prompt_config[0],
  ];

  it("parses minimal HTTP entries with only fields accepted by the HTTP serializer", () => {
    for (const parsed of parseEntries({ target_type: "http", http: { url: "https://fixture.example/run" } })) {
      expect(parsed).toEqual({ target_type: "http", http: {
        url: "https://fixture.example/run", method: "POST", headers: [], body_template: "",
        output_path: "$.reply", streaming: false, audio: "text",
      } });
      // The backend exports PromptConfigEntry flat; the generator builds the HTTP variant from it.
      const allowed = Object.keys(normalizeExperimentEntrySchema(snapshot.definitions.PromptConfigEntry).oneOf[0].properties);
      expect(Object.keys(parsed).filter((key) => !allowed.includes(key))).toEqual([]);
    }
  });

  it.each([undefined, "prompt", "agent"])("retains the legacy defaults for %s", (kind) => {
    const entry = kind === undefined ? {} : { target_type: kind };
    for (const parsed of parseEntries(entry))
      expect(parsed).toEqual({ ...entry, model_params: {}, configuration: {}, output_format: "string" });
  });

  it("cannot parse invalid HTTP as a prompt fallback", () => {
    for (const http of [undefined, {}, { url: "https://fixture.example/run", method: "INVALID" }]) {
      const entry = { target_type: "http", ...(http && { http }) };
      expect(ModelHubExperimentsV2CreateBody.safeParse({ name: "HTTP", dataset_id, prompt_config: [entry], user_eval_metrics: [] }).success).toBe(false);
      expect(ModelHubExperimentsV2UpdateBody.safeParse({ prompt_config: [entry] }).success).toBe(false);
    }
  });

  it("upgrades the Swagger discriminator without reintroducing prompt-only defaults", () => {
    const definition = snapshot.definitions.PromptConfigEntry;
    const generated = normalizeExperimentEntrySchema(definition);
    expect(generated).not.toHaveProperty("properties");
    expect(generated.oneOf).toHaveLength(3);
    const [http, a2a, legacy] = generated.oneOf;
    expect(Object.keys(http.properties)).toEqual(["id", "name", "target_type", "http", "variable_mapping"]);
    expect(http.required).toEqual(["target_type", "http"]);
    expect(http.properties.target_type.enum).toEqual(["http"]);
    expect(http.properties.http.$ref).toBe("#/components/schemas/HttpExperimentSettings");
    expect(Object.keys(a2a.properties)).toEqual(["id", "name", "target_type", "a2a"]);
    expect(a2a.required).toEqual(["target_type", "a2a"]);
    expect(a2a.properties.target_type.enum).toEqual(["a2a"]);
    expect(a2a.properties.a2a.$ref).toBe("#/components/schemas/A2aExperimentSettings");
    expect(legacy.properties.target_type.enum).toEqual(["prompt", "agent"]);
    expect(legacy.properties).not.toHaveProperty("http");
    expect(legacy.properties).not.toHaveProperty("a2a");
    expect(legacy.properties).toHaveProperty("model_params");
    expect(legacy.properties).toHaveProperty("configuration");
    expect(legacy.properties.output_format.default).toBe("string");
    const flatExport = { ...definition };
    delete flatExport["x-oneOf"];
    expect(normalizeExperimentEntrySchema(flatExport)).toEqual(generated);
  });

  it("binds the tool catalog to the exact Swagger bytes", () => {
    const bytes = readFileSync(repoFile("api_contracts/openapi/swagger.json"));
    const catalog = JSON.parse(readFileSync(repoFile("futureagi/mcp_server/catalog/tools.generated.json"), "utf8"));
    expect(catalog.source_contract_sha256).toBe(createHash("sha256").update(bytes).digest("hex"));
  });
});
