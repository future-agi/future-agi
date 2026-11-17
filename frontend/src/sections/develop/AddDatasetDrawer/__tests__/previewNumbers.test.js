// F32 numeric policy, preview side. The Upload dialog reads JSON numbers with
// JSON.parse, so it shows every whole number up to Number.MAX_SAFE_INTEGER
// exactly and cannot show larger ones exactly. The server reader
// (futureagi/model_hub/services/file_import_json.py) refuses a record holding
// a larger one rather than store a value the preview did not show, and stores
// every other number with the text previewed here
// (futureagi/model_hub/tests/test_upload_json_cells.py uses the same files).
import { describe, expect, it } from "vitest";
import { parseImport } from "../importFile";

const shown = (text, ext = "json") => parseImport(text, ext).rows;

describe("Upload preview of JSON numbers (F32)", () => {
  it("shows every safe whole number exactly", () => {
    const p = parseImport(
      '{"max": 9007199254740991, "min": -9007199254740991, "big": 1e21, "ids": [9007199254740991]}',
      "json",
    );

    expect(Number.MAX_SAFE_INTEGER).toBe(9007199254740991);
    expect(p.columns).toEqual(["max", "min", "big", "ids"]);
    expect(p.rows).toEqual([
      ["9007199254740991", "-9007199254740991", "1e+21", "[9007199254740991]"],
    ]);
  });

  it("cannot show larger whole numbers exactly, so the server refuses them", () => {
    expect(shown('{"id": 9007199254740993}')).toEqual([["9007199254740992"]]);
    expect(shown('{"id": 9007199254740992}')).toEqual([["9007199254740992"]]);
    expect(shown('{"rows": [{"n": 1000000000000000000000}]}')).toEqual([
      ["1e+21"],
    ]);
    expect(
      shown(
        '{"a": "x"}\n{"a": "y", "meta": {"ids": [-9007199254740993]}}\n',
        "jsonl",
      ),
    ).toEqual([
      ["x", ""],
      ["y", '{"ids":[-9007199254740992]}'],
    ]);
  });
});
