import { describe, expect, it } from "vitest";
import * as checks from "./guardrailChecks";

const validate = (name, config) => checks.selectedConfigErrors(name, config);
const families = [
  "pii-detection",
  "content-moderation",
  "secret-detection",
  "language-detection",
  "topic-restriction",
  "input-validation",
];

describe("selected builtin config R3", () => {
  it("preserves the existing omitted keyword configuration error", () => {
    expect(validate("keyword-blocklist", undefined)).toEqual({
      words: "Enter words as a nonempty array of strings.",
    });
  });

  it.each(families)("offers the %s builtin in the policy catalog", (name) => {
    expect(checks.GUARDRAIL_CHECKS.some((check) => check.name === name)).toBe(
      true,
    );
  });

  it.each(families)("preserves omitted and empty %s defaults", (name) => {
    expect(validate(name, undefined)).toEqual({});
    expect(validate(name, {})).toEqual({});
  });

  it.each(families)("rejects explicit nonobject %s config", (name) => {
    for (const config of [null, false, 42, "private-marker", []]) {
      const errors = validate(name, config);
      expect(Object.keys(errors).length).toBeGreaterThan(0);
      expect(JSON.stringify(errors)).not.toContain("private-marker");
    }
  });

  const selectors = [
    [
      "pii-detection",
      "entities",
      [
        "email",
        "phone",
        "ssn",
        "credit_card",
        "ipv4",
        "ipv6",
        "dob",
        "passport",
        "drivers_license",
        "iban",
        "zip_code",
        "aws_key",
        "api_key",
        "url_credentials",
        "mac_address",
        "ein",
        "mrn",
        "bitcoin",
      ],
    ],
    [
      "content-moderation",
      "categories",
      ["toxicity", "hate_speech", "violence", "sexual", "self_harm"],
    ],
    [
      "secret-detection",
      "types",
      [
        "aws_access_key",
        "aws_secret_key",
        "github_token",
        "gitlab_token",
        "slack_token",
        "stripe_key",
        "openai_key",
        "private_key",
        "connection_string",
        "jwt",
        "password_assign",
      ],
    ],
  ];
  it.each(selectors)(
    "pins %s to the actual lower-case constructor catalog",
    (name, field, catalog) => {
      expect(validate(name, { [field]: catalog })).toEqual({});
      expect(validate(name, { [field]: [] })).toEqual({});
      for (const value of [
        null,
        catalog[0],
        [42],
        [catalog[0], "private-marker"],
        [catalog[0].toUpperCase()],
      ]) {
        const errors = validate(name, { [field]: value });
        expect(errors).toHaveProperty(field);
        expect(JSON.stringify(errors)).not.toContain("private-marker");
      }
    },
  );

  it("uses the language constructor's string-array contract, not a new script allowlist", () => {
    expect(
      validate("language-detection", {
        allowed_scripts: ["LATIN", "greek", "other", "custom"],
      }),
    ).toEqual({});
    expect(validate("language-detection", { allowed_scripts: [] })).toEqual({});
    for (const allowed_scripts of ["latin", null, [42], ["latin", false]]) {
      expect(
        validate("language-detection", { allowed_scripts }),
      ).toHaveProperty("allowed_scripts");
    }
  });

  it.each(["allowed_topics", "forbidden_topics"])(
    "validates every nested %s entry without skipping it",
    (field) => {
      for (const value of [
        null,
        "restricted",
        [42],
        [{}],
        [{ keywords: [] }],
        [{ name: 42, keywords: ["a"] }],
        [{ name: "restricted", keywords: "restricted" }],
        [{ keywords: ["a", 42] }],
      ]) {
        expect(
          validate("topic-restriction", { [field]: value }),
        ).toHaveProperty(field);
      }
      expect(validate("topic-restriction", { [field]: [] })).toEqual({});
      expect(
        validate("topic-restriction", {
          [field]: [{ keywords: ["a", " ", ""], extra: "keep" }],
        }),
      ).toEqual({});
    },
  );

  it.each(["deny_patterns", "require_patterns"])(
    "requires all %s entries to be strings",
    (field) => {
      for (const value of [null, "a", [42], ["a", 42]]) {
        expect(validate("input-validation", { [field]: value })).toHaveProperty(
          field,
        );
      }
      expect(validate("input-validation", { [field]: [] })).toEqual({});
    },
  );

  it.each([
    "[",
    "[abc",
    "[]",
    "[^]",
    "(",
    ")",
    "abc\\",
    "(?=a)",
    "(?!a)",
    "(?<=a)",
    "(?<!a)",
    String.raw`(a)\1`,
    String.raw`(?P<x>a)(?P=x)`,
    String.raw`(?P<x>a)\k<x>`,
    String.raw`(a)\g{1}`,
  ])("rejects bounded invalid Go pattern %s", (pattern) => {
    expect(
      validate("input-validation", { deny_patterns: ["valid", pattern] }),
    ).toHaveProperty("deny_patterns");
  });

  it.each([
    "",
    "[a-z]+",
    "(?:a|b)",
    "(?i)a",
    "(?U)a+",
    "(?P<word>a)",
    "(?<word>a)",
    String.raw`\p{Greek}+`,
    String.raw`\P{Greek}`,
    String.raw`\Q[(?=a)\1\E`,
    String.raw`\Q[`,
    String.raw`\[`,
    String.raw`\\1`,
    "[(?=)]",
    "[]a]",
    "[^]a]",
    "[[:alpha:]]",
    String.raw`\12`,
    String.raw`\123`,
    String.raw`\0`,
  ])("preserves Go-valid pattern %s without JS compilation", (pattern) => {
    expect(
      validate("input-validation", { require_patterns: [pattern] }),
    ).toEqual({});
  });

  it("documents bounded regex checking rather than falsely claiming RE2 equivalence", () => {
    // These are outside the deliberately bounded UI checker; Go compilation is authoritative.
    expect(
      validate("input-validation", {
        deny_patterns: ["a{1001}", String.raw`\p{NotAScript}`],
      }),
    ).toEqual({});
  });

  it.each(["min_message_length", "max_message_length", "max_total_length"])(
    "validates %s without changing intentional zero defaults",
    (field) => {
      for (const value of [-1, 1.5, "10", false, null, 2 ** 63]) {
        expect(validate("input-validation", { [field]: value })).toHaveProperty(
          field,
        );
      }
      expect(validate("input-validation", { [field]: 0 })).toEqual({});
      expect(validate("input-validation", { [field]: 10 })).toEqual({});
    },
  );

  it("rejects contradictory enabled length limits", () => {
    expect(
      Object.keys(
        validate("input-validation", {
          min_message_length: 5,
          max_message_length: 3,
        }),
      ).length,
    ).toBeGreaterThan(0);
    expect(
      Object.keys(
        validate("input-validation", {
          min_message_length: 5,
          max_total_length: 3,
        }),
      ).length,
    ).toBeGreaterThan(0);
    expect(
      validate("input-validation", {
        min_message_length: 5,
        max_message_length: 0,
      }),
    ).toEqual({});
  });

  it("does not mutate configs, delete unknown keys, or restrict custom factory names", () => {
    const config = { entities: [42], unknown: { nested: ["keep", 2] } };
    const before = JSON.stringify(config);
    validate("pii-detection", config);
    expect(JSON.stringify(config)).toBe(before);
    expect(validate("deployed-custom", config)).toEqual({});
    expect(validate("deployed-custom", null)).toEqual({});
  });
});
