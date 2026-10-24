import { describe, expect, it } from "vitest";
import {
  citationBudget,
  highlightTitle,
  normalizeFindings,
  refsInText,
  splitFirstSentence,
  verificationNote,
} from "../insightsFindings";
import runFixture from "./fixtures/run_sessions_failures.json";
import enrichedFixture from "./fixtures/run_sessions_failures_enriched.json";
import omegaFixture from "./fixtures/run_sessions_failures_omega.json";

const legacy = runFixture.result.result.summary;
const enriched = enrichedFixture.result.result.summary;
const omega = omegaFixture.result.result.summary;

describe("splitFirstSentence", () => {
  it("splits at the first sentence end", () => {
    expect(
      splitFirstSentence("Harbor loops on lookups. Add a handoff (#3)."),
    ).toEqual({
      headline: "Harbor loops on lookups.",
      rest: "Add a handoff (#3).",
    });
  });

  it("does not split inside decimals, citations or abbreviations", () => {
    expect(
      splitFirstSentence(
        "8 conversations had a median latency of 11.7s, e.g. during outages (#5, #6). Give a fallback.",
      ),
    ).toEqual({
      headline:
        "8 conversations had a median latency of 11.7s, e.g. during outages (#5, #6).",
      rest: "Give a fallback.",
    });
  });

  it("keeps a closing bracket or quote with the headline", () => {
    expect(
      splitFirstSentence('It said "check back later." Then it stopped.'),
    ).toEqual({
      headline: 'It said "check back later."',
      rest: "Then it stopped.",
    });
  });

  it("returns the whole text when there is one sentence", () => {
    expect(splitFirstSentence("  One sentence only  ")).toEqual({
      headline: "One sentence only",
      rest: "",
    });
    expect(splitFirstSentence("")).toEqual({ headline: "", rest: "" });
  });
});

describe("normalizeFindings", () => {
  it("reads the engine's findings with severity, action and refs", () => {
    const findings = normalizeFindings(enriched);
    expect(findings).toHaveLength(5);
    expect(findings[0]).toMatchObject({
      severity: "high",
      action:
        "Add a handoff after two failed lookups or one explicit request for a human.",
      categoryIds: ["3ffe0720fc9b"],
      legacy: false,
    });
    // #11 is written in the detail; #3 is only in refs, so it trails.
    expect(findings[0].trailingRefs).toEqual([3]);
    expect(findings[0].evidence[0].quote).toBe(
      "no. a human. agent. representative",
    );
    expect(findings[4].severity).toBe("low");
  });

  it("turns legacy key points into findings: first sentence bold, rest detail", () => {
    const findings = normalizeFindings(legacy);
    expect(findings).toHaveLength(5);
    expect(findings[0].headline).toBe(
      "Unresponsive support is the most damaging failure: 26 conversations (36%) from 15 users, all ended unresolved or abandoned and all with negative sentiment (1.5 average).",
    );
    expect(findings[0].detail).toMatch(/^Harbor loops and never escalates/);
    expect(findings[0]).toMatchObject({
      severity: null,
      action: "",
      legacy: true,
      categoryIds: ["3ffe0720fc9b"],
    });
    // Both refs are written in the text, so none trail.
    expect(findings[0].trailingRefs).toEqual([]);
  });

  it("falls back to key points when findings is empty, and is empty for a failed summary", () => {
    expect(normalizeFindings({ ...legacy, findings: [] })).toHaveLength(5);
    expect(normalizeFindings({ error: "unavailable" })).toEqual([]);
    expect(normalizeFindings(undefined)).toEqual([]);
  });

  it("drops a severity it does not know", () => {
    const [finding] = normalizeFindings({
      findings: [{ headline: "x", severity: "critical" }],
    });
    expect(finding.severity).toBeNull();
  });
});

describe("refsInText", () => {
  it("collects every cited number", () => {
    expect([...refsInText("a (#1, #2)", "b #7", undefined)]).toEqual([1, 2, 7]);
  });
});

describe("verificationNote", () => {
  it("counts findings and what was dropped", () => {
    expect(verificationNote(enriched, 5)).toEqual({
      checked: true,
      verifier: false,
      text: "5 findings checked against the report's statistics and the cited examples · 1 dropped",
    });
    expect(verificationNote(legacy, 5).text).toBe(
      "5 findings checked against the report's statistics",
    );
    expect(verificationNote({ ...enriched, verified: false }, 5).checked).toBe(
      false,
    );
  });

  it("names only the checks that ran", () => {
    // The built-in engine checks numbers against the statistics, not the
    // examples its findings cite.
    const builtIn = {
      ...enriched,
      findings: enriched.findings.map((f) => ({ ...f, evidence: [] })),
      verification: { numbers: true, quotes: false, audit: false },
    };
    expect(verificationNote(builtIn, 5)).toEqual({
      checked: true,
      verifier: false,
      text: "5 findings checked against the report's statistics · 1 dropped",
    });
    // What the summary says ran wins over whether quotes are shown.
    expect(
      verificationNote(
        { ...enriched, verification: { numbers: true, quotes: false } },
        5,
      ).text,
    ).toBe("5 findings checked against the report's statistics · 1 dropped");
    expect(
      verificationNote(
        { ...builtIn, verification: { numbers: true, quotes: true } },
        5,
      ).text,
    ).toBe(
      "5 findings checked against the report's statistics and the cited examples · 1 dropped",
    );
    // A run from before the summary said so: no quotes, no examples checked.
    const { verification: _ignored, ...older } = builtIn;
    expect(verificationNote(older, 5).text).toBe(
      "5 findings checked against the report's statistics · 1 dropped",
    );
  });
});

describe("real Omega summaries", () => {
  it("reads every finding with its quotes", () => {
    const findings = normalizeFindings(omega);
    expect(findings).toHaveLength(6);
    expect(findings[0]).toMatchObject({
      severity: "high",
      legacy: false,
      categoryIds: ["b98de668aa5f"],
    });
    expect(findings[0].refs.map((r) => r.ref)).toEqual([1, 2, 3]);
    expect(findings[0].evidence[0].quote).toBe(
      "Apologies for the confusion! Could you please provide your order number?",
    );
  });

  it("says when an independent verifier passed the findings", () => {
    expect(verificationNote(omega, 6)).toEqual({
      checked: true,
      verifier: true,
      text: "6 findings checked by an independent verifier against the statistics and cited examples",
    });
  });

  it("flags a verifier that did not pass, and failed investigations", () => {
    const failed = verificationNote(
      { ...omega, verification: { model_verifier: "failed" } },
      6,
    );
    expect(failed.checked).toBe(false);
    expect(
      verificationNote(
        {
          ...omega,
          verification: {
            model_verifier: "passed",
            investigations_failed: 2,
            request_evidence_discarded: 1,
          },
        },
        6,
      ).text,
    ).toMatch(/· 1 dropped · 2 could not be investigated$/);
  });
});

describe("highlightTitle", () => {
  it("uses the title, else the summary's first sentence, cut at a word", () => {
    expect(highlightTitle({ title: "Short title" })).toBe("Short title");
    expect(
      highlightTitle({
        summary:
          "The customer repeatedly asked to speak to a human and rejected the bot's replies, then gave up without getting help. More.",
      }),
    ).toBe(
      "The customer repeatedly asked to speak to a human and rejected the bot's replies, then…",
    );
    expect(highlightTitle({ title: "x".repeat(120) })).toHaveLength(90);
  });
});

describe("citationBudget", () => {
  it("keeps the first four citations in reading order and folds the rest", () => {
    const finding = {
      headline: "Policy fails (#12, #13)",
      detail: "Refunds after threats (#14, #15, #16) and codes #17.",
      trailingRefs: [18],
      action: "Enforce it (#12).",
    };
    expect(citationBudget(finding)).toEqual({
      hidden: new Set([16, 17, 18]),
      more: [16, 17, 18],
      at: "detail",
    });
  });

  it("folds trailing refs after the text and ignores unknown numbers", () => {
    const finding = {
      headline: "Order #12345 lost (#1)",
      detail: "Seen in #2 and #3.",
      trailingRefs: [4, 5],
      action: "",
    };
    const known = (n) => n < 100;
    expect(citationBudget(finding, known)).toEqual({
      hidden: new Set([5]),
      more: [5],
      at: "trailing",
    });
  });

  it("hides nothing at four or fewer", () => {
    expect(
      citationBudget({ headline: "#1 #2", detail: "#3", trailingRefs: [4] })
        .more,
    ).toEqual([]);
  });
});
