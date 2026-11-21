import { describe, it, expect } from "vitest";
import { extractJinjaVariables } from "../jinjaVariables";

// Every expected list below is what jinja2.meta.find_undeclared_variables()
// returns for the same template on jinja2 3.1.6, the version the backend
// (model_hub/utils/jinja_variables.py) is locked to. The editors use this
// function to decide which inputs to ask for, so it has to agree with the
// backend that renders the template.
describe("extractJinjaVariables", () => {
  it("returns sorted root names of plain, dotted and filtered references", () => {
    expect(
      extractJinjaVariables(
        "{{ user.name | upper }} {{ items[0] }} {{ topic }}",
      ),
    ).toEqual(["items", "topic", "user"]);
  });

  it("returns an empty list for empty or unparseable templates", () => {
    expect(extractJinjaVariables("")).toEqual([]);
    expect(extractJinjaVariables(null)).toEqual([]);
    expect(extractJinjaVariables("{% for x in %}")).toEqual([]);
  });

  describe("for loops", () => {
    it("treats the loop target and loop as local to the body", () => {
      expect(
        extractJinjaVariables(
          "{% for doc in documents %}{{ doc.text }} {{ loop.index }}{% endfor %}",
        ),
      ).toEqual(["documents"]);
    });

    it("collects inputs used only in the else branch", () => {
      expect(
        extractJinjaVariables(
          "{% for doc in documents %}{{ doc }}{% else %}Nothing found for {{ query }}{% endfor %}",
        ),
      ).toEqual(["documents", "query"]);
    });

    it("does not keep the loop target after endfor", () => {
      expect(
        extractJinjaVariables(
          "{% for message in history %}{{ message.content }}{% endfor %}User: {{ message }}",
        ),
      ).toEqual(["history", "message"]);
    });

    it("does not expose the loop target or loop to the else branch", () => {
      expect(
        extractJinjaVariables(
          "{% for item in items %}{{ item }}{% else %}{{ item }} {{ loop }}{% endfor %}",
        ),
      ).toEqual(["item", "items", "loop"]);
    });

    it("does not keep names set inside the body", () => {
      expect(
        extractJinjaVariables(
          "{% for x in xs %}{% set last = x %}{% endfor %}{{ last }}",
        ),
      ).toEqual(["last", "xs"]);
    });

    it("reads the iterable in the enclosing scope", () => {
      expect(
        extractJinjaVariables("{% for row in row %}{{ row }}{% endfor %}"),
      ).toEqual(["row"]);
    });
  });

  describe("set", () => {
    it("reads the value before assigning the target", () => {
      expect(
        extractJinjaVariables(
          "{% set context = context | trim %}{{ context }}",
        ),
      ).toEqual(["context"]);
    });

    it("treats the target as local after the assignment", () => {
      expect(
        extractJinjaVariables(
          "{% set greeting = 'Hi ' ~ name %}{{ greeting }}",
        ),
      ).toEqual(["name"]);
    });

    it("gives a block set its own scope", () => {
      expect(
        extractJinjaVariables(
          "{% set intro %}{% set tmp = 1 %}Dear {{ name }} {{ intro }}{% endset %}{{ intro }} {{ tmp }}",
        ),
      ).toEqual(["name", "tmp"]);
    });
  });

  it("does not report Jinja2 globals such as range", () => {
    expect(
      extractJinjaVariables(
        "{% for i in range(n) %}{{ cycler(a, b).next() }}{% endfor %}{{ dict(k=v) }}",
      ),
    ).toEqual(["a", "b", "n", "v"]);
  });

  it("does not report keyword argument names", () => {
    expect(
      extractJinjaVariables(
        "{{ payload | tojson(indent=2) }} {{ document | truncate(length=500, end=suffix) }}",
      ),
    ).toEqual(["document", "payload", "suffix"]);
  });
});
