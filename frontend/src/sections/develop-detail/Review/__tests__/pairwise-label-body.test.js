import { describe, expect, it } from "vitest";

import { PAIRWISE_LABEL } from "../reviewModel";
import pairwiseLabelBody from "./pairwise-label.fixture.json";

// Pairwise… in Start review and Start here: Review the regressions create the
// "Overall preference" label by posting PAIRWISE_LABEL to
// /model-hub/annotations-labels/. The fixture is the body the label-create
// endpoint accepts: model_hub/tests/test_pairwise_label_body.py posts this
// same file through the real serializer and gets the label back.
describe("the pairwise label the review screens create", () => {
  it("is the body the label-create endpoint accepts", () => {
    expect(PAIRWISE_LABEL).toEqual(pairwiseLabelBody);
  });
});
