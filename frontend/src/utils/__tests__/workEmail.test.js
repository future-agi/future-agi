import { describe, it, expect } from "vitest";
import { isWorkEmail, NON_WORK_EMAIL_DOMAINS } from "../workEmail";

describe("isWorkEmail", () => {
  it("rejects every domain the backend gate rejects", () => {
    NON_WORK_EMAIL_DOMAINS.forEach((domain) => {
      expect(isWorkEmail(`someone@${domain}`)).toBe(false);
    });
  });

  it("accepts a company domain", () => {
    expect(isWorkEmail("dileep@futureagi.com")).toBe(true);
    expect(isWorkEmail("dileep@mail.futureagi.com")).toBe(true);
  });

  it("accepts a domain that only looks like a free provider", () => {
    // Subdomain and lookalike registrations are not on the list, so the
    // backend accepts them and the form must not diverge.
    expect(isWorkEmail("someone@gmail.company.com")).toBe(true);
    expect(isWorkEmail("someone@notgmail.com")).toBe(true);
  });

  it("normalises case and surrounding whitespace", () => {
    expect(isWorkEmail("  Someone@GMAIL.com  ")).toBe(false);
  });

  it("leaves malformed input to the format validator", () => {
    ["", "   ", "not-an-email", "@gmail.com", undefined, null].forEach(
      (value) => {
        expect(isWorkEmail(value)).toBe(true);
      },
    );
  });
});
