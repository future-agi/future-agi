import { describe, expect, it } from "vitest";
import {
  dagCompactLine,
  dagInstructionsOf,
  dagSummaryOf,
  firstJudgeOf,
  numbersOf,
  pathKeyOf,
  pathLabelOf,
  runCheck,
  treeJudgeOf,
  validateDag,
  variablesOf,
} from "../dagModel";
import {
  blankDag,
  graphOf,
  pointEdge,
  removeNode,
  setChoice,
} from "src/sections/evals/types/dagEditorModel";
import { typedOwnJudgeOf } from "../typedEvalTypes";

// W7 A2 (W7-3): the decision tree's pure model — validation (verbatim
// messages), the deterministic checks, the outline and the editor helpers.
const TONE = "0e000000-0000-4000-8000-000000000003";
const refundPath = () => ({
  schema: "fi-dag-v1",
  root: "n1",
  output_type: "score",
  nodes: [
    {
      id: "n1",
      kind: "judge",
      question: "Does the reply say a refund was issued for {{input}}?",
      judge_model: "gpt-4o-mini",
      answers: [
        { label: "Yes", next: "n2" },
        { label: "No", next: "n3" },
      ],
    },
    {
      id: "n2",
      kind: "check",
      check: { type: "regex", on: "output", pattern: "A-\\d{4}" },
      answers: [
        { label: "Pass", next: "l1" },
        { label: "Fail", next: "l2" },
      ],
    },
    {
      id: "n3",
      kind: "eval",
      template_id: TONE,
      answers: [
        { label: "Pass", next: "l3" },
        { label: "Fail", next: "l4" },
      ],
    },
    { id: "l1", kind: "leaf", label: "Refund with order id", score: 1 },
    { id: "l2", kind: "leaf", label: "Refund without order id", score: 0.3 },
    { id: "l3", kind: "leaf", label: "No refund, polite", score: 0.6 },
    { id: "l4", kind: "leaf", label: "No refund, rude", score: 0 },
  ],
});
const nodeOf = (dag, id) => dag.nodes.find((n) => n.id === id);

describe("validateDag", () => {
  it("a valid tree has no errors", () => {
    expect(validateDag(refundPath())).toEqual([]);
    expect(validateDag(refundPath(), { mapping: { input: "col-1" } })).toEqual(
      [],
    );
  });

  it("every message, verbatim", () => {
    const cases = [
      [
        (d) => {
          d.root = "";
        },
        "Start from one question",
      ],
      [
        (d) => {
          d.root = "l1";
        },
        "Start from one question",
      ],
      [
        (d) => {
          nodeOf(d, "l4").id = "l3";
        },
        "Node ids must be unique",
      ],
      [
        (d) => {
          nodeOf(d, "n2").answers[1].next = "";
        },
        "2 has an answer that leads nowhere",
      ],
      [
        (d) => {
          nodeOf(d, "n3").answers[1].next = "n1";
        },
        "3 loops back to 1",
      ],
      [
        (d) =>
          d.nodes.splice(3, 0, {
            id: "n4",
            kind: "judge",
            question: "Polite?",
            answers: [
              { label: "Yes", next: "l1" },
              { label: "No", next: "l2" },
            ],
          }),
        "4 can't be reached from the first question",
      ],
      [
        (d) => {
          d.nodes = d.nodes.filter((n) => n.kind !== "leaf");
        },
        "Every path must end in a score",
      ],
      [
        (d) => {
          nodeOf(d, "n1").answers.push({ label: "Maybe", next: "n3" });
        },
        "A yes/no question has exactly two answers",
      ],
      [
        (d) => {
          const n = nodeOf(d, "n1");
          delete n.answers;
          n.options = [{ label: "Only", next: "n2" }];
        },
        "A choice question has 2 to 10 options",
      ],
      [
        (d) => {
          nodeOf(d, "l1").score = 1.5;
        },
        "Scores are between 0 and 1",
      ],
      [
        (d) => {
          nodeOf(d, "n1").question = "  ";
        },
        "Write the question",
      ],
      [
        (d) => {
          nodeOf(d, "n2").check.pattern = "A-[";
        },
        "A-[ is not a valid pattern",
      ],
    ];
    cases.forEach(([mutate, message]) => {
      const d = refundPath();
      mutate(d);
      expect(validateDag(d), message).toContain(message);
    });
    expect(validateDag(refundPath(), { mapping: {} })).toContain(
      "{{input}} is not mapped",
    );
  });

  it("eleven options is too many", () => {
    const d = refundPath();
    const n = nodeOf(d, "n1");
    delete n.answers;
    n.options = Array.from({ length: 11 }, (_, i) => ({
      label: `o${i}`,
      next: "n2",
    }));
    expect(validateDag(d)).toContain("A choice question has 2 to 10 options");
  });
});

describe("runCheck — every check type, free and deterministic", () => {
  const out = "Your refund for order A-7781 was issued.";
  it("contains / not_contains with the case toggle", () => {
    expect(
      runCheck({ type: "contains", value: "REFUND" }, { output: out }).passed,
    ).toBe(true);
    expect(
      runCheck(
        { type: "contains", value: "REFUND", case_sensitive: true },
        { output: out },
      ).passed,
    ).toBe(false);
    expect(
      runCheck({ type: "not_contains", value: "sorry" }, { output: out })
        .passed,
    ).toBe(true);
  });
  it("regex keeps the match; a variable can be checked instead of the output", () => {
    const r = runCheck({ type: "regex", pattern: "A-\\d{4}" }, { output: out });
    expect(r).toMatchObject({ passed: true, match: "A-7781" });
    expect(
      runCheck(
        { type: "regex", pattern: "A-\\d{4}", on: "input" },
        { output: out, values: { input: "no id" } },
      ).passed,
    ).toBe(false);
  });
  it("equals_expected is trimmed and case-folded; no expected value skips", () => {
    expect(
      runCheck(
        { type: "equals_expected" },
        { output: " Yes ", values: { expected: "yes" } },
      ).passed,
    ).toBe(true);
    const s = runCheck(
      { type: "equals_expected" },
      { output: "Yes", values: {} },
    );
    expect(s.passed).toBeNull();
    expect(s.skipped).toBe("no expected value");
  });
  it("json_path exists / equals", () => {
    const o = JSON.stringify({ order: { id: "A-1", items: [{ sku: "J" }] } });
    expect(
      runCheck({ type: "json_path", path: "$.order.id" }, { output: o }).passed,
    ).toBe(true);
    expect(
      runCheck(
        {
          type: "json_path",
          path: "$.order.items[0].sku",
          op: "equals",
          value: "J",
        },
        { output: o },
      ).passed,
    ).toBe(true);
    expect(
      runCheck({ type: "json_path", path: "$.order.total" }, { output: o })
        .passed,
    ).toBe(false);
    expect(
      runCheck({ type: "json_path", path: "$.a" }, { output: "not json" })
        .passed,
    ).toBe(false);
  });
  it("tool_called reads the try's tool calls; none skips", () => {
    const attempt = { tool_calls: [{ name: "issue_refund" }] };
    expect(
      runCheck(
        { type: "tool_called", value: "issue_refund" },
        { output: out, attempt },
      ).passed,
    ).toBe(true);
    expect(
      runCheck(
        { type: "tool_called", value: "lookup" },
        { output: out, attempt },
      ).passed,
    ).toBe(false);
    expect(
      runCheck(
        { type: "tool_called", value: "lookup" },
        { output: out, attempt: { tool_calls: [] } },
      ).passed,
    ).toBeNull();
  });
  it("length ≤ / ≥ the author's number", () => {
    expect(
      runCheck({ type: "length", op: "lte", n: out.length }, { output: out })
        .passed,
    ).toBe(true);
    expect(
      runCheck(
        { type: "length", op: "gte", n: out.length + 1 },
        { output: out },
      ).passed,
    ).toBe(false);
  });
});

describe("the outline, numbering and paths", () => {
  it("dagInstructionsOf writes a numbered outline listing every variable", () => {
    const text = dagInstructionsOf(refundPath(), {
      evalNames: { [TONE]: "Tone" },
    });
    expect(text).toContain("{{output}}");
    expect(text).toContain("{{input}}");
    expect(text).toMatch(
      /^1\. Does the reply say a refund was issued for \{\{input\}\}\? — Yes → question 2 · No → question 3$/m,
    );
    expect(text).toMatch(
      /^2\. Check: output matches A-\\d\{4\} — Pass → Refund with order id \(1\.00\)/m,
    );
    expect(text).toMatch(/^3\. Eval: Tone — /m);
    expect(variablesOf(refundPath())).toEqual(["input"]);
  });
  it("an Equals expected check reads {{expected}}: Test maps it like any variable", () => {
    const dag = refundPath();
    dag.nodes[1].check = { type: "equals_expected", on: "output" };
    expect(variablesOf(dag)).toEqual(["input", "expected"]);
    expect(validateDag(dag, { mapping: { input: "c-in" } })).toContain(
      "{{expected}} is not mapped",
    );
    expect(
      validateDag(dag, { mapping: { input: "c-in", expected: "c-exp" } }),
    ).toEqual([]);
    expect(dagInstructionsOf(dag)).toMatch(
      /Reads: \{\{input\}\} \{\{expected\}\}$/,
    );
  });
  it("a question without its own judge asks the template's model, else the first question's", () => {
    const dag = refundPath();
    expect(firstJudgeOf(dag)).toBe(dag.nodes[0].judge_model ?? null);
    dag.nodes[0].judge_model = "claude-haiku-4-5";
    expect(treeJudgeOf(dag)).toBe("claude-haiku-4-5");
    expect(treeJudgeOf(dag, { custom_template: { model: "gpt-4.1" } })).toBe(
      "gpt-4.1",
    );
    delete dag.nodes[0].judge_model;
    expect(treeJudgeOf(dag)).toBeNull();
  });
  it("the one-line summary counts the tree's nodes", () => {
    expect(dagSummaryOf(refundPath())).toBe(
      "Decision tree · 3 questions (1 judged, 1 check, 1 eval) · 4 scores",
    );
  });
  it("numbers, path keys, labels and the compact line", () => {
    expect(numbersOf(refundPath())).toEqual({ n1: 1, n2: 2, n3: 3 });
    const path = [
      { node_id: "n1", n: 1, answer: "Yes" },
      { node_id: "n2", n: 2, answer: "Fail" },
    ];
    const leaf = { id: "l2", label: "Refund without order id", score: 0.3 };
    expect(pathKeyOf(path, leaf)).toBe("n1:Yes>n2:Fail>l2");
    expect(pathLabelOf(path, leaf)).toBe(
      "1 Yes → 2 Fail → Refund without order id",
    );
    expect(dagCompactLine({ path_label: pathLabelOf(path, leaf), leaf })).toBe(
      "Path: 1 Yes → 2 Fail → Refund without order id (0.30)",
    );
  });
});

describe("editor helpers", () => {
  it("a blank tree starts from one question and needs its answers", () => {
    const d = blankDag();
    expect(d.root).toBe("n1");
    expect(validateDag(d)).toContain("Write the question");
    expect(validateDag(d)).toContain("1 has an answer that leads nowhere");
  });
  it("Goes to › New score appends a score and links it; removing it unlinks", () => {
    const d = pointEdge(blankDag(), "n1", 0, "new:leaf");
    const leaf = d.nodes.find((n) => n.kind === "leaf");
    expect(d.nodes[0].answers[0].next).toBe(leaf.id);
    const r = removeNode(d, leaf.id);
    expect(r.nodes[0].answers[0].next).toBe("");
  });
  it("a choice question keeps its targets; back to yes/no too", () => {
    const d = setChoice(refundPath(), "n1", true);
    expect(nodeOf(d, "n1").options.map((o) => o.next)).toEqual(["n2", "n3"]);
    expect(validateDag(d)).toEqual([]);
    const back = setChoice(d, "n1", false);
    expect(nodeOf(back, "n1").answers.map((a) => [a.label, a.next])).toEqual([
      ["Yes", "n2"],
      ["No", "n3"],
    ]);
  });
  it("a choice question drops a decision judge (yes/no only) and keeps an LLM judge", () => {
    const withJudge = (m) => ({
      ...refundPath(),
      nodes: refundPath().nodes.map((n) =>
        n.id === "n1" ? { ...n, judge_model: m } : n,
      ),
    });
    expect(
      nodeOf(setChoice(withJudge("jev-1.13.0"), "n1", true), "n1"),
    ).not.toHaveProperty("judge_model");
    expect(
      nodeOf(setChoice(withJudge("gpt-4o"), "n1", true), "n1").judge_model,
    ).toBe("gpt-4o");
  });
  it("a typed card names the one judge its own questions ask, none for a tree of checks", () => {
    expect(typedOwnJudgeOf({ judge_models: [] })).toBeNull();
    expect(typedOwnJudgeOf({ judge_models: [null] })).toBeNull();
    expect(
      typedOwnJudgeOf({
        judge_models: ["gpt-4o-mini", "gpt-4o-mini", null],
      }),
    ).toBe("gpt-4o-mini");
    expect(
      typedOwnJudgeOf({ judge_models: ["gpt-4o-mini", "gpt-4o"] }),
    ).toBeNull();
    expect(typedOwnJudgeOf(null)).toBeNull();
  });
  it("the graph is laid out by depth and highlights the path", () => {
    const g = graphOf(refundPath(), { highlight: new Set(["n1", "n2", "l2"]) });
    const y = Object.fromEntries(g.nodes.map((n) => [n.id, n.position.y]));
    expect(y.n1).toBeLessThan(y.n2);
    expect(y.n2).toBe(y.n3);
    expect(y.l1).toBeGreaterThan(y.n2);
    expect(g.nodes.find((n) => n.id === "l2").className).toContain(
      "dag-on-path",
    );
    expect(g.edges).toHaveLength(6);
  });
});
