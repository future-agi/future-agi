import { describe, expect, it } from "vitest";
import { mergeDesignFiles } from "../designSources";
import { readFile } from "../orgData";

const md = (text, name = "README.md") => ({ name, size: text.length, text });

describe("mergeDesignFiles — sources add up, same name replaces, copies skip", () => {
  it("adds a different file next to the others", () => {
    const one = mergeDesignFiles({}, [md("# A\nNever refund an order twice, ever.")]);
    const two = mergeDesignFiles({ sources: one.sources }, [{ name: "orders.csv", size: 4000 }]);
    expect(two.sources.map((s) => s.name)).toEqual(["README.md", "orders.csv"]);
    expect(two.added).toEqual(["orders.csv"]);
  });

  it("replaces the earlier copy when the same name comes with new contents", () => {
    const one = mergeDesignFiles({}, [md("# A\nNever refund an order twice, ever.")]);
    const two = mergeDesignFiles({ sources: one.sources }, [md("# A\nRefunds over $200 must be escalated to a human.")]);
    expect(two.sources).toHaveLength(1);
    expect(two.replaced).toEqual(["README.md"]);
    expect(two.sources[0].version).toBe(2);
    expect(two.sources[0].id).toBe(one.sources[0].id);
  });

  it("skips an identical copy", () => {
    const one = mergeDesignFiles({}, [md("# A\nNever refund an order twice, ever.")]);
    const two = mergeDesignFiles({ sources: one.sources }, [md("# A\nNever refund an order twice, ever.")]);
    expect(two.sources).toHaveLength(1);
    expect(two.skipped).toEqual(["README.md"]);
    expect(two.fresh).toHaveLength(0);
  });
});

describe("readFile — markup and labels are never rules", () => {
  it("ignores HTML, table rows and short labels", () => {
    const s = readFile(
      md(
        "# Router\n<td>HTTP router dispatch <sub>static route — ours only</sub></td>\n| Audit log | append-only |\n- Audit log (append-only)\n- Audit chain (append-only, tamper-evident)\n",
      ),
    );
    expect(s.parsed.passages.some((p) => p.rule)).toBe(false);
    expect(s.role).toBe("reference");
  });
});
