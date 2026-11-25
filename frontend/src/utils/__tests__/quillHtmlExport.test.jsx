import { afterEach, beforeAll, describe, expect, it } from "vitest";
import UpstreamQuill from "quill";
import Quill from "../quillHtmlExport";

const FORMULA = '</span><img src="x" onerror="window.quillProbe=1">';
const VIDEO = 'https://example.test/" onmouseover="window.quillProbe=1';
const hosts = [];

beforeAll(() => {
  // jsdom provides selection but not layout; no Quill/clipboard code is mocked.
  const rect = () => ({
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    width: 0,
    height: 0,
  });
  Range.prototype.getBoundingClientRect ??= rect;
  Range.prototype.getClientRects ??= () => [];
});

afterEach(() => {
  hosts.splice(0).forEach((host) => host.remove());
});

function editor(formats = ["formula", "video", "bold"]) {
  const host = document.createElement("div");
  document.body.append(host);
  hosts.push(host);
  return new Quill(host, { theme: null, modules: { toolbar: false }, formats });
}

function formula(quill, value) {
  // Load an existing formula node built through the DOM API. Serialization
  // does not need a KaTeX renderer, and none is substituted.
  const span = document.createElement("span");
  span.className = "ql-formula";
  span.setAttribute("data-value", value);
  span.textContent = value;
  const paragraph = document.createElement("p");
  paragraph.append(span);
  quill.root.replaceChildren(paragraph);
  quill.update("user");
}

function video(quill, value) {
  quill.setContents([{ insert: { video: value } }, { insert: "\n" }]);
}

function parsed(html) {
  const body = new DOMParser().parseFromString(html, "text/html").body;
  expect(body.querySelector("script, img, iframe")).toBeNull();
  for (const element of body.querySelectorAll("*")) {
    expect(
      [...element.attributes].some((attribute) => /^on/i.test(attribute.name)),
    ).toBe(false);
  }
  return body;
}

function clipboard(quill, type) {
  quill.setSelection(0, 1, "silent");
  const values = {};
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clipboardData", {
    value: {
      setData: (mime, value) => {
        values[mime] = value;
      },
    },
  });
  quill.root.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  expect(typeof values["text/html"]).toBe("string");
  return values["text/html"];
}

describe("Quill formula/video HTML export", () => {
  it("exports the same Quill class the editors already import", () => {
    expect(Quill).toBe(UpstreamQuill);
  });

  it.each([
    FORMULA,
    '<svg onload="window.quillProbe=2">',
    "x < y & y > z; \"double\" and 'single'",
    "literal &amp; &lt; &#39; stays literal",
    "E = mc^2 + \\frac{a}{b}",
  ])("preserves formula data as text through direct export: %s", (value) => {
    const quill = editor();
    formula(quill, value);
    const before = quill.getContents();
    expect(before.ops.some((op) => op.insert?.formula === value)).toBe(true);
    expect(parsed(quill.getSemanticHTML()).textContent).toBe(value);
    expect(quill.getContents()).toEqual(before);
  });

  it.each([
    VIDEO,
    'https://example.test/</a><img src=x onerror="window.quillProbe=2">',
    "https://example.test/watch?v=one&quality=hd",
    "https://example.test/?literal=&amp;&quoted=&#39;",
    "https://example.test/\"double\"/'single'/<angle>",
  ])("preserves video data in one link through direct export: %s", (value) => {
    const quill = editor();
    video(quill, value);
    const before = quill.getContents();
    const body = parsed(quill.getSemanticHTML());
    expect(body.querySelectorAll("a")).toHaveLength(1);
    expect(body.querySelector("a").getAttribute("href")).toBe(value);
    expect(body.querySelector("a").textContent).toBe(value);
    expect(quill.getContents()).toEqual(before);
  });

  for (const type of ["copy", "cut"]) {
    it.each(["formula", "video"])(
      `escapes %s in actual ${type} event HTML`,
      (kind) => {
        const quill = editor();
        const value = kind === "formula" ? FORMULA : VIDEO;
        if (kind === "formula") formula(quill, value);
        else video(quill, value);
        const before = quill.getContents();
        const body = parsed(clipboard(quill, type));
        expect(body.textContent).toBe(value);
        if (type === "copy") expect(quill.getContents()).toEqual(before);
        else
          expect(quill.getContents().ops.some((op) => op.insert?.[kind])).toBe(
            false,
          );
      },
    );
  }

  it("preserves Quill's existing disallowed URL protocol policy", () => {
    const quill = editor();
    // eslint-disable-next-line no-script-url -- Quill must keep rejecting this protocol.
    video(quill, "javascript:window.quillProbe=1");
    expect(
      parsed(quill.getSemanticHTML()).querySelector("a").getAttribute("href"),
    ).toBe("about:blank");
  });

  it.each(["formula", "video"])(
    "does not widen restricted editor formats for %s",
    (kind) => {
      const quill = editor(["color", "background", "bold"]);
      expect(() =>
        quill.insertEmbed(0, kind, "https://example.test/owned"),
      ).toThrow(/Unable to create/);
      expect(quill.root.querySelector(".ql-formula,.ql-video")).toBeNull();
    },
  );
});
