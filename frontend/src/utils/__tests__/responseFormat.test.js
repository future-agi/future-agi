import { describe, it, expect } from "vitest";
import {
  buildResponseFormatMenu,
  canonicalResponseFormat,
  responseFormatLabel,
} from "../responseFormat";

describe("canonicalResponseFormat", () => {
  it("collapses json and json_object onto one key", () => {
    expect(canonicalResponseFormat("json")).toBe("json_object");
    expect(canonicalResponseFormat("json_object")).toBe("json_object");
    expect(canonicalResponseFormat("JSON")).toBe("json_object");
  });

  it("leaves other formats untouched", () => {
    expect(canonicalResponseFormat("text")).toBe("text");
    expect(canonicalResponseFormat("none")).toBe("none");
    expect(canonicalResponseFormat("json_schema")).toBe("json_schema");
  });

  it("passes through non-strings", () => {
    expect(canonicalResponseFormat(undefined)).toBeUndefined();
    const schema = { id: "abc" };
    expect(canonicalResponseFormat(schema)).toBe(schema);
  });
});

describe("responseFormatLabel", () => {
  it("labels known formats", () => {
    expect(responseFormatLabel("json")).toBe("JSON");
    expect(responseFormatLabel("json_object")).toBe("JSON");
    expect(responseFormatLabel("text")).toBe("Text");
  });

  it("start-cases unknown formats", () => {
    expect(responseFormatLabel("structured_output")).toBe("Structured Output");
  });
});

describe("buildResponseFormatMenu", () => {
  const DEVELOP_DEFAULTS = [
    { value: "text", label: "Text" },
    { value: "json_object", label: "JSON" },
    { value: "none", label: "None" },
  ];

  it("does not add a second JSON row when the backend advertises `json`", () => {
    const menu = buildResponseFormatMenu({
      defaults: DEVELOP_DEFAULTS,
      modelResponseFormat: [{ value: "json" }, { value: "text" }],
    });

    expect(menu).toEqual(DEVELOP_DEFAULTS);
  });

  it("keeps the surface's own spelling when deduping", () => {
    const menu = buildResponseFormatMenu({
      defaults: [{ value: "json", label: "JSON" }],
      modelResponseFormat: [{ value: "json_object" }],
    });

    expect(menu).toEqual([{ value: "json", label: "JSON" }]);
  });

  it("appends custom schemas between defaults and backend formats", () => {
    const menu = buildResponseFormatMenu({
      defaults: DEVELOP_DEFAULTS,
      responseSchema: [{ id: "schema-id", name: "asdasd" }],
      modelResponseFormat: [{ value: "json" }],
    });

    expect(menu).toEqual([
      ...DEVELOP_DEFAULTS,
      { label: "asdasd", value: "schema-id" },
    ]);
  });

  it("keeps backend formats that are genuinely new", () => {
    const menu = buildResponseFormatMenu({
      defaults: DEVELOP_DEFAULTS,
      modelResponseFormat: [{ value: "structured_output" }],
    });

    expect(menu).toContainEqual({
      label: "Structured Output",
      value: "structured_output",
    });
  });

  it("builds a backend-only menu when no defaults are given", () => {
    const menu = buildResponseFormatMenu({
      modelResponseFormat: [{ value: "json" }, { value: "text" }],
    });

    expect(menu).toEqual([
      { label: "JSON", value: "json" },
      { label: "Text", value: "text" },
    ]);
  });
});
