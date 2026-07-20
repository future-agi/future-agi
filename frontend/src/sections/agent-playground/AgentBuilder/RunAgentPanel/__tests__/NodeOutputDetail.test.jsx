import { describe, it, expect } from "vitest";

const IMAGE_URL_RE = /^https?:\/\/.+\.(png|jpe?g|webp|gif)(\?.*)?$/i;

describe("IMAGE_URL_RE (NodeOutputDetail)", () => {
  it("matches PNG URLs", () => {
    expect(IMAGE_URL_RE.test("https://example.com/image.png")).toBe(true);
  });

  it("matches JPEG URLs", () => {
    expect(IMAGE_URL_RE.test("https://example.com/photo.jpg")).toBe(true);
    expect(IMAGE_URL_RE.test("https://example.com/photo.jpeg")).toBe(true);
  });

  it("matches WebP URLs", () => {
    expect(IMAGE_URL_RE.test("https://example.com/image.webp")).toBe(true);
  });

  it("matches GIF URLs", () => {
    expect(IMAGE_URL_RE.test("https://example.com/anim.gif")).toBe(true);
  });

  it("matches URLs with query strings", () => {
    expect(
      IMAGE_URL_RE.test("https://cdn.example.com/img.png?v=123&w=800"),
    ).toBe(true);
  });

  it("is case-insensitive", () => {
    expect(IMAGE_URL_RE.test("https://example.com/IMAGE.PNG")).toBe(true);
    expect(IMAGE_URL_RE.test("https://example.com/photo.JPEG")).toBe(true);
  });

  it("does not match plain text", () => {
    expect(IMAGE_URL_RE.test("hello world")).toBe(false);
  });

  it("does not match non-image URLs", () => {
    expect(IMAGE_URL_RE.test("https://example.com/file.pdf")).toBe(false);
    expect(IMAGE_URL_RE.test("https://example.com/data.json")).toBe(false);
  });

  it("does not match http-less strings", () => {
    expect(IMAGE_URL_RE.test("/local/path/image.png")).toBe(false);
  });
});
