// L7c g2 · knob:http:method (skeptic must-fix): a GET card keeps no body,
// maps only its URL's {{variables}} and says where the column goes; a
// change back to POST restores what was put aside.
import { describe, expect, it } from "vitest";
import {
  getBodyNote,
  httpMethodSwitch,
  isGetMethod,
  sentTemplateOf,
} from "../endpointCards-g2";
import { variablesInTemplate } from "../variableMapping";
import { buildRequest } from "src/sections/common/RunSources/endpoint/requestModel";

const BODY = '{\n  "message": "{{customer_message}}"\n}';

describe("knob:http:method · GET sends no body", () => {
  it("only the URL's variables are sent (and mapped) on a GET", () => {
    const url = "https://api.acme.ai/v1/reply?order={{order_id}}";
    expect(
      variablesInTemplate(sentTemplateOf({ method: "GET", url, body: BODY })),
    ).toEqual(["order_id"]);
    expect(
      variablesInTemplate(sentTemplateOf({ method: "POST", url, body: BODY })),
    ).toEqual(["order_id", "customer_message"]);
    expect(isGetMethod("get")).toBe(true);
    expect(isGetMethod("PUT")).toBe(false);
  });
  it("the note names the input column", () => {
    expect(getBodyNote("customer_message")).toBe(
      "GET sends no body: put {{customer_message}} in the URL query, e.g. ?q={{customer_message}}",
    );
    expect(getBodyNote(null)).toMatch(/put \{\{column\}\} in the URL query/);
  });
  it("to GET: the body and its body-only mapping are put aside", () => {
    const out = httpMethodSwitch({
      toGet: true,
      url: "https://api.acme.ai/v1/reply?order={{order_id}}",
      body: '{"message": "{{customer_message}}", "order": "{{order_id}}"}',
      mapping: { customer_message: "col_msg", order_id: "col_order" },
      stockBody: BODY,
    });
    expect(out.body).toBe("");
    expect(out.mapping).toEqual({ order_id: "col_order" });
    expect(out.aside.mapping).toEqual({ customer_message: "col_msg" });
    // Back to POST: the put-aside body and mapping return.
    const back = httpMethodSwitch({
      toGet: false,
      url: "https://api.acme.ai/v1/reply",
      body: "",
      mapping: out.mapping,
      aside: out.aside,
      stockBody: BODY,
    });
    expect(back.body).toBe(out.aside.body);
    expect(back.mapping).toEqual({
      customer_message: "col_msg",
      order_id: "col_order",
    });
    expect(back.aside).toBeNull();
  });
  it("nothing to put aside is no change; an empty POST body gets the stock one", () => {
    expect(
      httpMethodSwitch({ toGet: true, url: "u", body: "", stockBody: BODY }),
    ).toBeNull();
    expect(
      httpMethodSwitch({
        toGet: false,
        url: "u",
        body: '{"a":1}',
        stockBody: BODY,
      }),
    ).toBeNull();
    expect(
      httpMethodSwitch({ toGet: false, url: "u", body: " ", stockBody: BODY })
        .body,
    ).toBe(BODY);
  });
  it("the one request builder sends no body on a GET", () => {
    const req = buildRequest({
      endpoint: {
        url: "https://api.acme.ai/v1/reply?q={{customer_message}}",
        method: "GET",
        body_template: BODY,
      },
      values: { c1: "where is my order" },
      columns: [{ id: "c1", name: "customer_message" }],
    });
    expect(req.body).toBe("");
    expect(req.url).toBe(
      "https://api.acme.ai/v1/reply?q=where%20is%20my%20order",
    );
  });
});
