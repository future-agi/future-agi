import { describe, it, expect } from "vitest";
import { createSseParser } from "../parseSse";

/** Feed a parser a list of raw chunks and collect everything it emits. */
const drain = (chunks) => {
  const seen = [];
  const parser = createSseParser((event) => seen.push(event));
  chunks.forEach((chunk) => parser.push(chunk));
  parser.end();
  return seen;
};

describe("createSseParser", () => {
  it("emits one event per complete data line", () => {
    const seen = drain([
      'data: {"kind":"text","text":"reading"}\n\n',
      'data: {"kind":"done"}\n\n',
    ]);
    expect(seen.map((e) => e.kind)).toEqual(["text", "done"]);
  });

  it("reassembles an event split across chunk boundaries", () => {
    const seen = drain(['data: {"kind":"te', 'xt","text":"hello"}\n\n']);
    expect(seen).toEqual([{ kind: "text", text: "hello" }]);
  });

  it("handles several events arriving in a single chunk", () => {
    const seen = drain([
      'data: {"kind":"tool","tool":"save_world"}\n\ndata: {"kind":"result","text":"ok"}\n\n',
    ]);
    expect(seen.map((e) => e.kind)).toEqual(["tool", "result"]);
  });

  it("ignores keep-alive blanks and comment lines", () => {
    const seen = drain(['\n', ': keep-alive\n\n', 'data: {"kind":"done"}\n\n']);
    expect(seen).toEqual([{ kind: "done" }]);
  });

  it("skips a malformed payload instead of throwing away the rest of the stream", () => {
    const seen = drain([
      "data: {not json}\n\n",
      'data: {"kind":"done"}\n\n',
    ]);
    expect(seen).toEqual([{ kind: "done" }]);
  });

  it("emits a trailing event that never got its blank line", () => {
    const seen = drain(['data: {"kind":"status"}']);
    expect(seen).toEqual([{ kind: "status" }]);
  });
});
