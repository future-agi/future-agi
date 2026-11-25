import { describe, expect, it } from "vitest";
import { extractJinjaVariables } from "../jinjaVariables";
import { extractVariables, extractVariablesFromMessages } from "../utils";

describe("Jinja variable extraction", () => {
  it("returns sorted, unique input names synchronously", () => {
    expect(extractJinjaVariables("{{ z }} {{ a }} {{ z }}")).toEqual([
      "a",
      "z",
    ]);
  });

  it("ignores loop locals and filters while retaining external inputs", () => {
    expect(
      extractJinjaVariables(
        "{% for item in items %}{{ loop.index }} {{ item.name | upper }} {{ suffix }}{% endfor %}",
      ),
    ).toEqual(["items", "suffix"]);
  });

  it("ignores assigned locals while retaining their input expressions", () => {
    expect(
      extractJinjaVariables("{% set display = name %}{{ display }}"),
    ).toEqual(["name"]);
  });

  it("returns an empty list for missing or incomplete templates", () => {
    expect(extractJinjaVariables(null)).toEqual([]);
    expect(extractJinjaVariables(undefined)).toEqual([]);
    expect(extractJinjaVariables("{{ unfinished")).toEqual([]);
  });

  it("keeps the shared utility wrappers synchronous for both formats", () => {
    expect(extractVariables("<b>{{ name }}</b>")).toEqual(["name"]);
    expect(extractVariables("<b>{{ name | upper }}</b>", "jinja")).toEqual([
      "name",
    ]);
    expect(
      extractVariablesFromMessages(
        "{{ name }}",
        [{ content: "{{ profile.name }} {{ name | upper }}" }],
        "jinja",
      ),
    ).toEqual(["name", "profile"]);
  });
});
