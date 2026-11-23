import { describe, expect, it } from "vitest";
import { reelsFor } from "./omegaFindings";

const reel = (cluster_id, type) =>
  reelsFor({ cluster_id, error: { type } }, "hi", "done");
const text = (r) =>
  [...r.fail, ...r.pass]
    .map((s) => `${s.text} ${s.wentIn || ""} ${s.cameOut || ""}`)
    .join(" ");

describe("evidence steps read as the issue's own", () => {
  it("a clinic issue shows clinic steps, not a refund", () => {
    const t = text(reel("ISS-4557", "SkippedVerification"));
    expect(t).toMatch(/reschedule_appointment/);
    expect(t).not.toMatch(/refund/i);
  });

  it("the issue the type's story was written for keeps it", () => {
    expect(text(reel("ISS-4366", "SkippedVerification"))).toMatch(
      /issue_refund/,
    );
  });
});
