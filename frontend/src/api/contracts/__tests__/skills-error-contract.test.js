import { describe, expect, it } from "vitest";

import { validateContractedResponse } from "../openapi-contract";

const checks = {
  items: [{ code: "name_missing", severity: "error", message: "Add a name." }],
  summary: { errors: 1, warnings: 0, infos: 0 },
  tokens: { metadata: 0, skill_md: 0, total: 0 },
  frontmatter: null,
};

const response = (url, detail) => ({
  status: 400,
  config: { url, method: "get" },
  data: { status: false, code: "invalid_test_config", detail },
});

describe.each(["/model-hub/skills/", "/model-hub/skills/testing-options/"])(
  "typed Skills errors for %s",
  (url) => {
    it.each([
      "Choose a workspace.",
      { revision: 7, updated_at: null },
      { release_revision: 2, current_version: null },
      { retry_at: "2026-11-08T02:00:00Z" },
      { reason: "archive_invalid" },
      checks,
    ])("accepts documented text or structured detail %#", (detail) => {
      expect(validateContractedResponse(response(url, detail))).toMatchObject({ ok: true });
    });

    it.each([{ revision: "not-an-integer" }, { release_revision: [] }, ["not-an-object"], { ...checks, summary: { ...checks.summary, errors: "wrong" } }, { undocumented: true }])(
      "rejects incorrectly typed error detail %#",
      (detail) => {
        expect(validateContractedResponse(response(url, detail))).toMatchObject({ ok: false });
      },
    );
  },
);
