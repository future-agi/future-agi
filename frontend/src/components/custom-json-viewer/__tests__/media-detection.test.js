import { describe, expect, it } from "vitest";
import { getMediaKind, getSafeMediaSrc } from "../media-detection";

// Built at runtime so the no-script-url lint rule doesn't flag the fixture.
const SCRIPT_URL = ["javascript", "alert(1)"].join(":");

describe("getSafeMediaSrc", () => {
  it.each([
    ["https://cdn.example.com/cat.png", "image"],
    ["http://cdn.example.com/call.mp3", "audio"],
    ["data:image/svg+xml;base64,PHN2Zy8+", "image"],
    ["DATA:IMAGE/PNG;base64,iVBORw0KGgo=", "image"],
    ["data:audio/wav;base64,UklGRg==", "audio"],
  ])("allows %s as %s", (value, kind) => {
    expect(getSafeMediaSrc(value, kind)).toBe(value);
  });

  it.each([
    [SCRIPT_URL, "image"],
    [` ${SCRIPT_URL}`, "image"],
    ["vbscript:msgbox(1)", "audio"],
    ["blob:https://example.com/7f1c", "audio"],
    ["file:///tmp/cat.png", "image"],
    ["/uploads/cat.png", "image"],
    ["//cdn.example.com/cat.png", "image"],
    ["data:text/html;base64,PHNjcmlwdD4=", "image"],
    ["data:audio/wav;base64,UklGRg==", "image"],
    ["data:image;base64,AAAA", "image"],
    ["https://", "image"],
    ["", "image"],
    [null, "image"],
    [42, "audio"],
  ])("rejects %s as %s", (value, kind) => {
    expect(getSafeMediaSrc(value, kind)).toBeNull();
  });
});

describe("getMediaKind", () => {
  it.each([
    ["data:image/png;base64,iVBORw0KGgo=", "image"],
    ["https://cdn.example.com/cat.PNG", "image"],
    ["https://cdn.example.com/cat.jpeg?w=200", "image"],
    ["data:audio/mpeg;base64,SUQz", "audio"],
    ["https://cdn.example.com/call.mp3", "audio"],
    ["HTTPS://CDN.EXAMPLE.COM/CALL.WAV?sig=abc", "audio"],
  ])("detects %s as %s", (value, kind) => {
    expect(getMediaKind(value)).toBe(kind);
  });

  it.each([
    "https://example.com/docs/page",
    "https://example.com/cat.png.html",
    "blob:https://example.com/call.mp3",
    "cat.png",
    "see photo.png for details",
    "data:image;base64,AAAA",
    `${SCRIPT_URL}//cat.png`,
    "",
  ])("leaves %s as text", (value) => {
    expect(getMediaKind(value)).toBeNull();
  });

  it.each([undefined, null, 42, { url: "https://cdn.example.com/cat.png" }])(
    "ignores the non-string value %s",
    (value) => {
      expect(getMediaKind(value)).toBeNull();
    },
  );
});
