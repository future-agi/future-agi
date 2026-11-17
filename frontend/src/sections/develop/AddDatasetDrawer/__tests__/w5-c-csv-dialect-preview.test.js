import { describe, expect, it } from "vitest";
import { parseImport } from "../importFile";

const alternateDialects = [
  { name: "semicolon", delimiter: ";" },
  { name: "tab", delimiter: "\t" },
  { name: "pipe", delimiter: "|" },
];
const dialects = [{ name: "comma", delimiter: "," }, ...alternateDialects];

// W5-C-004: run these specs on FINAL-1f before applying the parser change.
// Fixtures contain real record separators at runtime, not literal backslash-n.
describe("W5-C-004 hosted CSV dialect preview", () => {
  it("previews the exact semicolon upload without falsely skipping its row", () => {
    const parsed = parseImport(
      "question;answer\nName Paris, France;Paris\n",
      "csv",
    );

    expect(parsed).toEqual({
      columns: ["question", "answer"],
      rows: [["Name Paris, France", "Paris"]],
      records: [{ question: "Name Paris, France", answer: "Paris" }],
      errors: [],
      rejected: [],
      total: 1,
      problem: null,
    });
  });

  it.each(alternateDialects)(
    "preserves commas in unquoted $name fields",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question${delimiter}answer\nName Paris, France${delimiter}Paris\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question", "answer"]);
      expect(parsed.records).toEqual([
        { question: "Name Paris, France", answer: "Paris" },
      ]);
      expect(parsed.rows).toEqual([["Name Paris, France", "Paris"]]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
      expect(parsed.errors).toEqual([]);
      expect(parsed.problem).toBeNull();
    },
  );

  it.each(alternateDialects)(
    "does not collapse plain $name columns into one field",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question${delimiter}answer\nName Paris${delimiter}Paris\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question", "answer"]);
      expect(parsed.records).toEqual([
        { question: "Name Paris", answer: "Paris" },
      ]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
    },
  );

  it.each(dialects)(
    "preserves quoted delimiters, escaped quotes and newlines in $name CSV",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question${delimiter}answer\r\n"First, line\r\nSecond${delimiter}line"${delimiter}"Say ""Paris"""\r\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question", "answer"]);
      expect(parsed.records).toEqual([
        {
          question: `First, line\r\nSecond${delimiter}line`,
          answer: 'Say "Paris"',
        },
      ]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
      expect(parsed.errors).toEqual([]);
    },
  );

  it("honors the hosted quoted-semicolon preference when widths tie", () => {
    const parsed = parseImport(
      "question,locale;answer\nName Paris, France;\"Paris\"\n",
      "csv",
    );

    expect(parsed).toEqual({
      columns: ["question,locale", "answer"],
      rows: [["Name Paris, France", "Paris"]],
      records: [{ "question,locale": "Name Paris, France", answer: "Paris" }],
      errors: [],
      rejected: [],
      total: 1,
      problem: null,
    });
  });

  it.each(alternateDialects)(
    "uses quoted final-field boundaries to break $name width ties",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question,locale${delimiter}answer\nName Paris, France${delimiter}"Paris"\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question,locale", "answer"]);
      expect(parsed.records).toEqual([
        { "question,locale": "Name Paris, France", answer: "Paris" },
      ]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
    },
  );

  it.each(alternateDialects)(
    "uses quoted leading-field boundaries to break $name width ties",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question${delimiter}answer,locale\n"Name Paris"${delimiter}Paris, France\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question", "answer,locale"]);
      expect(parsed.records).toEqual([
        { question: "Name Paris", "answer,locale": "Paris, France" },
      ]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
    },
  );

  it.each(alternateDialects)(
    "uses quoted interior-field boundaries to break $name width ties",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question,locale,kind${delimiter}answer${delimiter}note\nName Paris, France, city${delimiter}"Paris"${delimiter}Capital\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question,locale,kind", "answer", "note"]);
      expect(parsed.records).toEqual([
        {
          "question,locale,kind": "Name Paris, France, city",
          answer: "Paris",
          note: "Capital",
        },
      ]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
    },
  );

  it.each(alternateDialects)(
    "keeps comma precedence for unquoted $name width ties",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question,locale${delimiter}answer\nName Paris, France${delimiter}Paris\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question", `locale${delimiter}answer`]);
      expect(parsed.records).toEqual([
        { question: "Name Paris", [`locale${delimiter}answer`]: ` France${delimiter}Paris` },
      ]);
      expect(parsed.total).toBe(1);
      expect(parsed.rejected).toEqual([]);
    },
  );

  it("ignores quoted-field evidence beyond the first 4096 characters", () => {
    const padding = "x".repeat(4096);
    const parsed = parseImport(
      `question,locale;answer\n${padding}, France;"Paris"\n`,
      "csv",
    );

    expect(parsed.columns).toEqual(["question", "locale;answer"]);
    expect(parsed.records).toEqual([
      { question: padding, "locale;answer": " France;Paris" },
    ]);
    expect(parsed.total).toBe(1);
    expect(parsed.rejected).toEqual([]);
  });

  it("chooses the widest consistent supported dialect", () => {
    const parsed = parseImport(
      "question,locale;answer;note\nName Paris, France;Paris;Capital\n",
      "csv",
    );

    expect(parsed.columns).toEqual(["question,locale", "answer", "note"]);
    expect(parsed.records).toEqual([
      { "question,locale": "Name Paris, France", answer: "Paris", note: "Capital" },
    ]);
    expect(parsed.rejected).toEqual([]);
  });

  it("ignores a wider inconsistent separator in a comma header", () => {
    const parsed = parseImport(
      "question;locale;hint,answer\nName Paris,Paris\n",
      "csv",
    );

    expect(parsed.columns).toEqual(["question;locale;hint", "answer"]);
    expect(parsed.records).toEqual([
      { "question;locale;hint": "Name Paris", answer: "Paris" },
    ]);
    expect(parsed.rejected).toEqual([]);
  });

  it.each(dialects)(
    "keeps genuine $name width errors and physical line numbers",
    ({ delimiter }) => {
      const parsed = parseImport(
        `question${delimiter}answer\n"First, line\nSecond"${delimiter}Paris\nbad${delimiter}row${delimiter}extra\nLast${delimiter}Rome\n`,
        "csv",
      );

      expect(parsed.columns).toEqual(["question", "answer"]);
      expect(parsed.records).toEqual([
        { question: "First, line\nSecond", answer: "Paris" },
        { question: "Last", answer: "Rome" },
      ]);
      expect(parsed.total).toBe(2);
      expect(parsed.rejected).toEqual([
        {
          line: 4,
          reason: "Expected 2 values, found 3",
          text: `bad${delimiter}row${delimiter}extra`,
        },
      ]);
      expect(parsed.errors).toEqual([
        { line: 4, message: "Expected 2 values, found 3" },
      ]);
    },
  );

  it.each(dialects)("keeps empty $name fields", ({ delimiter }) => {
    const parsed = parseImport(
      `question${delimiter}answer\n${delimiter}Paris\nName Rome${delimiter}\n`,
      "csv",
    );

    expect(parsed.columns).toEqual(["question", "answer"]);
    expect(parsed.records).toEqual([
      { question: "", answer: "Paris" },
      { question: "Name Rome", answer: "" },
    ]);
    expect(parsed.total).toBe(2);
    expect(parsed.rejected).toEqual([]);
  });

  it("keeps a single-column file when punctuation occurs only in its header", () => {
    const parsed = parseImport("question;hint\nParis\nRome\n", "csv");

    expect(parsed.columns).toEqual(["question;hint"]);
    expect(parsed.records).toEqual([
      { "question;hint": "Paris" },
      { "question;hint": "Rome" },
    ]);
    expect(parsed.rejected).toEqual([]);
  });
});
