import { describe, expect, it } from "vitest";
import {
  DEFAULT_SESSION_TURN_FIELDS,
  normalizeSessionTurnFields,
  resolveSessionTurnFields,
  mergeSessionTurnFields,
  sessionTurnFieldError,
} from "./sessionTurnFields";

const field = (path, label = path) => ({ kind: "value", label, path });

describe("session turn fields", () => {
  it("distinguishes legacy preferences from deliberately empty selection", () => {
    expect(normalizeSessionTurnFields(undefined)).toEqual(
      DEFAULT_SESSION_TURN_FIELDS,
    );
    expect(normalizeSessionTurnFields(null)).toEqual(
      DEFAULT_SESSION_TURN_FIELDS,
    );
    expect(normalizeSessionTurnFields([])).toEqual([]);
  });

  it("resolves nested JSON text, negative indices, quoted keys and wildcards", () => {
    const trace = {
      input: JSON.stringify({
        messages: [{ content: "first" }, { content: "last" }],
      }),
      output: { "answer.text": JSON.stringify({ ok: false }) },
    };
    expect(
      resolveSessionTurnFields(trace, [
        field("input.messages[-1].content"),
        field('output["answer.text"].ok'),
        field("input.messages[*].content"),
      ]).map((item) => item.value),
    ).toEqual(["last", false, ["first", "last"]]);
  });

  it("preserves zero, false, empty text and null; only missing paths are unavailable", () => {
    const fields = ["zero", "false", "empty", "null", "missing"].map((key) =>
      field(`input.${key}`),
    );
    const resolved = resolveSessionTurnFields(
      { input: { zero: 0, false: false, empty: "", null: null } },
      fields,
    );
    expect(resolved.map(({ found }) => found)).toEqual([
      true,
      true,
      true,
      true,
      false,
    ]);
    expect(resolved.slice(0, 4).map(({ value }) => value)).toEqual([
      0,
      false,
      "",
      null,
    ]);
  });

  it("validates descriptors and safely falls back for malformed stored configuration", () => {
    for (const invalid of [
      null,
      {},
      field("metadata.foo"),
      field("input["),
      field("input", ""),
      { kind: "metric", metric: "secret" },
    ]) {
      expect(sessionTurnFieldError(invalid)).toBeTruthy();
      expect(normalizeSessionTurnFields([invalid])).toEqual(
        DEFAULT_SESSION_TURN_FIELDS,
      );
    }
    expect(sessionTurnFieldError(field("output.answer"))).toBeNull();
    expect(
      sessionTurnFieldError({ kind: "metric", metric: "total_cost" }),
    ).toBeNull();
  });

  it("merges only the preference without losing unrelated saved-view configuration", () => {
    const config = {
      filters: [{ column_id: "status" }],
      display: { cellHeight: "Tall", futureField: true },
      sort: { field: "id" },
    };
    expect(mergeSessionTurnFields(config, [])).toEqual({
      ...config,
      display: { ...config.display, sessionTurnFields: [] },
    });
    expect(config.display).not.toHaveProperty("sessionTurnFields");
  });
});
