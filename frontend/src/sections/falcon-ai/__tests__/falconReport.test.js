import { describe, it, expect } from "vitest";
import {
  looksLikeReport,
  parseReport,
  TAG_CELL,
  toPrintHtml,
} from "../helpers/falconReport";

// One answer carrying every block the shape defines.
const REPORT = `# Evaluation Setup

Product Analytics Agent, seven evaluations

**Everything here runs on the traces you are already sending.** No SDK change.

\`\`\`stats
2.40M | SPANS INGESTED
0 | EVALS RUNNING
\`\`\`

## 01 - Set the row type to **Spans**

Choose Spans.

|  | Evaluation | What it scores |
|---|---|---|
| 1 | \`groundedness\` built in | Is the answer supported? |
| 2 | \`Numbers Match\` custom | Does every figure have a basis? |

> **2 · text_to_sql** scores the query the moment it is written.
> **7 · Right Event** names the event that was wrong.

---

## What your traces show today

> Read this before the meeting.

\`\`\`prompt
You are checking whether an analytics answer is numerically honest.
\`\`\`

_A dash means leave it blank._
`;

describe("parseReport", () => {
  const doc = parseReport(REPORT);

  it("takes its title from the opening heading", () => {
    expect(doc.title).toBe("Evaluation Setup");
  });

  it("reads the line under the title as the subtitle, not a paragraph", () => {
    expect(doc.pages[0].blocks[1]).toMatchObject({ type: "subtitle" });
  });

  it("reads the first bold-opening paragraph as the lede", () => {
    expect(doc.pages[0].blocks[2].type).toBe("lede");
  });

  it("splits a numbered heading into its step and its text, keeping the markup", () => {
    const step = doc.pages[0].blocks.find((b) => b.type === "step");
    expect(step.step).toBe("01");
    expect(step.html).toBe("Set the row type to <b>Spans</b>");
  });

  it("turns the stats fence into value and label pairs", () => {
    const stats = doc.pages[0].blocks.find((b) => b.type === "stats");
    expect(stats.stats).toEqual([
      { n: "2.40M", label: "SPANS INGESTED" },
      { n: "0", label: "EVALS RUNNING" },
    ]);
  });

  it("keeps an empty first header so the column can be numbered", () => {
    const table = doc.pages[0].blocks.find((b) => b.type === "table");
    expect(table.head[0]).toBe("");
    expect(table.rows).toHaveLength(2);
  });

  it("reads an all-bold-leading quote as findings and a plain one as a callout", () => {
    expect(doc.pages[0].blocks.find((b) => b.type === "solves").solves).toHaveLength(2);
    expect(doc.pages[1].blocks.find((b) => b.type === "note").html).toContain("meeting");
  });

  it("keeps a prompt fence separate from an ordinary code block", () => {
    const prompt = doc.pages[1].blocks.find((b) => b.type === "prompt");
    expect(prompt.value).toContain("numerically honest");
  });

  it("reads a closing italic line as the caveat", () => {
    const blocks = doc.pages[1].blocks;
    expect(blocks[blocks.length - 1].type).toBe("muted");
  });

  it("starts a new page on a rule", () => {
    expect(doc.pages).toHaveLength(2);
  });

  it("drops raw markup that came from the model instead of rendering it", () => {
    const out = toPrintHtml(parseReport("# T\n\n<img src=x onerror=alert(1)>"));
    expect(out).not.toContain("<img src=x");
    expect(out).not.toContain("onerror");
  });

  it("escapes angle brackets that arrived as text", () => {
    expect(toPrintHtml(parseReport("# T\n\nUse `a < b` in the filter."))).toContain("&lt;");
  });

  it("drops a link whose target is not http or in-app", () => {
    const scheme = `java${"script"}:`;
    const doc2 = parseReport(`# T\n\n[click](${scheme}alert(1))`);
    expect(toPrintHtml(doc2)).not.toContain(scheme);
    expect(toPrintHtml(doc2)).toContain("click");
  });
});

describe("looksLikeReport", () => {
  it("is true for an answer with a title and structure", () => {
    expect(looksLikeReport(REPORT)).toBe(true);
  });

  it("is false for a greeting, a bare paragraph and empty input", () => {
    expect(looksLikeReport("Hey! What would you like to do?")).toBe(false);
    expect(looksLikeReport("# Title alone with nothing under it")).toBe(false);
    expect(looksLikeReport("")).toBe(false);
  });
});

describe("TAG_CELL", () => {
  it("badges the kind at the end of a cell and leaves the name alone", () => {
    expect(TAG_CELL("<code>groundedness</code> built in")).toBe(
      '<code>groundedness</code><span class="tag t-b">built in</span>',
    );
    expect(TAG_CELL("100%")).toBe("100%");
  });
});

describe("toPrintHtml", () => {
  const out = toPrintHtml(parseReport(REPORT), { badge: "Research by Falcon AI" });

  it("repeats the header and numbers every page", () => {
    expect(out.match(/class="bar"/g)).toHaveLength(2);
    expect(out).toContain("Page 1 of 2");
    expect(out).toContain("Page 2 of 2");
  });

  it("carries the brand colour and the page rules the pack was printed with", () => {
    expect(out).toContain("#7857FC");
    expect(out).toContain("@page { size: A4;");
    expect(out).toContain("page-break-before: always");
  });

  it("drops the badge when there is none to show", () => {
    expect(toPrintHtml(parseReport(REPORT), { badge: "" })).not.toContain('class="by"');
  });
});
