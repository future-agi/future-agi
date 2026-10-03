import { describe, it, expect } from "vitest";
import { isDocumentWebAddress } from "../editHelper";

describe("isDocumentWebAddress", () => {
  it("accepts http(s) addresses", () => {
    expect(isDocumentWebAddress("https://example.com/report.pdf")).toBe(true);
    expect(isDocumentWebAddress("http://example.com/a.docx")).toBe(true);
  });

  it("refuses values that are not web addresses", () => {
    expect(isDocumentWebAddress("sssss")).toBe(false);
    expect(isDocumentWebAddress("not a url")).toBe(false);
    expect(isDocumentWebAddress("ftp://example.com/a.pdf")).toBe(false);
    expect(isDocumentWebAddress("javascript:alert(1)")).toBe(false);
    expect(isDocumentWebAddress("https://")).toBe(false);
    expect(isDocumentWebAddress("")).toBe(false);
    expect(isDocumentWebAddress(null)).toBe(false);
  });
});
