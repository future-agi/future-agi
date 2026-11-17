// L2 fix round 1: dev keeps no response path for an API Calls column (its
// add-api-column/ and rerun-operation/ store the config without one and keep
// the whole response), so a real build neither offers nor sends it.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  apiCallConfigOf,
  apiCallFormOf,
  responsePathOffered,
} from "../responsePath";

const FORM = {
  column_name: "status",
  config: {
    url: "http://127.0.0.1:9/status",
    method: "GET",
    params: [],
    headers: [],
    body: "",
    output_type: "string",
    response_path: "$.status",
  },
  concurrency: 1,
};

describe("API Calls response path", () => {
  it("is offered only in prototype mode", () => {
    expect(responsePathOffered(false)).toBe(false);
    expect(responsePathOffered(true)).toBe(true);
  });

  it("a real build sends the config without response_path", () => {
    const out = apiCallFormOf(FORM, false);
    expect(out.config).not.toHaveProperty("response_path");
    expect(out.config.url).toBe(FORM.config.url);
    expect(out.column_name).toBe("status");
    expect(FORM.config.response_path).toBe("$.status");
  });

  it("prototype mode keeps it (its mocks apply it)", () => {
    expect(apiCallConfigOf(FORM.config, true)).toEqual(FORM.config);
  });

  it("the drawer renders the field only where it is offered", () => {
    const src = readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        "..",
        "AddColumnApiCall.jsx",
      ),
      "utf8",
    );
    const field = src.indexOf('fieldName="config.response_path"');
    const gate = src.lastIndexOf(
      "<ShowComponent condition={responsePathOffered()}>",
      field,
    );
    expect(field).toBeGreaterThan(0);
    expect(gate).toBeGreaterThan(0);
    expect(src.slice(gate, field)).not.toContain("</ShowComponent>");
    // Create, update and preview all go through apiCallFormOf.
    expect(src.match(/apiCallFormOf\(/g)?.length).toBe(2);
    expect(src).toContain("const formValues = apiCallFormOf(submitted);");
  });
});
