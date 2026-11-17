// v2.1 g8 (dyncol:api-calls): Configure reopens an API Calls column with
// the form it was saved with (URL with column names, method, params,
// output, path, concurrency), not a blank form.
import { describe, expect, it } from "vitest";
import { savedApiCallForm } from "../savedApiCallForm";
import { transformDynamicColumnConfig } from "../../common";

const COLS = [{ headerName: "ticket_id", field: "c9" }];
const toForm = (c) => transformDynamicColumnConfig("api_call", c, COLS);

describe("savedApiCallForm", () => {
  it("reopens the stored form (ids back to names, params as rows)", () => {
    const out = savedApiCallForm(
      {
        column_name: "status_api",
        new_column_name: "status_api",
        config: {
          url: "https://api.example.com/orders/{{c9}}",
          method: "GET",
          params: { q: { type: "PlainText", value: "x" } },
          headers: {},
          body: "",
          output_type: "string",
          response_path: "$.status",
        },
        concurrency: 4,
      },
      toForm,
    );
    expect(out.column_name).toBe("status_api");
    expect(out.config.url).toBe("https://api.example.com/orders/{{ticket_id}}");
    expect(out.config.method).toBe("GET");
    expect(out.config.response_path).toBe("$.status");
    expect(out.config.params[0]).toMatchObject({ name: "q", value: "x" });
    expect(out.concurrency).toBe(4);
  });
  it("other shapes go to the plain transform", () => {
    expect(savedApiCallForm({ url: "u" }, toForm)).toBeNull();
    expect(savedApiCallForm({ config: [] }, toForm)).toBeNull();
  });
});
