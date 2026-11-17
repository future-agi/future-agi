// W6 B1: the one request builder (Test, endpoint test and every try).
import { describe, expect, it } from "vitest";
import {
  browserHeaders,
  browserSecretNote,
  buildRequest,
  headerLines,
  mcpEnvSecrets,
  missingSecretMessage,
  missingSecrets,
  parseTraceparent,
  requestKey,
  traceparentOf,
  valuesById,
} from "../requestModel";

const cols = [
  { id: "c1", name: "instruction" },
  { id: "c2", name: "order_id" },
  { id: "c3", name: "meta" },
];
const values = {
  c1: 'He said "refund" \\ now\nplease',
  c2: "A-7781",
  c3: { tier: "gold", n: 3 },
};

describe("buildRequest", () => {
  it("escapes values inside JSON strings and writes JSON values outside them", () => {
    const req = buildRequest({
      endpoint: {
        url: "https://agent.acme.test/v2/reply",
        body_template:
          '{"message": "{{instruction}}", "meta": {{meta}}, "id": {{order_id}}}',
      },
      values,
      columns: cols,
    });
    const parsed = JSON.parse(req.body);
    expect(parsed.message).toBe(values.c1);
    expect(parsed.meta).toEqual({ tier: "gold", n: 3 });
    expect(parsed.id).toBe("A-7781");
    expect(req.method).toBe("POST");
  });

  it("reads the mapping first (with a path), then the column name, then row_id", () => {
    const req = buildRequest({
      endpoint: {
        url: "https://h.test/o/{{order_id}}?q={{instruction}}",
        body_template:
          '{"tier": "{{who}}", "row": "{{row_id}}", "x": "{{nope}}"}',
      },
      mapping: { who: "c3::$.tier" },
      values,
      columns: cols,
      rowId: "r-12",
    });
    expect(req.url).toBe(
      `https://h.test/o/A-7781?q=${encodeURIComponent(values.c1)}`,
    );
    expect(JSON.parse(req.body)).toEqual({
      tier: "gold",
      row: "r-12",
      x: "{{nope}}",
    });
    expect(req.missing).toEqual(["nope"]);
  });

  it("lists variables that are empty on the row", () => {
    const req = buildRequest({
      endpoint: {
        url: "https://h.test",
        body_template: '{"m": "{{instruction}}"}',
      },
      values: { c1: "  " },
      columns: cols,
    });
    expect(req.empty).toEqual(["instruction"]);
  });

  it("sends no body on GET and keeps secrets as references", () => {
    const req = buildRequest({
      endpoint: {
        url: "https://h.test",
        method: "get",
        body_template: '{"m": "x"}',
        headers: [
          { name: "X-Api-Key", secret: "ACME_KEY" },
          { name: "X-Team", value: "support" },
          { name: " ", secret: "IGNORED" },
        ],
      },
    });
    expect(req.method).toBe("GET");
    expect(req.body).toBe("");
    expect(req.headers).toEqual([
      { name: "X-Api-Key", secret_ref: "ACME_KEY" },
      { name: "X-Team", value: "support" },
    ]);
    expect(browserHeaders(req)).toEqual({ "X-Team": "support" });
    expect(headerLines(req)).toEqual({
      "X-Api-Key": "•••• (from secret ACME_KEY)",
      "X-Team": "support",
    });
    expect(headerLines(req, { from: "browser" })["X-Api-Key"]).toBe(
      "not sent (from secret ACME_KEY)",
    );
    expect(browserSecretNote(req)).toBe(
      "Your browser sent this without X-Api-Key: secret headers are added when Future AGI calls your endpoint.",
    );
  });

  it("adds a W3C traceparent from the try's trace", () => {
    const traceId = "1a2b3c4d-5e6f-4a1b-a2c3-d4e5f6a7b8c9";
    const req = buildRequest({
      endpoint: { url: "https://h.test" },
      traceContext: { traceId, spanId: "1a2b3c4d00000000" },
    });
    expect(req.traceparent).toBe(
      "00-1a2b3c4d5e6f4a1ba2c3d4e5f6a7b8c9-1a2b3c4d00000000-01",
    );
    expect(req.headers.at(-1)).toEqual({
      name: "traceparent",
      value: req.traceparent,
    });
    expect(parseTraceparent(req.traceparent)).toEqual({
      traceId: "1a2b3c4d5e6f4a1ba2c3d4e5f6a7b8c9",
      spanId: "1a2b3c4d00000000",
    });
    expect(
      traceparentOf({ traceId: "short", spanId: "1a2b3c4d00000000" }),
    ).toBeNull();
    expect(traceparentOf({ traceId, spanId: "0000000000000000" })).toBeNull();
  });

  it("keys the reply on what was sent and the try, not on the trace", () => {
    const base = {
      endpoint: {
        url: "https://h.test",
        body_template: '{"m": "{{instruction}}"}',
      },
      values,
      columns: cols,
    };
    const a = buildRequest({
      ...base,
      trial: 1,
      traceContext: { traceId: "a".repeat(32), spanId: "b".repeat(16) },
    });
    const b = buildRequest({
      ...base,
      trial: 1,
      traceContext: { traceId: "c".repeat(32), spanId: "d".repeat(16) },
    });
    const c = buildRequest({ ...base, trial: 2 });
    expect(requestKey(a)).toBe(requestKey(b));
    expect(requestKey(a)).not.toBe(requestKey(c));
  });
});

describe("secrets", () => {
  it("names a secret the workspace does not have", () => {
    const miss = missingSecrets(
      [{ secret_ref: "ACME_KYE" }, "FI_PROJECT_KEY", "ACME_KYE"],
      ["FI_PROJECT_KEY", "ANTHROPIC_API_KEY"],
    );
    expect(miss).toEqual(["ACME_KYE"]);
    expect(missingSecretMessage(miss)).toBe(
      "No workspace secret named ACME_KYE. Pick one from the list.",
    );
    expect(missingSecretMessage([])).toBeNull();
  });

  it("reads FE sample rows by column id", () => {
    expect(
      valuesById({ c1: { cellValue: "hi" }, cells: { c2: "A-1" } }, cols),
    ).toEqual({ c1: "hi", c2: "A-1" });
  });

  it("reads an MCP server's env secrets one way (and the older single field)", () => {
    expect(
      mcpEnvSecrets({
        env: [
          { name: " ACME_TOKEN ", secret: "acme-mcp " },
          { name: "", secret: "" },
        ],
      }),
    ).toEqual([{ name: "ACME_TOKEN", secret: "acme-mcp" }]);
    expect(mcpEnvSecrets({ env_secret: "FI_PROJECT_KEY" })).toEqual([
      { name: "FI_PROJECT_KEY", secret: "FI_PROJECT_KEY" },
    ]);
    expect(mcpEnvSecrets({ env: [], env_secret: "X" })).toEqual([]);
    expect(mcpEnvSecrets(undefined)).toEqual([]);
  });
});
