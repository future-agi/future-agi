import { expect, it } from "vitest";
import { TAG_PATTERN } from "../constants";
import { DETAIL_BOUNDARY_TAG } from "./detail.browser-fixtures";

it("uses a valid exact-40-character tag for the real-browser detail fixture", () => {
  expect(DETAIL_BOUNDARY_TAG).toHaveLength(40);
  expect(DETAIL_BOUNDARY_TAG).toMatch(TAG_PATTERN);
  expect(`${DETAIL_BOUNDARY_TAG}b`).not.toMatch(TAG_PATTERN);
});
