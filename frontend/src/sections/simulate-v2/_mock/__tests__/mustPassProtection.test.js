import { describe, expect, it } from "vitest";
import { protectionOf, protectionTally } from "../mustPassProtection";

const latest = (status) => ({ name: "Run 2", task: { status } });

describe("must-pass protection", () => {
  it("a must-pass row that passes is protected", () => {
    expect(protectionOf({ critical: true }, latest("passed")).id).toBe(
      "marked",
    );
  });
  it("a must-pass row failing or flaky now is not protected", () => {
    expect(protectionOf({ critical: true }, latest("failed")).id).toBe(
      "markedFailing",
    );
    expect(protectionOf({ critical: true }, latest("flaky")).label).toBe(
      "Must pass · failing now",
    );
  });
  it("tallies failing must-pass rows apart from the protected ones", () => {
    const rows = [
      { id: "a", critical: true },
      { id: "b", critical: true },
      { id: "c" },
    ];
    const t = protectionTally(rows, (r) =>
      r.id === "b" ? latest("failed") : latest("passed"),
    );
    expect(t).toMatchObject({
      marked: 1,
      markedFailing: 1,
      passing: 1,
      total: 3,
    });
  });
});
