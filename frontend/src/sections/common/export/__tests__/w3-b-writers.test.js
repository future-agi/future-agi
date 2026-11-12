// W3-6 B1: one golden file per format (golden/*, made from goldenFixture.js).
// golden.parquet.b64 is also read back by pyarrow and duckdb on a cloud VM
// (golden-parquet-expected.json).
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import { Buffer } from "node:buffer";
import path from "node:path";
import {
  conversationTurns,
  exportFileName,
  hasExpected,
  openAiToolCalls,
  parquetTypeOf,
  toCsv,
  toJson,
  toJsonl,
  toOpenAiEvals,
  toOpenAiFineTune,
  toParquet,
} from "../writers";
import {
  GOLDEN_COLUMNS,
  GOLDEN_META,
  GOLDEN_ROLES,
  GOLDEN_ROWS,
} from "./goldenFixture";

const dir = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "golden",
);
const golden = (name) => fs.readFileSync(path.join(dir, name), "utf8");

describe("W3-6 B1 · writers golden files", () => {
  it("CSV is RFC 4180: CRLF lines, quoted commas, quotes, line breaks and edge spaces", () => {
    const out = toCsv(GOLDEN_ROWS, GOLDEN_COLUMNS);
    expect(out).toBe(golden("golden.csv"));
    expect(out.split("\r\n")[0]).toBe(
      "row_id,customer_message,conversation,expected_reply,expected_tool_calls,order_total,Groundedness,Correct?,row_metadata",
    );
    expect(out).toContain(
      '"Order A-3920 says ""delivered"", but nothing arrived, sorry"',
    );
    expect(out).toContain('"  Où est ma commande ? — Zoë  "');
    expect(out).toContain('"Refund\r\nplease, ✓ 日本"');
    expect(out.endsWith("\r\n")).toBe(true);
  });

  it("CSV context line is off by default and one '# …' line when asked", () => {
    expect(toCsv(GOLDEN_ROWS, GOLDEN_COLUMNS).startsWith("#")).toBe(false);
    const out = toCsv(GOLDEN_ROWS, GOLDEN_COLUMNS, {
      contextLine: "Golden fixture · v3\nsplit smoke",
    });
    expect(out).toBe(golden("golden-context.csv"));
    expect(out.split("\r\n")[0]).toBe("# Golden fixture · v3 split smoke");
  });

  it("JSON is one document {meta, rows}", () => {
    const out = toJson({ meta: GOLDEN_META, rows: GOLDEN_ROWS });
    expect(out).toBe(golden("golden-json.txt"));
    expect(JSON.parse(out)).toEqual({ meta: GOLDEN_META, rows: GOLDEN_ROWS });
  });

  it("JSONL is one object per line and parses back to the rows", () => {
    const out = toJsonl(GOLDEN_ROWS);
    expect(out).toBe(golden("golden.jsonl"));
    expect(
      out
        .trimEnd()
        .split("\n")
        .map((l) => JSON.parse(l)),
    ).toEqual(GOLDEN_ROWS);
    expect(toJsonl([])).toBe("");
  });

  it("Parquet bytes equal the golden file (read back by pyarrow + duckdb)", () => {
    const bytes = toParquet(GOLDEN_ROWS, GOLDEN_COLUMNS);
    const b64 =
      Buffer.from(bytes).toString("base64").replace(/.{76}/g, "$&\n") + "\n";
    expect(b64).toBe(golden("golden.parquet.b64"));
    const text = Buffer.from(bytes);
    expect(text.subarray(0, 4).toString()).toBe("PAR1");
    expect(text.subarray(-4).toString()).toBe("PAR1");
    const footer = text.readUInt32LE(text.length - 8);
    expect(footer).toBeGreaterThan(0);
    expect(footer).toBeLessThan(text.length - 12);
  });

  it("Parquet types: booleans BOOLEAN, whole numbers INT64, others DOUBLE, the rest UTF8", () => {
    const expected = JSON.parse(golden("golden-parquet-expected.json"));
    expect(expected.columns.map((c) => [c.name, c.type])).toEqual([
      ["row_id", "BYTE_ARRAY"],
      ["customer_message", "BYTE_ARRAY"],
      ["conversation", "BYTE_ARRAY"],
      ["expected_reply", "BYTE_ARRAY"],
      ["expected_tool_calls", "BYTE_ARRAY"],
      ["order_total", "INT64"],
      ["Groundedness", "DOUBLE"],
      ["Correct?", "BOOLEAN"],
      ["row_metadata", "BYTE_ARRAY"],
    ]);
    expect(parquetTypeOf([null, undefined])).toBe("BYTE_ARRAY");
    expect(parquetTypeOf([1, 2.5])).toBe("DOUBLE");
    expect(parquetTypeOf([1, "a"])).toBe("BYTE_ARRAY");
  });

  it("OpenAI fine-tune JSONL: system?, earlier turns, user, assistant (+ tool_calls); rows with no expected are left out", () => {
    const out = toOpenAiFineTune(GOLDEN_ROWS, GOLDEN_ROLES);
    expect(out).toBe(golden("golden-finetune.jsonl"));
    const lines = out
      .trimEnd()
      .split("\n")
      .map((l) => JSON.parse(l));
    expect(lines).toHaveLength(2);
    lines.forEach((l) => {
      const roles = l.messages.map((m) => m.role);
      expect(roles[0]).toBe("system");
      expect(roles).toContain("user");
      expect(roles.at(-1)).toBe("assistant");
    });
    expect(lines[0].messages.at(-1).tool_calls).toEqual([
      {
        id: "call_1",
        type: "function",
        function: { name: "lookup_order", arguments: '{"order_id":"A-3920"}' },
      },
    ]);
    expect(
      GOLDEN_ROWS.filter((r) => hasExpected(r, GOLDEN_ROLES)),
    ).toHaveLength(2);
  });

  it("OpenAI Evals JSONL: {input:[messages], ideal}", () => {
    const out = toOpenAiEvals(GOLDEN_ROWS, GOLDEN_ROLES);
    expect(out).toBe(golden("golden-evals.jsonl"));
    const lines = out
      .trimEnd()
      .split("\n")
      .map((l) => JSON.parse(l));
    expect(lines.map((l) => l.ideal)).toEqual([
      GOLDEN_ROWS[0].exp,
      GOLDEN_ROWS[2].exp,
    ]);
    expect(lines[0].input.at(-1)).toEqual({
      role: "user",
      content: GOLDEN_ROWS[0].msg,
    });
  });

  it("a conversation input maps its turns; tool calls need {name}", () => {
    const conv = JSON.stringify([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]);
    expect(conversationTurns(conv)).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]);
    expect(conversationTurns("plain text")).toBeNull();
    expect(conversationTurns([{ role: "robot", content: "x" }])).toBeNull();
    expect(openAiToolCalls([{ foo: 1 }])).toBeNull();
    const ft = toOpenAiFineTune([{ q: conv, a: "ok" }], {
      user: "q",
      assistant: "a",
    });
    expect(JSON.parse(ft).messages.map((m) => m.role)).toEqual([
      "user",
      "assistant",
      "assistant",
    ]);
  });

  it("file names follow dataset, version and split", () => {
    expect(
      exportFileName(["Support agent - golden set", "v3", "smoke"], "jsonl"),
    ).toBe("support-agent-golden-set-v3-smoke.jsonl");
    expect(
      exportFileName(["Support agent - golden set", "v6"], "openai_ft"),
    ).toBe("support-agent-golden-set-v6-finetune.jsonl");
    expect(exportFileName(["GS"], "parquet")).toBe("gs.parquet");
  });
});
