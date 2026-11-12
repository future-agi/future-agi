// W7 B2/B3 (W7-SCOPE §2.6): the editable copy of a reference-kind built-in
// is made once; a second Make a copy (or Use as this eval's judge) reuses
// the copy found by its name instead of tripping "already exists".
import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => post(...args) },
  endpoints: {
    develop: {
      eval: { getEvalNames: "/names", createEvalTemplateV2: "/create-v2" },
    },
  },
}));

import { editableCopyOf } from "../alignmentApi";
import { COPY_INSTRUCTIONS } from "../agreementModel";

describe("editableCopyOf", () => {
  beforeEach(() => post.mockReset());

  it("reuses the copy already made (exact name), with no create call", async () => {
    post.mockResolvedValueOnce({
      data: {
        result: [
          { id: "other", name: "correctness_vs_reference_aligned_2" },
          { id: "copy-1", name: "correctness_vs_reference_aligned" },
        ],
      },
    });
    const copy = await editableCopyOf({
      templateName: "correctness_vs_reference",
      outputType: "pass_fail",
      model: "gpt-4o-mini",
    });
    expect(copy).toEqual({
      id: "copy-1",
      name: "correctness_vs_reference_aligned",
      existed: true,
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/names", {
      search_text: "correctness_vs_reference_aligned",
    });
  });

  it("creates the copy through create-v2 when none exists", async () => {
    post.mockResolvedValueOnce({ data: { result: [] } }).mockResolvedValueOnce({
      data: { result: { id: "new-1", name: "tone_aligned" } },
    });
    const copy = await editableCopyOf({
      templateName: "tone",
      outputType: "pass_fail",
      model: "ft:gpt-4.1-mini:acme:refund-judge",
    });
    expect(copy).toEqual({ id: "new-1", name: "tone_aligned", existed: false });
    expect(post).toHaveBeenLastCalledWith("/create-v2", {
      name: "tone_aligned",
      eval_type: "llm",
      instructions: COPY_INSTRUCTIONS,
      output_type: "pass_fail",
      model: "ft:gpt-4.1-mini:acme:refund-judge",
    });
  });
});
