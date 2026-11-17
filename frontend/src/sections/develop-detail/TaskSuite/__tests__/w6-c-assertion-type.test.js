// W6 C1: the check editor's "Plain-language assertion (judged)" type. One
// sentence per check, stored as {kind: "assertion", assert: {sentence}} —
// the shape sources/checksJudge.js judges through the one scorer.
import { describe, expect, it } from "vitest";
import {
  CHECK_TYPES,
  buildCheck,
  checkSentence,
  checkTypeMeta,
  checkTypeOf,
  draftFromCheck,
  validateDraft,
} from "../checkTypes";

const draft = (patch = {}) => ({
  ...draftFromCheck(null, { defaults: { type: "assertion" } }),
  key: "no_refund_date",
  ...patch,
});

describe("W6 C1 assertion check type", () => {
  it("is offered with its chip and label", () => {
    expect(CHECK_TYPES.map((t) => t.id)).toContain("assertion");
    expect(checkTypeMeta("assertion")).toMatchObject({
      chip: "Assertion",
      label: "Plain-language assertion (judged)",
    });
  });

  it("needs a sentence", () => {
    expect(validateDraft(draft({ sentence: "  " }))).toMatchObject({
      valid: false,
      field: "sentence",
    });
    expect(
      validateDraft(
        draft({ sentence: "The reply never promises a refund date." }),
      ).valid,
    ).toBe(true);
  });

  it("builds the judged shape and reads back the same draft", () => {
    const check = buildCheck(
      draft({ sentence: " The reply never promises a refund date. " }),
    );
    expect(check).toMatchObject({
      key: "no_refund_date",
      type: "judge",
      kind: "assertion",
      assert: { sentence: "The reply never promises a refund date." },
    });
    expect(checkTypeOf(check)).toBe("assertion");
    expect(checkTypeOf({ key: "x", assert: { sentence: "Says hello." } })).toBe(
      "assertion",
    );
    expect(checkSentence(check)).toBe(
      "“The reply never promises a refund date.” (judged)",
    );
    expect(draftFromCheck(check).sentence).toBe(
      "The reply never promises a refund date.",
    );
    expect(draftFromCheck(check).type).toBe("assertion");
  });

  it("leaves the other types as they were", () => {
    expect(checkTypeOf({ assert: { tool: "send_email" } })).toBe("tool_call");
    expect(checkTypeOf({ assert: { judge: "Tone" } })).toBe("rubric");
    expect(checkTypeOf({ assert: { path: "refunds", expected: 1 } })).toBe(
      "end_state",
    );
  });
});
