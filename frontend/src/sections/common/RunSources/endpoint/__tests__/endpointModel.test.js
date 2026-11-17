import { describe, expect, it } from "vitest";
import {
  defaultBodyTemplate,
  fillTemplate,
  missingVariables,
  pickPath,
  requestSummary,
  rowValues,
  streamingSummary,
  unknownColumns as unknownColumnsOf,
} from "../endpointModel";

describe("endpointModel", () => {
  it("fills the body template with JSON-escaped row values", () => {
    const body = fillTemplate(defaultBodyTemplate("instruction"), {
      instruction: 'Refund "the mixer"',
      row_id: "r1",
    });
    expect(JSON.parse(body)).toEqual({ message: 'Refund "the mixer"' });
    expect(missingVariables("{{a}} {{b}}", { a: 1 })).toEqual(["b"]);
  });

  it("reads the reply with a small JSONPath", () => {
    const data = { reply: "ok", choices: [{ message: { content: "hi" } }] };
    expect(pickPath(data, "$.reply")).toBe("ok");
    expect(pickPath(data, "$.choices[0].message.content")).toBe("hi");
    expect(pickPath(data, "$.missing.x")).toBeUndefined();
  });

  it("reads values by column name from both cell shapes", () => {
    const columns = [
      { id: "c1", name: "instruction" },
      { id: "c2", name: "order_id" },
    ];
    const row = {
      row_id: "r9",
      c1: { cell_value: "hello" },
      c2: { cellValue: "A-1" },
    };
    expect(rowValues(row, columns)).toEqual({
      row_id: "r9",
      instruction: "hello",
      order_id: "A-1",
    });
  });

  it("summarises the request and streaming in one line each", () => {
    expect(
      requestSummary({
        method: "POST",
        headers: [{ name: "Authorization", secret: "ACME_TOKEN" }],
        template: defaultBodyTemplate("instruction"),
        outputPath: "$.reply",
      }),
    ).toBe(
      "POST · 1 header from secrets · body uses instruction · reply at $.reply",
    );
    expect(streamingSummary({ audio: "text", streaming: false })).toBe(
      "Text in, text out",
    );
  });
});

describe("Test on one row headers (options-truth L1 G5, L5 G5)", () => {
  // L5 G5 (W0-6): a browser test never sends a stand-in for a secret (it
  // sent the literal "Bearer ****"); it names the secret as not sent.
  it("sends only literal headers and names secret ones as not sent", async () => {
    const { isLocalUrl, shownHeaders, testHeaders } = await import(
      "../endpointModel"
    );
    const h = [
      { name: "Authorization", secret: "ACME_AGENT_TOKEN" },
      { name: "X-Team", value: "support" },
      { name: "", secret: "X" },
    ];
    expect(testHeaders(h)).toEqual({ "X-Team": "support" });
    expect(JSON.stringify(testHeaders(h))).not.toMatch(/\*\*\*\*/);
    expect(shownHeaders(h)).toEqual({
      "X-Team": "support",
      Authorization: "from secret ACME_AGENT_TOKEN (not sent from the browser)",
    });
    expect(isLocalUrl("http://127.0.0.1:8787/reply")).toBe(true);
    expect(isLocalUrl("https://agent.acme.test/v2/reply")).toBe(false);
  });
});

describe("unknownColumns (N6)", () => {
  it("names the {{vars}} the dataset lacks; row_id is always known", () => {
    const ts = ["instruction", "expected_end_state"];
    expect(unknownColumnsOf('{"message": "{{customer_message}}"}', ts)).toEqual(
      ["customer_message"],
    );
    expect(
      unknownColumnsOf(
        '{"message": "{{instruction}}", "id": "{{row_id}}"}',
        ts,
      ),
    ).toEqual([]);
    expect(unknownColumnsOf("{{a}} {{a}} {{b}}", ["x"])).toEqual(["a", "b"]);
    // columns not known yet: no warning
    expect(unknownColumnsOf("{{customer_message}}", [])).toEqual([]);
  });
});
